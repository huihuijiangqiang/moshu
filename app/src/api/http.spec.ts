import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { request, requestResponse } from './http'
import { clearSession, setSession } from './session'

const user = { id: 'u1', name: '作者', email: 'writer@example.com', plan: 'free' }
const storage = new Map<string, string>()
vi.stubGlobal('localStorage', {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
  removeItem: (key: string) => storage.delete(key),
  clear: () => storage.clear()
})

function response(status: number, body: unknown) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  })
}

describe('authenticated API requests', () => {
  beforeEach(() => {
    storage.clear()
    vi.restoreAllMocks()
  })

  afterEach(() => clearSession())

  it('attaches the runtime access token instead of a build-time credential', async () => {
    setSession({ access_token: 'runtime-access', refresh_token: 'runtime-refresh', user })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(200, { ok: true }))

    await request('/projects')

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: 'Bearer runtime-access'
    })
  })

  it('accepts an empty 204 response for delete and logout requests', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))

    await expect(request<void>('/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
  })

  it('returns an authenticated raw response for file downloads', async () => {
    setSession({ access_token: 'runtime-access', refresh_token: 'runtime-refresh', user })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('manuscript', {
      status: 200,
      headers: { 'Content-Type': 'text/plain' }
    }))

    const result = await requestResponse('/projects/p1/export')

    await expect(result.text()).resolves.toBe('manuscript')
  })

  it('leaves multipart content type to the browser boundary generator', async () => {
    const body = new FormData()
    body.append('file', new Blob(['reference']), 'reference.txt')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(200, { ok: true }))

    await requestResponse('/analysis/deconstruct', { method: 'POST', body })

    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>
    expect(headers['Content-Type']).toBeUndefined()
  })

  it('refreshes once after a 401 and retries with the new access token', async () => {
    setSession({ access_token: 'expired', refresh_token: 'refresh-token', user })
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(401, 'expired'))
      .mockResolvedValueOnce(response(200, {
        access_token: 'renewed',
        refresh_token: 'rotated',
        user
      }))
      .mockResolvedValueOnce(response(200, { id: 'p1' }))

    await expect(request<{ id: string }>('/projects/p1')).resolves.toEqual({ id: 'p1' })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/auth/refresh')
    expect(fetchMock.mock.calls[2]?.[1]?.headers).toMatchObject({ Authorization: 'Bearer renewed' })
    expect(storage.get('moshu:access-token')).toBe('renewed')
    expect(storage.get('moshu:refresh-token')).toBe('rotated')
  })

  it('clears the session and emits an unauthorized event when refresh fails', async () => {
    setSession({ access_token: 'expired', refresh_token: 'invalid-refresh', user })
    const unauthorized = vi.fn()
    window.addEventListener('moshu:unauthorized', unauthorized, { once: true })
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(401, 'expired'))
      .mockResolvedValueOnce(response(401, 'invalid refresh'))

    await expect(request('/projects/p1')).rejects.toMatchObject({ status: 401 })

    expect(unauthorized).toHaveBeenCalledOnce()
    expect(storage.get('moshu:access-token')).toBeUndefined()
    expect(storage.get('moshu:refresh-token')).toBeUndefined()
  })
})
