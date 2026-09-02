export interface SessionUser {
  id: string
  name: string
  email: string | null
  plan: string
}

export interface SessionTokens {
  access_token: string
  refresh_token: string
  user: SessionUser
}

const ACCESS_KEY = 'moshu:access-token'
const REFRESH_KEY = 'moshu:refresh-token'
const USER_KEY = 'moshu:session-user'

function storageAvailable() {
  return typeof localStorage !== 'undefined'
}

export function getAccessToken() {
  return storageAvailable() ? localStorage.getItem(ACCESS_KEY) : null
}

export function getRefreshToken() {
  return storageAvailable() ? localStorage.getItem(REFRESH_KEY) : null
}

export function hasSession() {
  return !!(getAccessToken() || getRefreshToken())
}

export function setSession(session: SessionTokens) {
  if (!storageAvailable()) return
  localStorage.setItem(ACCESS_KEY, session.access_token)
  localStorage.setItem(REFRESH_KEY, session.refresh_token)
  localStorage.setItem(USER_KEY, JSON.stringify(session.user))
}

export function clearSession() {
  if (!storageAvailable()) return
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(USER_KEY)
}
