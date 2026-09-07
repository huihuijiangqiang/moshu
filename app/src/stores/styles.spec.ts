import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { stylesApi, type StyleProfile } from '@/api/styles'
import { useStylesStore } from './styles'

vi.mock('@/api/styles', () => ({
  stylesApi: {
    list: vi.fn(), create: vi.fn(), update: vi.fn(), extract: vi.fn(), remove: vi.fn(), bind: vi.fn()
  }
}))

const profile: StyleProfile = {
  id: 'style_a',
  name: '田园白描',
  sampleWords: 52000,
  isDefault: true,
  dimensions: {},
  status: 'ready',
  confidence: 'standard',
  createdAt: '2026-09-01T00:00:00Z',
  updatedAt: '2026-09-01T00:00:00Z',
  boundProjectIds: []
}

describe('styles store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(stylesApi.list).mockResolvedValue([structuredClone(profile)])
  })

  it('loads once and resolves a bound profile name by id', async () => {
    const store = useStylesStore()
    await store.load()
    await store.load()
    expect(store.byId.get('style_a')?.name).toBe('田园白描')
    expect(stylesApi.list).toHaveBeenCalledTimes(1)
  })

  it('replaces an extracted profile and synchronizes project bindings', async () => {
    const store = useStylesStore()
    await store.load()
    vi.mocked(stylesApi.extract).mockResolvedValue({ ...profile, status: 'ready', dimensions: {
      dialogue: { title: '对白习惯', score: 60, summary: '对白直接', traits: [], avoid: [] }
    } })
    vi.mocked(stylesApi.bind).mockResolvedValue({ projectId: 'project_a', styleProfileId: 'style_a' })

    await store.extract('style_a')
    await store.bind('project_a', 'style_a')

    expect(store.byId.get('style_a')?.dimensions.dialogue.summary).toBe('对白直接')
    expect(store.byId.get('style_a')?.boundProjectIds).toEqual(['project_a'])
  })
})
