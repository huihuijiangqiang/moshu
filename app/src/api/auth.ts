import { request } from './http'
import { clearSession, getRefreshToken, setSession, type SessionTokens, type SessionUser } from './session'

export interface AuthResponse extends SessionTokens {
  token_type: 'bearer'
  expires_in: number
}

export interface CaptchaConfig {
  mode: 'off' | 'adaptive' | 'always'
  site_key: string | null
  challenge_required: boolean
}

export interface CaptchaChallenge {
  challenge_id: string
  site_key: string
  expires_in: number
}

export const authApi = {
  async login(email: string, password: string, captcha?: { token: string; challenge: string }) {
    const session = await request<AuthResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        email,
        password,
        ...(captcha ? { captcha_token: captcha.token, captcha_challenge: captcha.challenge } : {})
      })
    }, false)
    setSession(session)
    return session.user
  },

  async register(name: string, email: string, password: string, captcha?: { token: string; challenge: string }) {
    const session = await request<AuthResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        name,
        email,
        password,
        ...(captcha ? { captcha_token: captcha.token, captcha_challenge: captcha.challenge } : {})
      })
    }, false)
    setSession(session)
    return session.user
  },

  me() {
    return request<SessionUser>('/auth/me')
  },

  captchaConfig() {
    return request<CaptchaConfig>('/auth/captcha/config', undefined, false)
  },

  captchaChallenge() {
    return request<CaptchaChallenge>('/auth/captcha/challenge', { method: 'POST' }, false)
  },

  async logout() {
    const refreshToken = getRefreshToken()
    try {
      if (refreshToken) {
        await request('/auth/logout', {
          method: 'POST',
          body: JSON.stringify({ refresh_token: refreshToken })
        })
      }
    } finally {
      clearSession()
    }
  }
}
