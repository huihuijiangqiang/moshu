import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { del as idbDel, get as idbGet, set as idbSet } from 'idb-keyval'
import { BodyConflictError, contentApi } from '@/api/content'
import { useAutosave } from './use-autosave'

vi.mock('idb-keyval', () => ({
  del: vi.fn().mockResolvedValue(undefined),
  get: vi.fn().mockResolvedValue(undefined),
  set: vi.fn().mockResolvedValue(undefined)
}))

describe('useAutosave', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    vi.mocked(idbDel).mockResolvedValue(undefined)
    vi.mocked(idbGet).mockResolvedValue(undefined)
    vi.mocked(idbSet).mockResolvedValue(undefined)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  function setup() {
    const chapterId = ref<string | null>('ch-1')
    const html = ref('')
    let autosave!: ReturnType<typeof useAutosave>
    const wrapper = mount(defineComponent({
      setup() {
        autosave = useAutosave(chapterId, html, 10)
        return () => h('div')
      }
    }))
    return { chapterId, html, autosave, wrapper }
  }

  it('treats hydrated server content as clean and saves the first user edit', async () => {
    const save = vi.spyOn(contentApi, 'saveChapter').mockResolvedValue({ rev: 1 })
    const { html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>server</p>')
    html.value = '<p>server</p>'
    await nextTick()
    await vi.runAllTimersAsync()
    expect(save).not.toHaveBeenCalled()
    expect(autosave.state.value).toBe('idle')

    html.value = '<p>edited</p>'
    await nextTick()
    await vi.runAllTimersAsync()
    expect(save).toHaveBeenCalledWith('ch-1', { content: '<p>edited</p>' })
    expect(autosave.state.value).toBe('saved')
    expect(idbDel).toHaveBeenCalledWith('draft:ch-1')
    wrapper.unmount()
  })

  it('keeps a pending draft bound to the chapter where it was edited', async () => {
    const save = vi.spyOn(contentApi, 'saveChapter').mockResolvedValue({ rev: 1 })
    const { chapterId, html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>one</p>')
    html.value = '<p>one edited</p>'
    await nextTick()
    chapterId.value = 'ch-2'
    autosave.markClean('ch-2', '<p>two</p>')
    html.value = '<p>two</p>'
    await nextTick()
    await vi.runAllTimersAsync()

    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith('ch-1', { content: '<p>one edited</p>' })
    wrapper.unmount()
  })

  it('queues edits from multiple chapters instead of replacing the older draft', async () => {
    const save = vi.spyOn(contentApi, 'saveChapter').mockResolvedValue({ rev: 1 })
    const { chapterId, html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>one</p>')
    html.value = '<p>one edited</p>'
    await nextTick()
    chapterId.value = 'ch-2'
    autosave.markClean('ch-2', '<p>two</p>')
    html.value = '<p>two edited</p>'
    await nextTick()
    await vi.runAllTimersAsync()

    expect(save).toHaveBeenNthCalledWith(1, 'ch-1', { content: '<p>one edited</p>' })
    expect(save).toHaveBeenNthCalledWith(2, 'ch-2', { content: '<p>two edited</p>' })
    wrapper.unmount()
  })

  it('does not mark an unsynced draft clean when switching back to its chapter', async () => {
    const save = vi.spyOn(contentApi, 'saveChapter').mockResolvedValue({ rev: 1 })
    const { chapterId, html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>one server</p>')
    html.value = '<p>one local</p>'
    await nextTick()
    chapterId.value = 'ch-2'
    autosave.markClean('ch-2', '<p>two server</p>')
    html.value = '<p>two server</p>'
    await nextTick()

    chapterId.value = 'ch-1'
    await autosave.prepareChapter('ch-1', '<p>one local</p>')
    await vi.runAllTimersAsync()

    expect(save).toHaveBeenCalledWith('ch-1', { content: '<p>one local</p>' })
    expect(idbDel).toHaveBeenCalledWith('draft:ch-1')
    wrapper.unmount()
  })

  it('offers a newer local draft for explicit recovery', async () => {
    vi.mocked(idbGet).mockResolvedValue({ html: '<p>local</p>', at: 123 })
    vi.spyOn(contentApi, 'saveChapter').mockResolvedValue({ rev: 2 })
    const { autosave, wrapper } = setup()

    await autosave.prepareChapter('ch-1', '<p>server</p>')
    expect(autosave.recoveryDraft.value?.html).toBe('<p>local</p>')

    const restored = autosave.restoreLocalDraft()
    expect(restored?.chapterId).toBe('ch-1')
    expect(autosave.state.value).toBe('dirty')
    wrapper.unmount()
  })

  it('keeps a 409 draft local until the author chooses a version', async () => {
    const save = vi.spyOn(contentApi, 'saveChapter')
      .mockRejectedValueOnce(new BodyConflictError({
        chapterId: 'ch-1',
        serverContentHtml: '<p>server newer</p>',
        serverRev: 7,
        clientContentHtml: '<p>local edit</p>'
      }))
      .mockResolvedValueOnce({ rev: 8 })
    const { html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>old server</p>')
    html.value = '<p>local edit</p>'
    await nextTick()
    await vi.runAllTimersAsync()

    expect(autosave.state.value).toBe('conflict')
    expect(autosave.conflict.value?.serverRev).toBe(7)
    expect(idbSet).toHaveBeenCalled()

    await autosave.keepLocalVersion()
    expect(save).toHaveBeenCalledTimes(2)
    expect(autosave.state.value).toBe('saved')
    wrapper.unmount()
  })

  it('continues cloud autosave when IndexedDB writes fail', async () => {
    vi.mocked(idbSet).mockRejectedValueOnce(new Error('storage unavailable'))
    const save = vi.spyOn(contentApi, 'saveChapter').mockResolvedValue({ rev: 2 })
    const { html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>server</p>')
    html.value = '<p>edited</p>'
    await nextTick()
    await vi.runAllTimersAsync()

    expect(save).toHaveBeenCalledWith('ch-1', { content: '<p>edited</p>' })
    expect(autosave.state.value).toBe('saved')
    expect(autosave.storageWarning.value).toBe(true)
    wrapper.unmount()
  })

  it('keeps a successful cloud save successful when IndexedDB cleanup fails', async () => {
    vi.mocked(idbDel).mockRejectedValueOnce(new Error('storage unavailable'))
    vi.spyOn(contentApi, 'saveChapter').mockResolvedValue({ rev: 2 })
    const { html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>server</p>')
    html.value = '<p>edited</p>'
    await nextTick()
    await vi.runAllTimersAsync()

    expect(autosave.state.value).toBe('saved')
    expect(autosave.storageWarning.value).toBe(true)
    wrapper.unmount()
  })

  it('accepts the server version and clears the conflict and local draft', async () => {
    vi.spyOn(contentApi, 'saveChapter').mockRejectedValueOnce(new BodyConflictError({
      chapterId: 'ch-1',
      serverContentHtml: '<p>server newer</p>',
      serverRev: 7,
      clientContentHtml: '<p>local edit</p>'
    }))
    const { html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>old server</p>')
    html.value = '<p>local edit</p>'
    await nextTick()
    await vi.runAllTimersAsync()
    const accepted = await autosave.acceptServerVersion()

    expect(accepted).toEqual({ chapterId: 'ch-1', html: '<p>server newer</p>', rev: 7 })
    expect(autosave.conflict.value).toBeNull()
    expect(autosave.state.value).toBe('idle')
    expect(idbDel).toHaveBeenCalledWith('draft:ch-1')
    wrapper.unmount()
  })

  it('does not upload edits made during a conflict until the author chooses', async () => {
    const save = vi.spyOn(contentApi, 'saveChapter')
      .mockRejectedValueOnce(new BodyConflictError({
        chapterId: 'ch-1',
        serverContentHtml: '<p>server newer</p>',
        serverRev: 7,
        clientContentHtml: '<p>local edit</p>'
      }))
      .mockResolvedValueOnce({ rev: 8 })
    const { html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>old server</p>')
    html.value = '<p>local edit</p>'
    await nextTick()
    await vi.runAllTimersAsync()
    html.value = '<p>latest local edit</p>'
    await nextTick()
    await vi.runAllTimersAsync()

    expect(save).toHaveBeenCalledTimes(1)
    expect(autosave.state.value).toBe('conflict')
    expect(autosave.conflict.value?.clientContentHtml).toBe('<p>latest local edit</p>')

    await autosave.keepLocalVersion()
    expect(save).toHaveBeenNthCalledWith(2, 'ch-1', { content: '<p>latest local edit</p>' })
    wrapper.unmount()
  })

  it('drains drafts queued behind an active save when the workspace unmounts', async () => {
    let finishFirst!: (result: { rev: number }) => void
    const firstSave = new Promise<{ rev: number }>((resolve) => { finishFirst = resolve })
    const save = vi.spyOn(contentApi, 'saveChapter')
      .mockReturnValueOnce(firstSave)
      .mockResolvedValueOnce({ rev: 2 })
    const { chapterId, html, autosave, wrapper } = setup()

    autosave.markClean('ch-1', '<p>one</p>')
    html.value = '<p>one edited</p>'
    await nextTick()
    await vi.advanceTimersByTimeAsync(10)
    expect(save).toHaveBeenCalledTimes(1)

    chapterId.value = 'ch-2'
    autosave.markClean('ch-2', '<p>two</p>')
    html.value = '<p>two edited</p>'
    await nextTick()
    wrapper.unmount()
    finishFirst({ rev: 1 })
    await vi.runAllTimersAsync()

    expect(save).toHaveBeenNthCalledWith(2, 'ch-2', { content: '<p>two edited</p>' })
    expect(vi.getTimerCount()).toBe(0)
  })
})
