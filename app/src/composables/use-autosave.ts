import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { del as idbDel, get as idbGet, set as idbSet } from 'idb-keyval'
import { BodyConflictError, contentApi, type BodyConflict } from '@/api/content'

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'offline' | 'error' | 'conflict'

interface PendingDraft {
  chapterId: string
  html: string
}

export interface RecoveryDraft extends PendingDraft {
  at: number
}

interface StoredDraft {
  html: string
  at: number
}

/**
 * Three-layer protection: IndexedDB first, throttled server upload second,
 * immutable server revisions last. Every unsaved chapter keeps its own queue slot.
 */
export function useAutosave(chapterId: Ref<string | null>, html: Ref<string>, throttleMs = 3000) {
  const state = ref<SaveState>('idle')
  const savedAt = ref<Date | null>(null)
  const online = ref(navigator.onLine)
  const recoveryDraft = ref<RecoveryDraft | null>(null)
  const conflict = ref<BodyConflict | null>(null)
  const storageWarning = ref(false)
  let timer: number | undefined
  let saving = false
  let disposed = false
  let prepareSequence = 0
  const pendingByChapter = new Map<string, PendingDraft>()
  const cleanByChapter = new Map<string, string>()

  const onOnline = () => { online.value = true; if (pendingByChapter.size) void flush() }
  const onOffline = () => { online.value = false; state.value = 'offline' }
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)

  async function removeStoredDraft(id: string) {
    try {
      await idbDel(`draft:${id}`)
    } catch {
      storageWarning.value = true
    }
  }

  async function flush() {
    if (saving || pendingByChapter.size === 0) return
    if (!online.value) { state.value = 'offline'; return }
    if (conflict.value) { state.value = 'conflict'; return }
    const draft = pendingByChapter.values().next().value as PendingDraft
    if (cleanByChapter.get(draft.chapterId) === draft.html) {
      pendingByChapter.delete(draft.chapterId)
      await removeStoredDraft(draft.chapterId)
      if (pendingByChapter.size) schedule()
      else state.value = 'idle'
      return
    }

    saving = true
    state.value = 'saving'
    try {
      await contentApi.saveChapter(draft.chapterId, { content: draft.html })
      cleanByChapter.set(draft.chapterId, draft.html)
      const latest = pendingByChapter.get(draft.chapterId)
      if (latest?.html === draft.html) {
        pendingByChapter.delete(draft.chapterId)
        await removeStoredDraft(draft.chapterId)
      }
      savedAt.value = new Date()
      state.value = 'saved'
    } catch (error) {
      if (error instanceof BodyConflictError) {
        const latest = pendingByChapter.get(draft.chapterId)
        conflict.value = {
          ...error.conflict,
          clientContentHtml: latest?.html ?? error.conflict.clientContentHtml
        }
        state.value = 'conflict'
      } else {
        state.value = online.value ? 'error' : 'offline'
      }
    } finally {
      saving = false
      if (pendingByChapter.size && state.value !== 'error' && state.value !== 'offline' && state.value !== 'conflict') {
        state.value = 'dirty'
        schedule()
      }
    }
  }

  function schedule() {
    window.clearTimeout(timer)
    if (disposed) {
      void flush()
      return
    }
    timer = window.setTimeout(() => void flush(), throttleMs)
  }

  function markClean(id: string, content: string) {
    cleanByChapter.set(id, content)
    const pending = pendingByChapter.get(id)
    if (pending?.html === content) pendingByChapter.delete(id)
    if (!pendingByChapter.size && !saving) state.value = 'idle'
  }

  async function prepareChapter(id: string, serverHtml: string) {
    const sequence = ++prepareSequence
    recoveryDraft.value = null
    if (pendingByChapter.has(id)) return
    if (!cleanByChapter.has(id)) markClean(id, serverHtml)
    let stored: StoredDraft | undefined
    try {
      stored = await idbGet<StoredDraft>(`draft:${id}`)
    } catch {
      storageWarning.value = true
      return
    }
    if (sequence !== prepareSequence) return
    if (stored && typeof stored.html === 'string' && typeof stored.at === 'number' && stored.html !== serverHtml) {
      recoveryDraft.value = { chapterId: id, html: stored.html, at: stored.at }
      return
    }
    if (stored) await removeStoredDraft(id)
  }

  function restoreLocalDraft() {
    const draft = recoveryDraft.value
    if (!draft) return null
    recoveryDraft.value = null
    pendingByChapter.set(draft.chapterId, draft)
    state.value = 'dirty'
    schedule()
    return draft
  }

  async function discardLocalDraft() {
    const draft = recoveryDraft.value
    if (!draft) return
    recoveryDraft.value = null
    await removeStoredDraft(draft.chapterId)
  }

  async function acceptServerVersion() {
    const current = conflict.value
    if (!current) return null
    conflict.value = null
    pendingByChapter.delete(current.chapterId)
    cleanByChapter.set(current.chapterId, current.serverContentHtml)
    await removeStoredDraft(current.chapterId)
    state.value = pendingByChapter.size ? 'dirty' : 'idle'
    if (pendingByChapter.size) schedule()
    return { chapterId: current.chapterId, html: current.serverContentHtml, rev: current.serverRev }
  }

  async function keepLocalVersion() {
    const current = conflict.value
    if (!current) return
    conflict.value = null
    pendingByChapter.set(current.chapterId, {
      chapterId: current.chapterId,
      html: pendingByChapter.get(current.chapterId)?.html ?? current.clientContentHtml
    })
    state.value = 'dirty'
    await flush()
  }

  async function retry() {
    if (!pendingByChapter.size || saving) return
    state.value = 'dirty'
    await flush()
  }

  watch(html, async (next) => {
    const id = chapterId.value
    if (!id || cleanByChapter.get(id) === next) return
    const draft = { chapterId: id, html: next }
    pendingByChapter.set(id, draft)
    if (conflict.value?.chapterId === id) {
      conflict.value = { ...conflict.value, clientContentHtml: next }
    }
    state.value = conflict.value ? 'conflict' : online.value ? 'dirty' : 'offline'
    try {
      await idbSet(`draft:${id}`, { html: next, at: Date.now() })
    } catch {
      storageWarning.value = true
    } finally {
      if (!saving && online.value && !conflict.value) schedule()
    }
  })

  onBeforeUnmount(() => {
    disposed = true
    window.clearTimeout(timer)
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
    void flush()
  })

  return {
    state, savedAt, online, recoveryDraft, conflict, storageWarning, flush, retry, markClean,
    prepareChapter, restoreLocalDraft, discardLocalDraft, acceptServerVersion, keepLocalVersion
  }
}
