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

export type AssignmentStatus = 'assigned' | 'claimed' | 'returned' | 'completed'

export interface ChapterAssignment {
  id: number
  chapter_id: string
  chapter_title: string
  chapter_index: number
  assigned_to: string
  assignee_name: string
  assigned_by: string
  assigner_name: string
  status: AssignmentStatus
  notes: string | null
  words: number
  updated_at: string
}

export interface ProductionMember {
  user_id: string
  name: string
  role: OrgRole
  assigned_count: number
  claimed_count: number
  completed_count: number
  returned_count: number
  active_words: number
  completed_words: number
}

export interface ProductionBoard {
  project_id: string
  org_id: string
  can_manage: boolean
  current_user_id: string
  assignments: ChapterAssignment[]
  members: ProductionMember[]
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
  production: (orgId: string, projectId: string) => request<ProductionBoard>(`/orgs/${orgId}/projects/${projectId}/production`),
  assignChapter: (orgId: string, projectId: string, chapterId: string, assignedTo: string, notes?: string) => request<ChapterAssignment>(`/orgs/${orgId}/projects/${projectId}/assignments`, {
    method: 'POST', body: JSON.stringify({ chapter_id: chapterId, assigned_to: assignedTo, notes: notes || null })
  }),
  updateAssignment: (orgId: string, projectId: string, assignmentId: number, status: Exclude<AssignmentStatus, 'assigned'>) => request<ChapterAssignment>(`/orgs/${orgId}/projects/${projectId}/assignments/${assignmentId}`, {
    method: 'PATCH', body: JSON.stringify({ status })
  }),
  removeAssignment: (orgId: string, projectId: string, assignmentId: number) => request<void>(`/orgs/${orgId}/projects/${projectId}/assignments/${assignmentId}`, { method: 'DELETE' }),
  projectPermissions: (projectId: string) => request<string[]>(`/projects/${projectId}/permissions`)
}
