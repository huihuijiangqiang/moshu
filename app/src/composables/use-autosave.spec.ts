import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { contentApi } from '@/api/content'
import { useAutosave } from './use-autosave'

vi.mock('idb-keyval', () => ({ set: vi.fn().mockResolvedValue(undefined) }))

describe('useAutosave', () => {
  beforeEach(() => vi.useFakeTimers())
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
    const save = vi.spyOn(contentApi, 'saveChapter').mockResolvedValue(undefined)
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
    wrapper.unmount()
  })

  it('keeps a pending draft bound to the chapter where it was edited', async () => {
    const save = vi.spyOn(contentApi, 'saveChapter').mockResolvedValue(undefined)
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
})
