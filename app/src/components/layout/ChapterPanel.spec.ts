import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import ChapterPanel from './ChapterPanel.vue'
import { useProjectStore } from '@/stores/project'
import type { Chapter } from '@/types'

function makeChapter(index: number, volumeId: string): Chapter {
  return {
    id: `chapter-${index}`,
    volumeId,
    index,
    title: `第 ${index} 章的标题`,
    words: index * 10,
    status: index === 150 ? 'drafting' : 'outlined',
    outline: [],
    outlineNote: '',
    content: ''
  }
}

function mountPanel(chapterCount = 300, activeIndex = 150) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useProjectStore()
  store.project = {
    id: 'project-1',
    title: '长篇测试作品',
    dailyGoal: 2000,
    dailyWords: 0,
    styleProfile: null,
    volumes: [
      { id: 'volume-1', index: 1, title: '入局' },
      { id: 'volume-2', index: 2, title: '山河' },
      { id: 'volume-3', index: 3, title: '归途' }
    ]
  }
  store.chapters = Array.from({ length: chapterCount }, (_, offset) => {
    const index = offset + 1
    const volume = Math.min(3, Math.ceil(index / Math.max(1, chapterCount / 3)))
    return makeChapter(index, `volume-${volume}`)
  })
  store.activeId = `chapter-${activeIndex}`
  const wrapper = mount(ChapterPanel, { attachTo: document.body, global: { plugins: [pinia] } })
  return { wrapper, store }
}

describe('ChapterPanel', () => {
  beforeEach(() => { document.body.innerHTML = '' })

  it('keeps a 300 chapter project virtualized and reveals the active chapter', async () => {
    const { wrapper } = mountPanel()
    await flushPromises()

    expect(wrapper.findAll('[data-chapter-id]').length).toBeLessThan(40)
    expect(wrapper.get('[data-chapter-id="chapter-150"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('.chapter-viewport').element.scrollTop).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('keeps a visible chapter in the tab order after scrolling away from the active chapter', async () => {
    const { wrapper } = mountPanel()
    await flushPromises()
    const viewport = wrapper.get('.chapter-viewport')

    viewport.element.scrollTop = 0
    await viewport.trigger('scroll')
    await flushPromises()

    const rendered = wrapper.findAll('[data-chapter-id]')
    expect(rendered.some((row) => row.attributes('tabindex') === '0')).toBe(true)
    expect(wrapper.find('[data-chapter-id="chapter-150"]').exists()).toBe(false)
    expect(rendered[0]?.attributes('aria-setsize')).toBe('300')
    wrapper.unmount()
  })

  it('moves focus with navigation keys and opens a chapter only on activation', async () => {
    const { wrapper, store } = mountPanel(12, 3)
    await flushPromises()
    const current = wrapper.get('[data-chapter-id="chapter-3"]')

    await current.trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-chapter-id')).toBe('chapter-4')
    expect(store.activeId).toBe('chapter-3')

    await wrapper.get('[data-chapter-id="chapter-4"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(store.activeId).toBe('chapter-4')

    await wrapper.get('[data-chapter-id="chapter-4"]').trigger('keydown', { key: 'End' })
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-chapter-id')).toBe('chapter-12')
    wrapper.unmount()
  })

  it('resets the virtual viewport and keyboard target after filtering', async () => {
    const { wrapper } = mountPanel(300, 250)
    await flushPromises()
    const viewport = wrapper.get('.chapter-viewport')
    expect(viewport.element.scrollTop).toBeGreaterThan(0)

    await wrapper.get('input[aria-label="筛选章节"]').setValue('第 17 章')
    await flushPromises()

    expect(viewport.element.scrollTop).toBe(0)
    expect(wrapper.findAll('[data-chapter-id]')).toHaveLength(1)
    expect(wrapper.get('[data-chapter-id="chapter-17"]').text()).toContain('第 17 章的标题')
    wrapper.unmount()
  })
})
