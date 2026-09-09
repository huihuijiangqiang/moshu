import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RouteLocationNormalized } from 'vue-router'
import { setSession } from '@/api/session'
import { authGuard } from './index'

const storage = new Map<string, string>()
vi.stubGlobal('localStorage', {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
  removeItem: (key: string) => storage.delete(key),
  clear: () => storage.clear()
})

function route(name: string, fullPath: string, meta: Record<string, unknown> = {}) {
  return { name, fullPath, meta } as RouteLocationNormalized
}

describe('authentication route guard', () => {
  beforeEach(() => storage.clear())

  it('redirects an anonymous user to login and preserves the destination', () => {
    expect(authGuard(route('workspace', '/projects/p1/write'), false)).toEqual({
      name: 'login',
      query: { redirect: '/projects/p1/write' }
    })
  })

  it('allows an anonymous user to view a public page', () => {
    expect(authGuard(route('landing', '/', { public: true }), false)).toBe(true)
  })

  it('keeps an authenticated user out of the login page', () => {
    setSession({
      access_token: 'access',
      refresh_token: 'refresh',
      user: { id: 'u1', name: '作者', email: null, plan: 'free' }
    })

    expect(authGuard(route('login', '/login'), false)).toEqual({ name: 'shelf' })
  })

  it('does not require a session in explicit mock mode', () => {
    expect(authGuard(route('workspace', '/projects/p1/write'), true)).toBe(true)
  })

  it('keeps regular users out of administrator routes', () => {
    setSession({
      access_token: 'access', refresh_token: 'refresh',
      user: { id: 'u1', name: '作者', email: null, plan: 'free', system_role: 'user' }
    })
    expect(authGuard(route('admin', '/admin', { admin: true }), false)).toEqual({ name: 'shelf' })
  })

  it('allows an administrator into administrator routes', () => {
    setSession({
      access_token: 'access', refresh_token: 'refresh',
      user: { id: 'u1', name: '管理员', email: null, plan: 'studio', system_role: 'admin' }
    })
    expect(authGuard(route('admin', '/admin', { admin: true }), false)).toBe(true)
  })
})
