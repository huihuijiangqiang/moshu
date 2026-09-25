import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { request } from '@/api/http'
import { storyboardApi } from '@/api/storyboard'
import type { ImageGenerationJob, ImageGenerationPreview, StoryboardAdaptation, StoryboardAsset } from '@/types'
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

const sheetJob: ImageGenerationJob = {
  ...queuedJob, status: 'queued', shot_id: null, episode_id: null, visual_profile_id: 'profile_a'
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('storyboard real image generation flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(request).mockReset()
  })

  afterEach(() => vi.restoreAllMocks())

  it('retries a timed-out confirmation with the same idempotency key', async () => {
    const store = useStoryboardStore()
    store.selectedShotId = 'shot_test'
    const submitted: string[] = []
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path === '/shots/shot_test/image-preview') return preview
      if (path === '/shots/shot_test/image-jobs' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        expect(body.model).toBe('gpt-image-2')
        expect(body.credits).toBe(23)
        submitted.push(body.client_request_id)
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

  it('reuses a full-body request after a timeout and character switch, then permits intentional regeneration', async () => {
    const store = useStoryboardStore()
    const submitted: string[] = []
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path.endsWith('/full-body-preview')) return preview
      if (path.endsWith('/full-body-jobs') && init?.method === 'POST') {
        submitted.push(JSON.parse(String(init.body)).client_request_id)
        if (submitted.length === 1) throw new Error('network timeout')
        return { ...sheetJob, status: 'completed' }
      }
      throw new Error(`unexpected request ${path}`)
    })
    store.selectVisualProfile('profile_a')
    await store.prepareFullBodyGeneration('profile_a')
    expect(await store.confirmFullBodyGeneration('profile_a')).toBeNull()
    expect(store.fullBodyError).toBe('network timeout')
    store.selectVisualProfile('profile_b')
    store.selectVisualProfile('profile_a')
    await store.prepareFullBodyGeneration('profile_a')
    expect((await store.confirmFullBodyGeneration('profile_a'))?.status).toBe('completed')
    await store.prepareFullBodyGeneration('profile_a')
    await store.confirmFullBodyGeneration('profile_a')
    expect(submitted).toHaveLength(3)
    expect(submitted[1]).toBe(submitted[0])
    expect(submitted[2]).not.toBe(submitted[0])
  })

  it.each([
    { prompt_sha256: 'b'.repeat(64) }, { model: 'another-image-model' }, { credits: 30 }
  ])('starts a new request when a full-body quote changes: %j', async (change) => {
    const store = useStoryboardStore()
    let quote = { ...preview }
    const submitted: string[] = []
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path.endsWith('/full-body-preview')) return quote
      submitted.push(JSON.parse(String(init?.body)).client_request_id)
      throw new Error('network timeout')
    })
    store.selectVisualProfile('profile_a')
    await store.prepareFullBodyGeneration('profile_a')
    await store.confirmFullBodyGeneration('profile_a')
    quote = { ...preview, ...change }
    await store.prepareFullBodyGeneration('profile_a')
    await store.confirmFullBodyGeneration('profile_a')
    expect(submitted).toHaveLength(2)
    expect(submitted[1]).not.toBe(submitted[0])
  })

  it('discards late quotes and errors without clearing the current character loading state', async () => {
    const store = useStoryboardStore()
    const first = deferred<ImageGenerationPreview>()
    const second = deferred<ImageGenerationPreview>()
    vi.mocked(request).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    store.selectVisualProfile('profile_a')
    const oldRequest = store.prepareFullBodyGeneration('profile_a')
    store.selectVisualProfile('profile_b')
    const currentRequest = store.prepareFullBodyGeneration('profile_b')
    first.reject(new Error('old request failed'))
    await oldRequest
    expect(store.fullBodyError).toBe('')
    expect(store.fullBodyLoading).toBe(true)
    expect(store.fullBodyPreview).toBeNull()
    second.resolve({ ...preview, prompt: 'profile b' })
    await currentRequest
    expect(store.fullBodyPreview?.prompt).toBe('profile b')
    expect(store.fullBodyLoading).toBe(false)

    const late = deferred<ImageGenerationPreview>()
    vi.mocked(request).mockReturnValueOnce(late.promise)
    const lateRequest = store.prepareFullBodyGeneration('profile_b')
    store.selectVisualProfile(null)
    late.resolve(preview)
    expect(await lateRequest).toBeNull()
    expect(store.fullBodyPreview).toBeNull()
    expect(await store.confirmFullBodyGeneration('profile_b')).toBeNull()
  })

  it('keeps current jobs when previous character polls finish late, including switching back', async () => {
    const store = useStoryboardStore()
    const old = deferred<ImageGenerationJob[]>()
    vi.mocked(request).mockReturnValueOnce(old.promise).mockResolvedValueOnce([sheetJob])
    store.selectVisualProfile('profile_a')
    const oldPoll = store.loadFullBodyJobs('profile_a')
    store.selectVisualProfile('profile_b')
    store.selectVisualProfile('profile_a')
    await store.loadFullBodyJobs('profile_a')
    expect(store.fullBodyJobs[0]?.id).toBe(sheetJob.id)
    old.resolve([])
    await oldPoll
    expect(store.fullBodyJobs[0]?.id).toBe(sheetJob.id)
    expect(store.fullBodyJobsLoading).toBe(false)
  })

  it('does not submit for the wrong profile, duplicate an active task, or reuse a rejected quote', async () => {
    const store = useStoryboardStore()
    vi.mocked(request).mockImplementation(async (path) => {
      if (path.endsWith('/full-body-preview')) return preview
      throw new Error(JSON.stringify({ detail: { code: 'image_preview_changed' } }))
    })
    store.selectVisualProfile('profile_a')
    await store.prepareFullBodyGeneration('profile_a')
    expect(await store.confirmFullBodyGeneration('profile_b')).toBeNull()
    store.fullBodyJobs = [sheetJob]
    expect(await store.confirmFullBodyGeneration('profile_a')).toBeNull()
    expect(request).toHaveBeenCalledTimes(1)
    store.fullBodyJobs = []
    expect(await store.confirmFullBodyGeneration('profile_a')).toBeNull()
    expect(store.fullBodyPreview).toBeNull()
    expect(store.fullBodyError).toContain('重新预览')
  })

  it('keeps an in-flight submission locked across character switches and reconciles its result', async () => {
    const store = useStoryboardStore()
    const submission = deferred<ImageGenerationJob>()
    const oldPoll = deferred<ImageGenerationJob[]>()
    let polls = 0
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path.endsWith('/full-body-preview')) return preview
      if (init?.method === 'POST') return submission.promise
      polls += 1
      return polls === 1 ? oldPoll.promise : [sheetJob]
    })
    store.selectVisualProfile('profile_a')
    await store.prepareFullBodyGeneration('profile_a')
    const sending = store.confirmFullBodyGeneration('profile_a')
    store.selectVisualProfile('profile_b')
    expect(store.fullBodyGenerating).toBe(false)
    store.selectVisualProfile('profile_a')
    const polling = store.loadFullBodyJobs('profile_a')
    expect(store.fullBodyGenerating).toBe(true)
    expect(await store.prepareFullBodyGeneration('profile_a')).toBeNull()
    submission.resolve(sheetJob)
    expect(await sending).toBeNull()
    await vi.waitFor(() => expect(store.fullBodyJobs[0]?.id).toBe(sheetJob.id))
    oldPoll.resolve([])
    await polling
    expect(store.fullBodyJobs[0]?.id).toBe(sheetJob.id)
    expect(store.fullBodyGenerating).toBe(false)
  })

  it('does not let an older poll erase an acknowledged full-body task', async () => {
    const store = useStoryboardStore()
    const submission = deferred<ImageGenerationJob>()
    const poll = deferred<ImageGenerationJob[]>()
    vi.mocked(request).mockImplementation(async (path, init) => {
      if (path.endsWith('/full-body-preview')) return preview
      return init?.method === 'POST' ? submission.promise : poll.promise
    })
    store.selectVisualProfile('profile_a')
    await store.prepareFullBodyGeneration('profile_a')
    const sending = store.confirmFullBodyGeneration('profile_a')
    const polling = store.loadFullBodyJobs('profile_a')
    submission.resolve(sheetJob)
    await sending
    poll.resolve([])
    await polling
    expect(store.fullBodyJobs[0]?.id).toBe(sheetJob.id)
  })

  it('ignores an old project load and an asset refresh after changing projects', async () => {
    const store = useStoryboardStore()
    const graph = (id: string): StoryboardAdaptation => ({
      id: `adp_${id}`, projectId: id, title: id, format: 'comic_drama', aspectRatio: '9:16',
      styleProfile: { label: '', description: '' }, status: 'draft', episodes: [], visualProfiles: []
    })
    const slow = deferred<StoryboardAdaptation>()
    vi.spyOn(storyboardApi, 'getStoryboard').mockImplementation(async (id) => id === 'slow' ? slow.promise : graph(id))
    const assetList = vi.spyOn(storyboardApi, 'listStoryboardAssets').mockResolvedValue([])
    const oldLoad = store.load('slow')
    await store.load('current')
    slow.resolve(graph('slow'))
    await oldLoad
    expect(store.adaptation?.projectId).toBe('current')
    expect(assetList).toHaveBeenCalledTimes(1)

    const oldAssets = deferred<StoryboardAsset[]>()
    assetList.mockReturnValueOnce(oldAssets.promise)
    vi.mocked(request).mockResolvedValue([{ ...sheetJob, status: 'completed', asset_id: 'old_asset' }])
    store.selectVisualProfile('profile_a')
    const polling = store.loadFullBodyJobs('profile_a')
    await vi.waitFor(() => expect(assetList).toHaveBeenCalledTimes(2))
    await store.load('next')
    oldAssets.resolve([{ id: 'old_asset' } as StoryboardAsset])
    await polling
    expect(store.adaptation?.projectId).toBe('next')
    expect(store.assets).toEqual([])
    expect(store.fullBodyJobs).toEqual([])
  })
})
