import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { contentApi } from '@/api/content'
import type { GuardIssue, GuardKind, GuardOverview, GuardResolutionAction, TimelineReflowResult } from '@/types'

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
  const loaded = ref(false)
  const loadedProjectId = ref<string | null>(null)
  const overview = ref<GuardOverview>({ ...EMPTY_OVERVIEW })
  let polling: Promise<void> | null = null
  let pollingProjectId: string | null = null
  let pollingGeneration = 0
  let timelineReflowGeneration = 0

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
    const [nextIssues, nextOverview] = await Promise.all([
      contentApi.listGuardIssues(projectId),
      contentApi.getGuardOverview(projectId)
    ])
    issues.value = nextIssues
    overview.value = nextOverview
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
      timelineReflowPending.value = false
      timelineReflowResult.value = null
      timelineReflowError.value = null
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

  async function resolve(id: string, action: GuardResolutionAction = 'defer') {
    const projectId = loadedProjectId.value
    const i = issues.value.find((item) => item.id === id)
    if (!projectId || !i) return
    await contentApi.resolveGuardIssue(projectId, id, i.issueRev ?? 1, action)
    if (i) i.resolved = true
  }

  return {
    issues, tab, scanning, scanRequestPending, timelineReflowPending,
    timelineReflowResult, timelineReflowError, overview, open, counts, visible,
    topThree, load, rescan, reflowTimeline, resolve, loadedProjectId
  }
})
