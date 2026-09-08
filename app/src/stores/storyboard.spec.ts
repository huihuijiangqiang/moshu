import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useStoryboardStore } from './storyboard'
import { mockApi } from '@/api/mock'

describe('storyboard store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.restoreAllMocks())

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

  it('keeps source chapters, scene bindings and visual profiles in the adaptation graph', async () => {
    const store = useStoryboardStore()
    await store.load('p1')

    const episode = await store.createEpisode('p1', {
      title: '第二集 · 玄铁令',
      sourceChapterIds: ['ch87', 'ch88'],
      targetDuration: 110
    })
    expect(episode?.sourceChapterIds).toEqual(['ch87', 'ch88'])

    const scene = await store.createScene('p1', {
      purpose: '老周头揭示玄铁令来历',
      summary: '沈砚决定北上。',
      timeAnchor: '当夜',
      locationEntryId: 'c-yanhui',
      characterEntryIds: ['c-shenyan', 'c-zhoutou']
    })
    expect(scene?.locationEntryId).toBe('c-yanhui')
    expect(scene?.characterEntryIds).toEqual(['c-shenyan', 'c-zhoutou'])

    const profile = await store.createVisualProfile('p1', {
      codexEntryId: 'c-zhoutou',
      displayName: '老周头',
      appearance: '花白短发，右眉有旧疤'
    })
    expect(profile?.version).toBe(1)
    expect(store.adaptation?.visualProfiles.some((item) => item.codexEntryId === 'c-zhoutou')).toBe(true)
  })

  it('surfaces mutation failures without changing the local scene graph', async () => {
    const store = useStoryboardStore()
    await store.load('p1')
    const before = store.selectedEpisode?.scenes.length
    vi.spyOn(mockApi, 'createStoryboardScene').mockRejectedValueOnce(new Error('地点引用已经失效'))

    const result = await store.createScene('p1')

    expect(result).toBeNull()
    expect(store.selectedEpisode?.scenes.length).toBe(before)
    expect(store.error).toBe('地点引用已经失效')
    expect(store.saving).toBe(false)
  })
})
