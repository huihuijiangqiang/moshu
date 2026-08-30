import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockApi } from '@/api/mock'
import type { CodexEntry, CodexKind } from '@/types'

export const useCodexStore = defineStore('codex', () => {
  const entries = ref<CodexEntry[]>([])
  const kind = ref<CodexKind>('character')
  const query = ref('')
  const loaded = ref(false)
  const loadedProjectId = ref<string | null>(null)

  const byId = computed(() => new Map(entries.value.map((e) => [e.id, e])))
  const resident = computed(() => entries.value.filter((e) => e.resident))
  const pending = computed(() => entries.value.filter((e) => e.status === 'pending'))

  const counts = computed(() => {
    const m = {} as Record<CodexKind, number>
    for (const e of entries.value) m[e.kind] = (m[e.kind] ?? 0) + 1
    return m
  })

  const visible = computed(() => {
    const q = query.value.trim().toLowerCase()
    return entries.value.filter((e) => {
      if (e.kind !== kind.value) return false
      if (!q) return true
      return e.name.toLowerCase().includes(q) || e.aliases.some((a) => a.toLowerCase().includes(q))
    })
  })

  /** 供编辑器 @ 引用：跨类型搜索，名称与别名都命中 */
  function search(q: string, limit = 8): CodexEntry[] {
    const s = q.trim().toLowerCase()
    const pool = entries.value.filter((e) => e.status === 'confirmed')
    if (!s) return pool.slice(0, limit)
    return pool
      .filter((e) => e.name.toLowerCase().includes(s) || e.aliases.some((a) => a.toLowerCase().includes(s)))
      .slice(0, limit)
  }

  async function load(projectId = 'p1') {
    if (loaded.value && loadedProjectId.value === projectId) return
    entries.value = await mockApi.listCodex(projectId)
    loadedProjectId.value = projectId
    loaded.value = true
  }

  async function confirm(id: string) {
    await mockApi.confirmCodexEntry(id)
    const e = byId.value.get(id)
    if (e) e.status = 'confirmed'
  }

  async function drop(id: string) {
    await mockApi.dropCodexEntry(id)
    entries.value = entries.value.filter((e) => e.id !== id)
  }

  return { entries, kind, query, byId, resident, pending, counts, visible, search, load, confirm, drop, loadedProjectId }
})
