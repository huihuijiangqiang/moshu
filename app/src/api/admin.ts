import { request } from './http'

export interface AdminOverview {
  users: number
  projects: number
  active_consistency_runs: number
  open_guard_issues: number
  active_sessions: number
}

export interface AdminUser {
  id: string
  name: string
  email: string | null
  plan: 'free' | 'author' | 'studio'
  system_role: 'user' | 'admin' | 'super_admin'
  is_active: boolean
  quota_remaining: number
  quota_total: number
  created_at: string
}

export interface AdminSettings {
  registration_enabled: boolean
  default_plan: 'free' | 'author' | 'studio'
  default_monthly_quota: number
  generation_model: string
  consistency_model: string
  embedding_model: string
  generation_gateway_configured: boolean
  embedding_gateway_configured: boolean
}

export const adminApi = {
  overview: () => request<AdminOverview>('/admin/overview'),
  users: (search = '') => request<AdminUser[]>(`/admin/users?search=${encodeURIComponent(search)}`),
  updateUser: (id: string, patch: Partial<Pick<AdminUser, 'plan' | 'system_role' | 'is_active' | 'quota_remaining' | 'quota_total'>>) =>
    request<AdminUser>(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  settings: () => request<AdminSettings>('/admin/settings'),
  updateSettings: (patch: Pick<AdminSettings, 'registration_enabled' | 'default_plan' | 'default_monthly_quota'>) =>
    request<AdminSettings>('/admin/settings', { method: 'PATCH', body: JSON.stringify(patch) })
}
