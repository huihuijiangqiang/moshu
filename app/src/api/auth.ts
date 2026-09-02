import { request } from './http'
import { clearSession, setSession, type SessionTokens, type SessionUser } from './session'

export interface AuthResponse extends SessionTokens {
  token_type: 'bearer'
  expires_in: number
}

export const authApi = {
  async login(email: string, password: string) {
    const session = await request<AuthResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password })
    }, false)
    setSession(session)
    return session.user
  },

  async register(name: string, email: string, password: string) {
    const session = await request<AuthResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ name, email, password })
    }, false)
    setSession(session)
    return session.user
  },

  me() {
    return request<SessionUser>('/auth/me')
  },

  logout() {
    clearSession()
  }
}
