import { USE_MOCK, delay, request } from './http'

export interface AuthSession {
  id: string
  created_at: string
  last_used_at: string
  expires_at: string
  revoked_at: string | null
  current: boolean
  active: boolean
}

const realAccountSecurityApi = {
  listSessions: () => request<AuthSession[]>('/auth/sessions'),
  revokeSession: (sessionId: string) => request<void>(`/auth/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),
  changePassword: (currentPassword: string, newPassword: string) => request<void>('/auth/password/change', {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
  }),
  requestPasswordReset: (email: string) => request<{ accepted: boolean }>('/auth/password-reset/request', {
    method: 'POST',
    body: JSON.stringify({ email })
  }, false),
  confirmPasswordReset: (token: string, newPassword: string) => request<void>('/auth/password-reset/confirm', {
    method: 'POST',
    body: JSON.stringify({ token, new_password: newPassword })
  }, false)
}

const mockSessions: AuthSession[] = [{
  id: 'mock-session-current',
  created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  last_used_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 29 * 24 * 60 * 60 * 1000).toISOString(),
  revoked_at: null,
  current: true,
  active: true
}]

const mockAccountSecurityApi = {
  async listSessions() { await delay(80); return structuredClone(mockSessions) },
  async revokeSession(sessionId: string) {
    await delay(80)
    const session = mockSessions.find((item) => item.id === sessionId)
    if (session) { session.revoked_at = new Date().toISOString(); session.active = false }
  },
  async changePassword() { await delay(100) },
  async requestPasswordReset() { await delay(100); return { accepted: true } },
  async confirmPasswordReset() { await delay(100) }
}

export const accountSecurityApi = USE_MOCK ? mockAccountSecurityApi : realAccountSecurityApi
