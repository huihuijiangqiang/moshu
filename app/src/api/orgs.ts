import { request } from './http'

export type OrgRole = 'owner' | 'lead' | 'writer' | 'editor' | 'viewer'

export interface Organization {
  id: string
  name: string
  plan: string
  seats: number
  seats_used: number
  role: OrgRole
}

export interface OrganizationMember {
  user_id: string
  name: string
  email: string | null
  role: OrgRole
}

export const orgApi = {
  list: () => request<Organization[]>('/orgs'),
  create: (name: string) => request<Organization>('/orgs', {
    method: 'POST', body: JSON.stringify({ name })
  }),
  members: (orgId: string) => request<OrganizationMember[]>(`/orgs/${orgId}/members`),
  addMember: (orgId: string, email: string, role: OrgRole) => request<OrganizationMember>(`/orgs/${orgId}/members`, {
    method: 'POST', body: JSON.stringify({ email, role })
  }),
  updateMember: (orgId: string, userId: string, role: OrgRole) => request<OrganizationMember>(`/orgs/${orgId}/members/${userId}`, {
    method: 'PATCH', body: JSON.stringify({ role })
  }),
  removeMember: (orgId: string, userId: string) => request<void>(`/orgs/${orgId}/members/${userId}`, { method: 'DELETE' }),
  attachProject: (orgId: string, projectId: string) => request<{ project_id: string; org_id: string }>(`/orgs/${orgId}/projects/${projectId}`, { method: 'POST' }),
  projectPermissions: (projectId: string) => request<string[]>(`/projects/${projectId}/permissions`)
}
