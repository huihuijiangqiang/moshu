import { request } from './http'

export interface AgentSession {
  id: string
  project_id: string | null
  title: string
  status: string
  created_at: string
  updated_at: string
}

export interface AgentMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  sequence: number
  metadata: Record<string, unknown>
  created_at: string
}

export interface AgentAction {
  id: string
  type: string
  title: string
  parameters: Record<string, unknown>
  status: 'proposed' | 'approved' | 'running' | 'succeeded' | 'failed' | 'rejected'
  result: Record<string, unknown>
  error_code: string | null
  created_at: string
  approved_at: string | null
  executed_at: string | null
}

export interface AgentChatResult {
  session: AgentSession
  message: AgentMessage
  actions: AgentAction[]
}

export const agentApi = {
  createSession: (projectId?: string, title = '新对话') =>
    request<AgentSession>('/agent/sessions', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId ?? null, title })
    }),
  getSession: (id: string) =>
    request<{ session: AgentSession; messages: AgentMessage[]; actions: AgentAction[] }>(`/agent/sessions/${id}/messages`),
  send: (id: string, content: string) =>
    request<AgentChatResult>(`/agent/sessions/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content })
    }),
  decide: (id: string, decision: 'approve' | 'reject') =>
    request<AgentAction>(`/agent/actions/${id}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision })
    })
}
