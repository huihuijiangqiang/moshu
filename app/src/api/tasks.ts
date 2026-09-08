import { delay, request, USE_MOCK } from './http'

export type TaskKind = 'generation' | 'consistency' | 'embedding' | 'outbox'
export type TaskState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface TaskRetry {
  method: 'POST'
  path: string
  body?: Record<string, unknown> | null
  label: string
}

export interface TaskItem {
  id: string
  kind: TaskKind
  title: string
  project_id: string
  project_title: string
  chapter_id: string | null
  chapter_index: number | null
  chapter_title: string | null
  state: TaskState
  source_status: string
  progress: number | null
  progress_label: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  error_code: string | null
  error_detail: string | null
  open_path: string | null
  retry: TaskRetry | null
}

export interface TaskOverview {
  generated_at: string
  counts: {
    total: number
    queued: number
    running: number
    succeeded: number
    failed: number
    cancelled: number
  }
  items: TaskItem[]
  limit: number
  has_more: boolean
}

const mockOverview: TaskOverview = {
  generated_at: new Date().toISOString(),
  counts: { total: 0, queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 },
  items: [],
  limit: 100,
  has_more: false
}

export const tasksApi = {
  async overview(limit = 100): Promise<TaskOverview> {
    if (USE_MOCK) {
      await delay(120)
      return { ...mockOverview, generated_at: new Date().toISOString(), limit }
    }
    return request<TaskOverview>(`/tasks/overview?limit=${encodeURIComponent(String(limit))}`)
  },

  /** Trigger an existing JSON retry endpoint. Streaming generation is opened in the editor instead. */
  async trigger(retry: TaskRetry): Promise<void> {
    await request(retry.path, {
      method: retry.method,
      ...(retry.body ? { body: JSON.stringify(retry.body) } : {})
    })
  }
}
