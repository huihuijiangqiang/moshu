import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { set as idbSet } from 'idb-keyval'
import { mockApi } from '@/api/mock'

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'offline'

/**
 * 三层保险：本地 IndexedDB 立即写 → 节流上传服务端 → 服务端保留版本快照。
 * 丢稿是不可原谅的，所以这里永不静默失败。
 */
export function useAutosave(chapterId: Ref<string | null>, html: Ref<string>, throttleMs = 3000) {
  const state = ref<SaveState>('idle')
  const savedAt = ref<Date | null>(null)
  const online = ref(navigator.onLine)
  let timer: number | undefined

  const onOnline = () => { online.value = true; void flush() }
  const onOffline = () => { online.value = false; state.value = 'offline' }
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)

  async function flush() {
    const id = chapterId.value
    if (!id || state.value === 'saving') return
    if (!online.value) { state.value = 'offline'; return }
    state.value = 'saving'
    try {
      await mockApi.saveChapter(id, { content: html.value })
      savedAt.value = new Date()
      state.value = 'saved'
    } catch {
      state.value = 'offline'
    }
  }

  watch(html, async (next) => {
    const id = chapterId.value
    if (!id) return
    state.value = 'dirty'
    // 本地先落盘，不等网络
    await idbSet(`draft:${id}`, { html: next, at: Date.now() })
    window.clearTimeout(timer)
    timer = window.setTimeout(() => void flush(), throttleMs)
  })

  onBeforeUnmount(() => {
    window.clearTimeout(timer)
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
    void flush()
  })

  return { state, savedAt, online, flush }
}
