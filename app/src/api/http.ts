import { clearSession, getAccessToken, getRefreshToken, setSession, type SessionTokens } from './session'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'
export const USE_MOCK = import.meta.env.MODE === 'test' || (import.meta.env.VITE_USE_MOCK ?? 'true') === 'true'
let refreshInFlight: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return null
  if (!refreshInFlight) {
    refreshInFlight = fetch(BASE + '/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken })
    }).then(async (response) => {
      if (!response.ok) {
        clearSession()
        return null
      }
      const session = await response.json() as SessionTokens
      setSession(session)
      return session.access_token
    }).finally(() => { refreshInFlight = null })
  }
  return refreshInFlight
}

export async function request<T>(path: string, init?: RequestInit, retryAuth = true): Promise<T> {
  const token = getAccessToken()
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {})
    }
  })
  if (res.status === 401 && retryAuth && !path.startsWith('/auth/')) {
    const refreshed = await refreshAccessToken()
    if (refreshed) return request<T>(path, init, false)
    window.dispatchEvent(new CustomEvent('moshu:unauthorized'))
  }
  if (!res.ok) throw new ApiError(res.status, await res.text())
  return (await res.json()) as T
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

/** mock 用：模拟网络延迟，暴露真实的 loading 状态，别让 UI 假装瞬时 */
export const delay = (ms = 260) => new Promise<void>((r) => setTimeout(r, ms))
