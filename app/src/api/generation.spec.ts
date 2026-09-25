import { afterEach, describe, expect, it, vi } from 'vitest'
import { generationDraftApi, longGenerationPayload, normalizeGenerationError, responseError, sseStream } from './generation'
import { setSession } from './session'

describe('generation stream failure contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('classifies insufficient credits from a 402 response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: 'INSUFFICIENT_CREDITS', required: 35, remaining: 2 } }),
      { status: 402, headers: { 'content-type': 'application/json' } }
    )))
    const failure = await responseError(new Response(
      JSON.stringify({ detail: { code: 'INSUFFICIENT_CREDITS', required: 35, remaining: 2 } }),
      { status: 402, headers: { 'content-type': 'application/json' } }
    ))
    expect(failure).toMatchObject({ code: 'INSUFFICIENT_CREDITS', status: 402 })
    expect((failure as Error).message).toContain('需要 35')
  })

  it('classifies a network failure so the UI can offer a retry', async () => {
    const failure = normalizeGenerationError(new TypeError('Failed to fetch'))
    expect(failure).toMatchObject({ code: 'network_error' })
  })

  it('maps auth and revision conflicts to stable error codes', async () => {
    const auth = await responseError(new Response(JSON.stringify({ detail: '登录已过期' }), { status: 401 }))
    const conflict = await responseError(new Response(JSON.stringify({ detail: '正文版本已变化' }), { status: 409 }))
    expect([auth.code, conflict.code]).toEqual(['AUTH_REQUIRED', 'GENERATION_CONFLICT'])
  })

  it('persists versioned paragraph review decisions', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null })
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'draft-1' }), {
      status: 200,
      headers: { 'content-type': 'application/json' }
    }))
    vi.stubGlobal('fetch', fetchMock)

    await generationDraftApi.review('draft-1', ['p1', 'p3'], 'accepted', 4)

    expect(fetchMock).toHaveBeenCalledWith('/api/generate/drafts/draft-1/review', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ segmentIds: ['p1', 'p3'], decision: 'accepted', baseVersion: 4 })
    }))
  })

  it('builds a real long-form request with the selected context policy', () => {
    expect(longGenerationPayload({
      model: 'advanced',
      providerModel: 'doubao-seed-2.1-turbo',
      contextMode: 'deep',
      dialogueDensity: 'high',
      instruction: '在段尾留下可见风险。'
    })).toEqual({
      model: 'advanced',
      modelId: 'doubao-seed-2.1-turbo',
      useStyleProfile: true,
      dialogueDensity: 'high',
      contextMode: 'deep',
      instruction: '在段尾留下可见风险。'
    })
  })

  it('refreshes an expired access token before opening the generation stream', async () => {
    const user = { id: 'u1', name: '作者', email: 'writer@example.com', plan: 'free' as const }
    const storage = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key)
    })
    setSession({ access_token: 'expired', refresh_token: 'refresh-token', user })
    const stream = new Response('data: {"type":"done"}\n\ndata: [DONE]\n\n', {
      status: 200,
      headers: { 'content-type': 'text/event-stream' }
    })
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response('expired', { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        access_token: 'renewed', refresh_token: 'rotated', user
      }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(stream)
    const onDone = vi.fn()
    const onError = vi.fn()
    await sseStream(
      '/generate/chapter',
      {
        chapterId: 'ch87', targetWords: 3000, model: 'basic', useStyleProfile: true,
        dialogueDensity: 'high'
      },
      new AbortController().signal,
      { onChunk: vi.fn(), onDone, onError }
    )
    expect(onDone).toHaveBeenCalledOnce()
    expect(onError).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[2]?.[1]?.headers).toMatchObject({ Authorization: 'Bearer renewed' })
  })
})
