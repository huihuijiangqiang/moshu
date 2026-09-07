import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useStoryboardStore } from './storyboard'

describe('storyboard store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('loads an adaptation with scene and shot structure', async () => {
    const store = useStoryboardStore()
    await store.load('p1')

    expect(store.adaptation?.format).toBe('comic_drama')
    expect(store.selectedEpisode?.scenes.length).toBeGreaterThan(0)
    expect(store.selectedScene?.shots.length).toBeGreaterThan(0)
    expect(store.selectedShot?.referenceAssetIds.length).toBeGreaterThanOrEqual(0)
  })

  it('creates a shot and persists its editable fields', async () => {
    const store = useStoryboardStore()
    await store.load('p1')
    const before = store.selectedScene?.shots.length ?? 0

    await store.createShot('p1')
    expect(store.selectedScene?.shots.length).toBe(before + 1)
    await store.updateShot('p1', { action: '人物回头，停在门槛前', status: 'approved' })

    expect(store.selectedShot?.action).toBe('人物回头，停在门槛前')
    expect(store.selectedShot?.status).toBe('approved')
  })
})
