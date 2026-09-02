import { USE_MOCK, request } from './http'

export type EmbeddingJobStatus = 'ready' | 'pending' | 'queued' | 'running' | 'retrying' | 'dead_letter'

export interface EmbeddingJob {
  projectId: string
  status: EmbeddingJobStatus
  totalCount: number
  freshCount: number
  remainingCount: number
  attempts: number
  dispatchAttempts: number
  taskId: string | null
  errorCode: string | null
  lastError: string | null
  lastAttemptAt: string | null
  exhaustedAt: string | null
  canRetry: boolean
}

interface EmbeddingJobDto {
  project_id: string
  status: EmbeddingJobStatus
  total_count: number
  fresh_count: number
  remaining_count: number
  attempts: number
  dispatch_attempts: number
  task_id: string | null
  error_code: string | null
  last_error: string | null
  last_attempt_at: string | null
  exhausted_at: string | null
  can_retry: boolean
}

export function embeddingJobFromDto(dto: EmbeddingJobDto): EmbeddingJob {
  return {
    projectId: dto.project_id,
    status: dto.status,
    totalCount: dto.total_count,
    freshCount: dto.fresh_count,
    remainingCount: dto.remaining_count,
    attempts: dto.attempts,
    dispatchAttempts: dto.dispatch_attempts,
    taskId: dto.task_id,
    errorCode: dto.error_code,
    lastError: dto.last_error,
    lastAttemptAt: dto.last_attempt_at,
    exhaustedAt: dto.exhausted_at,
    canRetry: dto.can_retry
  }
}

const mockReady: EmbeddingJob = {
  projectId: '', status: 'ready', totalCount: 0, freshCount: 0, remainingCount: 0,
  attempts: 0, dispatchAttempts: 0, taskId: null, errorCode: null, lastError: null,
  lastAttemptAt: null, exhaustedAt: null, canRetry: false
}

export const embeddingApi = {
  async status(projectId: string): Promise<EmbeddingJob> {
    if (USE_MOCK) return { ...mockReady, projectId }
    return embeddingJobFromDto(await request<EmbeddingJobDto>(`/codex/${projectId}/embedding-status`))
  },
  async queue(projectId: string): Promise<EmbeddingJob> {
    if (USE_MOCK) return { ...mockReady, projectId }
    return embeddingJobFromDto(await request<EmbeddingJobDto>(`/codex/${projectId}/embedding-backfill`, {
      method: 'POST'
    }))
  }
}
