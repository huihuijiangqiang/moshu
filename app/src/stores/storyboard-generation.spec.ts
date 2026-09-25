import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { request } from '@/api/http'
import { useStoryboardStore } from './storyboard'

vi.mock('@/api/http', () => ({
  USE_MOCK: false,
  request: vi.fn(),
  requestResponse: vi.fn(),
  delay: vi.fn(async () => {})
}))

const preview = {
  prompt: 'one storyboard frame', prompt_sha256: 'a'.repeat(64), profile_versions: {},
  model: 'gpt-image-2', credits: 23, ready: true, issues: []
}

const queuedJob = {
  id: 'pj_test', adaptation_id: 'adp_test', episode_id: 'ep_test', shot_id: 'shot_test',
  status: 'queued', model: 'gpt-image-2', prompt_sha256: 'a'.repeat(64), credits: 23,
  asset_id: null, error_code: null, created_at: new Date().toISOString(),
  started_at: null, finished_at: null
}

describe('storyboard real image generation flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(request).mockReset()
  })

  it('retries a timed-out confirmation with the same idempotency key', async () => {
    const store = useStoryboardStore()
    store.selectedShotId = 'shot_test'
    const submitted: string[] = []
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path === '/shots/shot_test/image-preview') return preview
      if (path === '/shots/shot_test/image-jobs' && init?.method === 'POST') {
        submitted.push(JSON.parse(String(init.body)).client_request_id)
        if (submitted.length === 1) throw new Error('network timeout')
        return queuedJob
      }
      throw new Error(`unexpected request ${path}`)
    })

    expect((await store.prepareImageGeneration('shot_test'))?.credits).toBe(23)
    expect(await store.confirmImageGeneration('shot_test')).toBeNull()
    expect(store.generationError).toBe('network timeout')
    await store.prepareImageGeneration('shot_test')
    expect((await store.confirmImageGeneration('shot_test'))?.id).toBe('pj_test')
    expect(submitted).toHaveLength(2)
    expect(submitted[0]).toBe(submitted[1])
    expect(store.imagePreview).toBeNull()
    expect(store.imageJobs[0]?.status).toBe('queued')
  })

  it('requires a new preview when the server rejects a stale prompt', async () => {
    const store = useStoryboardStore()
    store.selectedShotId = 'shot_test'
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path === '/shots/shot_test/image-preview') return preview
      if (path === '/shots/shot_test/image-jobs' && init?.method === 'POST') {
        throw new Error(JSON.stringify({ detail: { code: 'image_preview_changed' } }))
      }
      throw new Error(`unexpected request ${path}`)
    })

    await store.prepareImageGeneration('shot_test')
    expect(await store.confirmImageGeneration('shot_test')).toBeNull()
    expect(store.imagePreview).toBeNull()
    expect(store.generationError).toBe('镜头或人物档案已变更，请重新预览')
  })

  it('loads task state and cancels only through the real API', async () => {
    const store = useStoryboardStore()
    store.selectedShotId = 'shot_test'
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path === '/shots/shot_test/image-jobs') return [queuedJob]
      if (path === '/image-jobs/pj_test/cancel' && init?.method === 'POST') {
        return { ...queuedJob, status: 'cancelled' }
      }
      throw new Error(`unexpected request ${path}`)
    })

    await store.loadImageJobs('shot_test')
    expect(store.imageJobs[0]?.status).toBe('queued')
    expect((await store.cancelImageGeneration('pj_test'))?.status).toBe('cancelled')
    expect(store.imageJobs[0]?.status).toBe('cancelled')
  })
})
