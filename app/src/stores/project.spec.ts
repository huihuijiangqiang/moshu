import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { contentApi } from '@/api/content'
import { useProjectStore } from './project'

describe('project store editor content', () => {
  beforeEach(() => setActivePinia(createPinia()))

  afterEach(() => vi.restoreAllMocks())

  it('keeps the latest editor content in memory across chapter switches', () => {
    const store = useProjectStore()
    store.chapters = [{
      id: 'ch-1', volumeId: 'v-1', index: 1, title: '第一章', words: 0,
      status: 'drafting', outline: [], outlineNote: '', content: '<p>old</p>', rev: 1
    }]

    store.setContent('ch-1', '<p>edited</p>', 2)

    expect(store.chapters[0]?.content).toBe('<p>edited</p>')
    expect(store.chapters[0]?.rev).toBe(2)
  })

  it('preserves a loaded body and its matching revision across structure refreshes', async () => {
    const store = useProjectStore()
    store.loadedProjectId = 'p-1'
    store.activeId = 'ch-1'
    store.chapters = [{
      id: 'ch-1', volumeId: 'v-1', index: 1, title: '第一章', words: 4,
      status: 'drafting', outline: [], outlineNote: '', content: '<p>本地正文</p>', rev: 7
    }]
    vi.spyOn(contentApi, 'getProject').mockResolvedValue({
      id: 'p-1', title: '测试作品', genre: '悬疑', status: 'ongoing', dailyGoal: 2000,
      dailyWords: 0, styleProfile: null,
      volumes: [{ id: 'v-1', index: 1, title: '第一卷' }]
    })
    vi.spyOn(contentApi, 'listChapters').mockResolvedValue([{
      id: 'ch-1', volumeId: 'v-1', index: 2, title: '改名后的第一章', words: 4,
      status: 'drafting', outline: [], outlineNote: ''
    }])

    await store.refreshStructure()

    expect(store.chapters[0]).toMatchObject({
      index: 2,
      title: '改名后的第一章',
      content: '<p>本地正文</p>',
      rev: 7
    })
  })

  it('keeps the body revision returned by the chapter detail endpoint', async () => {
    const store = useProjectStore()
    store.chapters = [{
      id: 'ch-1', volumeId: 'v-1', index: 1, title: '第一章', words: 4,
      status: 'drafting', outline: [], outlineNote: ''
    }]
    vi.spyOn(contentApi, 'getChapter').mockResolvedValue({
      ...store.chapters[0]!, content: '<p>云端正文</p>', rev: 9
    })

    await store.openChapter('ch-1')

    expect(store.chapters[0]).toMatchObject({ content: '<p>云端正文</p>', rev: 9 })
  })
})
