import { afterEach, describe, expect, it, vi } from 'vitest'
import { GenerationError, generationDraftApi, normalizeGenerationError, responseError } from './generation'

describe('generation stream failure contract', () => {
  afterEach(() => vi.unstubAllGlobals())

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
})
