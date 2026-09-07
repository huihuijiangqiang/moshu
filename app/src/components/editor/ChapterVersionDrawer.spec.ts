import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { contentApi } from '@/api/content'
import ChapterVersionDrawer from './ChapterVersionDrawer.vue'

const summary = {
  id: 1,
  rev: 1,
  trigger: 'manual',
  words: 4,
  excerpt: '旧版正文',
  createdAt: '2026-09-03T08:00:00Z',
  isCurrent: false
}

describe('ChapterVersionDrawer', () => {
  afterEach(() => vi.restoreAllMocks())

  it('loads full content on selection and only renders it as text', async () => {
    vi.spyOn(contentApi, 'listChapterVersions').mockResolvedValue([summary])
    vi.spyOn(contentApi, 'getChapterVersion').mockResolvedValue({
      ...summary,
      content: '<p>旧版正文</p><img src="x" onerror="alert(1)">',
      contentJson: {}
    })

    const wrapper = mount(ChapterVersionDrawer, {
      props: { chapterId: 'ch-1', chapterTitle: '春种', currentContent: '<p>当前正文</p>' }
    })
    await flushPromises()

    expect(contentApi.listChapterVersions).toHaveBeenCalledWith('ch-1')
    expect(contentApi.getChapterVersion).toHaveBeenCalledWith('ch-1', 1)
    expect(wrapper.text()).toContain('旧版正文')
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('恢复这一版')
  })

  it('requires an explicit confirmation before emitting restore', async () => {
    vi.spyOn(contentApi, 'listChapterVersions').mockResolvedValue([summary])
    vi.spyOn(contentApi, 'getChapterVersion').mockResolvedValue({
      ...summary, content: '<p>旧版正文</p>', contentJson: {}
    })
    const wrapper = mount(ChapterVersionDrawer, {
      props: { chapterId: 'ch-1', chapterTitle: '春种', currentContent: '<p>当前正文</p>' }
    })
    await flushPromises()

    await wrapper.get('.version-actions > button').trigger('click')
    expect(wrapper.emitted('restore')).toBeUndefined()
    const confirm = wrapper.findAll('.version-actions button').find((button) => button.text().includes('确认恢复'))
    expect(confirm).toBeTruthy()
    await confirm!.trigger('click')
    expect(wrapper.emitted('restore')).toEqual([[1]])
  })
})
