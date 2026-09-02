import { describe, expect, it } from 'vitest'
import { embeddingJobFromDto } from './embedding'

describe('embedding job DTO', () => {
  it('preserves durable retry and dead-letter details', () => {
    expect(embeddingJobFromDto({
      project_id: 'p1', status: 'dead_letter', total_count: 24, fresh_count: 21,
      remaining_count: 3, attempts: 6, dispatch_attempts: 2, task_id: 'task-1',
      error_code: 'EmbeddingProviderError', last_error: 'dimension mismatch',
      last_attempt_at: '2026-09-03T00:00:00Z', exhausted_at: '2026-09-03T00:00:01Z',
      can_retry: true
    })).toMatchObject({
      projectId: 'p1', status: 'dead_letter', freshCount: 21,
      remainingCount: 3, attempts: 6, dispatchAttempts: 2, canRetry: true
    })
  })
})
