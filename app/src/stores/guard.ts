import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockApi } from '@/api/mock'
import type { GuardIssue, GuardKind } from '@/types'

export const useGuardStore = defineStore('guard', () => {
  const issues = ref<GuardIssue[]>([])
  const tab = ref<GuardKind | 'resolved'>('conflict')
  const scanning = ref(false)
  const loaded = ref(false)
  const loadedProjectId = ref<string | null>(null)

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

  async function load(projectId = 'p1') {
    if (loaded.value && loadedProjectId.value === projectId) return
    issues.value = await mockApi.listGuardIssues(projectId)
    loadedProjectId.value = projectId
    loaded.value = true
  }

  async function rescan() {
    scanning.value = true
    try {
      issues.value = await mockApi.listGuardIssues(loadedProjectId.value ?? 'p1')
    } finally {
      scanning.value = false
    }
  }

  async function resolve(id: string) {
    await mockApi.resolveGuardIssue(id)
    const i = issues.value.find((x) => x.id === id)
    if (i) i.resolved = true
  }

  return { issues, tab, scanning, open, counts, visible, topThree, load, rescan, resolve, loadedProjectId }
})
