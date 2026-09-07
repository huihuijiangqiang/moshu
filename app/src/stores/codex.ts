import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { contentApi } from '@/api/content'
import type { CodexEntry, CodexEntryDraft, CodexKind, CodexRelationDraft } from '@/types'

function flattenSearchValue(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(flattenSearchValue)
  if (value && typeof value === 'object') return Object.values(value).flatMap(flattenSearchValue)
  return []
}

/** 设定库与编辑器引用共用同一份全文检索文本，避免详情能看见却搜不到。 */
export function codexEntrySearchText(entry: CodexEntry): string {
  return [
    entry.name,
    ...entry.aliases,
    entry.summary,
    ...flattenSearchValue(entry.character),
    ...flattenSearchValue(entry.facts),
    ...flattenSearchValue(entry.relations)
  ].join(' ').toLowerCase()
}

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
      return codexEntrySearchText(e).includes(q)
    })
  })

  /** 供编辑器 @ 引用：跨类型搜索，人物约束与关系信息同样可命中。 */
  function search(q: string, limit = 8): CodexEntry[] {
    const s = q.trim().toLowerCase()
    const pool = entries.value.filter((e) => e.status === 'confirmed')
    if (!s) return pool.slice(0, limit)
    return pool
      .filter((e) => codexEntrySearchText(e).includes(s))
      .slice(0, limit)
  }

  async function load(projectId = 'p1', force = false) {
    if (!force && loaded.value && loadedProjectId.value === projectId) return
    entries.value = await contentApi.listCodex(projectId)
    loadedProjectId.value = projectId
    loaded.value = true
  }

  async function confirm(id: string) {
    await contentApi.confirmCodexEntry(id)
    const e = byId.value.get(id)
    if (e) e.status = 'confirmed'
  }

  async function create(projectId: string, draft: CodexEntryDraft) {
    const entry = await contentApi.createCodexEntry(projectId, draft)
    entries.value.push(entry)
    return entry
  }

  async function update(id: string, draft: CodexEntryDraft) {
    const current = byId.value.get(id)
    if (!current) throw new Error('codex_entry_not_loaded')
    const entry = await contentApi.updateCodexEntry(id, draft, current)
    const index = entries.value.findIndex((item) => item.id === id)
    if (index >= 0) entries.value[index] = entry
    return entry
  }

  async function drop(id: string) {
    await contentApi.dropCodexEntry(id)
    entries.value = entries.value.filter((e) => e.id !== id)
  }

  async function createRelation(entryId: string, draft: CodexRelationDraft) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('codex_entry_not_loaded')
    await contentApi.createCodexRelation(projectId, entryId, draft)
    await load(projectId, true)
  }

  async function updateRelation(entryId: string, relationId: string, draft: CodexRelationDraft) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('codex_entry_not_loaded')
    await contentApi.updateCodexRelation(projectId, entryId, relationId, draft)
    await load(projectId, true)
  }

  async function deleteRelation(entryId: string, relationId: string) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('codex_entry_not_loaded')
    await contentApi.deleteCodexRelation(projectId, entryId, relationId)
    await load(projectId, true)
  }

  return { entries, kind, query, byId, resident, pending, counts, visible, search, load, create, update, confirm, drop, createRelation, updateRelation, deleteRelation, loadedProjectId }
})
