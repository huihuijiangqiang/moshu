import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { set as idbSet } from 'idb-keyval'
import { contentApi } from '@/api/content'

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'offline' | 'error'

interface PendingDraft {
  chapterId: string
  html: string
}

/**
 * 三层保险：本地 IndexedDB 立即写 → 节流上传服务端 → 服务端保留版本快照。
 * 丢稿是不可原谅的，所以这里永不静默失败。
 */
export function useAutosave(chapterId: Ref<string | null>, html: Ref<string>, throttleMs = 3000) {
  const state = ref<SaveState>('idle')
  const savedAt = ref<Date | null>(null)
  const online = ref(navigator.onLine)
  let timer: number | undefined
  let pending: PendingDraft | null = null
  let saving = false
  const cleanByChapter = new Map<string, string>()

  const onOnline = () => { online.value = true; if (pending) void flush() }
  const onOffline = () => { online.value = false; state.value = 'offline' }
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)

  async function flush() {
    if (saving || !pending) return
    if (!online.value) { state.value = 'offline'; return }
    const draft = pending
    if (cleanByChapter.get(draft.chapterId) === draft.html) {
      pending = null
      state.value = 'idle'
      return
    }

    pending = null
    saving = true
    state.value = 'saving'
    try {
      await contentApi.saveChapter(draft.chapterId, { content: draft.html })
      cleanByChapter.set(draft.chapterId, draft.html)
      savedAt.value = new Date()
      state.value = 'saved'
    } catch {
      pending ??= draft
      state.value = online.value ? 'error' : 'offline'
    } finally {
      saving = false
      if (pending && state.value !== 'error' && state.value !== 'offline') {
        state.value = 'dirty'
        schedule()
      }
    }
  }

  function schedule() {
    window.clearTimeout(timer)
    timer = window.setTimeout(() => void flush(), throttleMs)
  }

  function markClean(id: string, content: string) {
    cleanByChapter.set(id, content)
    if (pending?.chapterId === id && pending.html === content) pending = null
    if (!pending && !saving) state.value = 'idle'
  }

  watch(html, async (next) => {
    const id = chapterId.value
    if (!id) return
    if (cleanByChapter.get(id) === next) return
    pending = { chapterId: id, html: next }
    state.value = 'dirty'
    // 本地先落盘，不等网络
    await idbSet(`draft:${id}`, { html: next, at: Date.now() })
    if (!saving) schedule()
  })

  onBeforeUnmount(() => {
    window.clearTimeout(timer)
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
    void flush()
  })

  return { state, savedAt, online, flush, markClean }
}
