const BASE = import.meta.env.VITE_API_BASE ?? '/api'
export const USE_MOCK = import.meta.env.MODE === 'test' || (import.meta.env.VITE_USE_MOCK ?? 'true') === 'true'

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = import.meta.env.VITE_API_TOKEN as string | undefined
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {})
    }
  })
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
