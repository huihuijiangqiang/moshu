import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProjectStore } from './project'

describe('project store editor content', () => {
  beforeEach(() => setActivePinia(createPinia()))

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
})
