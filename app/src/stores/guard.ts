import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { contentApi } from '@/api/content'
import { ApiError } from '@/api/http'
import type { GuardIssue, GuardKind, GuardOverview, GuardResolutionAction, TemporalReviewItem, TimelineReflowResult } from '@/types'

export interface GuardResolutionRecord {
  action: GuardResolutionAction
  label: string
  message: string
  followUp?: string
  recordedAt: string
}

const RESOLUTION_RECORDS: Record<GuardResolutionAction, Omit<GuardResolutionRecord, 'action' | 'recordedAt'>> = {
  accept_old_fact: {
    label: '已采用原设定',
    message: '后续正文应以已有设定为准。',
    followUp: '如正文仍未修改，请打开对应章节并重新扫描。'
  },
  accept_new_fact: {
    label: '已采用本章新事实',
    message: '处置决定已记录，设定库需要按本章事实核对。',
    followUp: '请在设定库确认条目描述和状态，再重新扫描。'
  },
  intentional_exception: {
    label: '已标记为有意例外',
    message: '这处差异按作者意图保留，不会自动改动正文或设定。'
  },
  false_positive: {
    label: '已标记为误报',
    message: '这条告警已从待处理列表移出，仅保留处置记录用于复盘。'
  },
  fixed_in_body: {
    label: '已标记正文修正',
    message: '处置决定已记录，正文修改需要由作者完成。',
    followUp: '修改正文并保存后重新扫描，确认这条告警不再出现。'
  },
  defer: {
    label: '已暂缓处理',
    message: '这条告警已记录为稍后处理。'
  }
}

const EMPTY_OVERVIEW: GuardOverview = {
  status: 'idle', queued: 0, running: 0, completed: 0, failed: 0,
  outboxPending: 0, outboxDeadLetter: 0, runs: []
}

export const useGuardStore = defineStore('guard', () => {
  const issues = ref<GuardIssue[]>([])
  const tab = ref<GuardKind | 'resolved'>('conflict')
  const scanning = ref(false)
  const scanRequestPending = ref(false)
  const timelineReflowPending = ref(false)
  const timelineReflowResult = ref<TimelineReflowResult | null>(null)
  const timelineReflowError = ref<string | null>(null)
  const temporalReviews = ref<TemporalReviewItem[]>([])
  const temporalDecisionPendingId = ref<number | null>(null)
  const temporalDecisionError = ref<string | null>(null)
  const loaded = ref(false)
  const loadedProjectId = ref<string | null>(null)
  const resolutions = ref<Record<string, GuardResolutionRecord>>({})
  const overview = ref<GuardOverview>({ ...EMPTY_OVERVIEW })
  let polling: Promise<void> | null = null
  let pollingProjectId: string | null = null
  let pollingGeneration = 0
  let timelineReflowGeneration = 0
  let temporalDecisionGeneration = 0

  const open = computed(() => issues.value.filter((i) => !i.resolved))
  const counts = computed(() => ({
    conflict: open.value.filter((i) => i.kind === 'conflict').length,
    foreshadow: open.value.filter((i) => i.kind === 'foreshadow').length,
    'pending-entry': open.value.filter((i) => i.kind === 'pending-entry').length,
    resolved: issues.value.filter((i) => i.resolved).length
  }))

  const visible = computed(() =>
    tab.value === 'resolved'
      ? issues.value.filter((i) => i.resolved)
      : open.value.filter((i) => i.kind === tab.value)
  )

  /** 侧栏提醒：只取最紧要的三条，不要把整份报告塞进写作界面 */
  const topThree = computed(() =>
    [...open.value].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === 'high' ? -1 : 1)).slice(0, 3)
  )

  function hasActiveWork(value: GuardOverview) {
    return value.outboxPending > 0 || value.queued > 0 || value.running > 0
  }

  async function refresh(projectId: string) {
    const [nextIssues, nextOverview, nextReviews] = await Promise.all([
      contentApi.listGuardIssues(projectId),
      contentApi.getGuardOverview(projectId),
      contentApi.listTemporalReviews(projectId)
    ])
    if (loadedProjectId.value !== projectId) return
    issues.value = nextIssues
    overview.value = nextOverview
    temporalReviews.value = nextReviews
    loadedProjectId.value = projectId
    loaded.value = true
  }

  function pollUntilSettled(projectId: string, firstDelay = 250) {
    if (polling && pollingProjectId === projectId) return polling
    if (polling) {
      polling = null
      pollingProjectId = null
    }
    const generation = ++pollingGeneration
    scanning.value = true
    const current = (async () => {
      let first = true
      while (true) {
        await new Promise((resolve) => setTimeout(resolve, first ? firstDelay : 2000))
        first = false
        if (generation !== pollingGeneration || loadedProjectId.value !== projectId) return
        await refresh(projectId)
        if (!hasActiveWork(overview.value)) return
      }
    })()
    polling = current
    pollingProjectId = projectId
    void current
      .finally(() => {
        if (polling === current) {
          polling = null
          pollingProjectId = null
          scanning.value = false
        }
      })
      .catch(() => undefined)
    return current
  }

  async function load(projectId = 'p1', force = false) {
    if (!force && loaded.value && loadedProjectId.value === projectId) {
      if (hasActiveWork(overview.value)) void pollUntilSettled(projectId).catch(() => undefined)
      return
    }
    if (loadedProjectId.value !== projectId) {
      ++timelineReflowGeneration
      ++temporalDecisionGeneration
      timelineReflowPending.value = false
      timelineReflowResult.value = null
      timelineReflowError.value = null
      temporalReviews.value = []
      temporalDecisionPendingId.value = null
      temporalDecisionError.value = null
      resolutions.value = {}
      // Mark the route target before I/O so a late request from the previous
      // project cannot refresh its data back into this shared store.
      loadedProjectId.value = projectId
    }
    await refresh(projectId)
    if (hasActiveWork(overview.value)) void pollUntilSettled(projectId).catch(() => undefined)
  }

  async function rescan() {
    const projectId = loadedProjectId.value
    if (!projectId || scanRequestPending.value) return
    scanRequestPending.value = true
    try {
      await contentApi.scanProject(projectId)
      await refresh(projectId)
      if (hasActiveWork(overview.value)) {
        void pollUntilSettled(projectId).catch(() => undefined)
      }
    } finally {
      scanRequestPending.value = false
    }
  }

  async function reflowTimeline() {
    const projectId = loadedProjectId.value
    if (!projectId || timelineReflowPending.value) return
    const generation = ++timelineReflowGeneration
    timelineReflowPending.value = true
    timelineReflowError.value = null
    try {
      const result = await contentApi.reflowProjectTimeline(projectId)
      if (generation !== timelineReflowGeneration || loadedProjectId.value !== projectId) return
      timelineReflowResult.value = result
      await refresh(projectId)
      if (hasActiveWork(overview.value)) {
        void pollUntilSettled(projectId).catch(() => undefined)
      }
    } catch {
      if (generation === timelineReflowGeneration && loadedProjectId.value === projectId) {
        timelineReflowError.value = '时间线重算失败，请稍后重试。'
      }
    } finally {
      if (generation === timelineReflowGeneration) timelineReflowPending.value = false
    }
  }

  async function decideTemporalReview(
    item: TemporalReviewItem,
    action: 'confirm' | 'clear',
    offsetSeconds?: number
  ) {
    const projectId = loadedProjectId.value
    if (!projectId || temporalDecisionPendingId.value !== null) return false
    const generation = ++temporalDecisionGeneration
    temporalDecisionPendingId.value = item.claimId
    temporalDecisionError.value = null
    try {
      const result = await contentApi.decideTemporalReview(
        projectId,
        item.claimId,
        action,
        item.overrideVersion,
        offsetSeconds
      )
      if (generation !== temporalDecisionGeneration || loadedProjectId.value !== projectId) return false
      const index = temporalReviews.value.findIndex((row) => row.claimId === item.claimId)
      if (index >= 0) temporalReviews.value[index] = result.item
      timelineReflowResult.value = result.reflow
      await refresh(projectId)
      if (hasActiveWork(overview.value)) void pollUntilSettled(projectId).catch(() => undefined)
      return true
    } catch (error) {
      if (generation !== temporalDecisionGeneration || loadedProjectId.value !== projectId) return false
      temporalDecisionError.value = error instanceof ApiError && error.status === 409
        ? '这条时间已被其他编辑修改，已重新载入最新值。'
        : '时间确认未保存，请检查取值后重试。'
      await refresh(projectId).catch(() => undefined)
      return false
    } finally {
      if (generation === temporalDecisionGeneration && loadedProjectId.value === projectId) {
        temporalDecisionPendingId.value = null
      }
    }
  }

  async function resolve(id: string, action: GuardResolutionAction = 'defer') {
    const projectId = loadedProjectId.value
    const i = issues.value.find((item) => item.id === id)
    if (!projectId || !i) return
    await contentApi.resolveGuardIssue(projectId, id, i.issueRev ?? 1, action)
    i.resolved = true
    resolutions.value[id] = {
      action,
      ...RESOLUTION_RECORDS[action],
      recordedAt: new Date().toISOString()
    }
  }

  return {
    issues, tab, scanning, scanRequestPending, timelineReflowPending,
    timelineReflowResult, timelineReflowError, temporalReviews,
    temporalDecisionPendingId, temporalDecisionError, overview, open, counts, visible, resolutions,
    topThree, load, rescan, reflowTimeline, decideTemporalReview, resolve, loadedProjectId
  }
})
