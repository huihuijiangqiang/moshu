import { ApiError, USE_MOCK, delay, request } from './http'
import type { ReviewComment, ReviewRound, ReviewWorkspace } from '@/types'

interface ReviewCommentDto {
  id: string
  round_id: string
  chapter_id: string
  body_revision: number
  paragraph_id: string
  paragraph_excerpt: string
  selected_text: string | null
  content: string
  status: 'open' | 'resolved'
  revision: number
  author_id: string | null
  author_name: string | null
  resolved_by: string | null
  created_at: string
  resolved_at: string | null
}

interface ReviewRoundDto {
  id: string
  chapter_id: string
  submitted_body_revision: number
  status: ReviewRound['status']
  submit_note: string | null
  decision_note: string | null
  revision: number
  submitted_by: string | null
  submitted_by_name: string | null
  reviewed_by: string | null
  reviewed_by_name: string | null
  submitted_at: string
  reviewed_at: string | null
  stale: boolean
  comments: ReviewCommentDto[]
}

interface ReviewWorkspaceDto {
  chapter_id: string
  current_body_revision: number
  can_submit: boolean
  can_review: boolean
  can_resolve: boolean
  rounds: ReviewRoundDto[]
}

function commentFromDto(row: ReviewCommentDto): ReviewComment {
  return {
    id: row.id,
    roundId: row.round_id,
    chapterId: row.chapter_id,
    bodyRevision: row.body_revision,
    paragraphId: row.paragraph_id,
    paragraphExcerpt: row.paragraph_excerpt,
    selectedText: row.selected_text ?? undefined,
    content: row.content,
    status: row.status,
    revision: row.revision,
    authorId: row.author_id ?? undefined,
    authorName: row.author_name ?? undefined,
    resolvedBy: row.resolved_by ?? undefined,
    createdAt: row.created_at,
    resolvedAt: row.resolved_at ?? undefined
  }
}

export function reviewWorkspaceFromDto(dto: ReviewWorkspaceDto): ReviewWorkspace {
  return {
    chapterId: dto.chapter_id,
    currentBodyRevision: dto.current_body_revision,
    canSubmit: dto.can_submit,
    canReview: dto.can_review,
    canResolve: dto.can_resolve,
    rounds: dto.rounds.map((round) => ({
      id: round.id,
      chapterId: round.chapter_id,
      submittedBodyRevision: round.submitted_body_revision,
      status: round.status,
      submitNote: round.submit_note ?? undefined,
      decisionNote: round.decision_note ?? undefined,
      revision: round.revision,
      submittedBy: round.submitted_by ?? undefined,
      submittedByName: round.submitted_by_name ?? undefined,
      reviewedBy: round.reviewed_by ?? undefined,
      reviewedByName: round.reviewed_by_name ?? undefined,
      submittedAt: round.submitted_at,
      reviewedAt: round.reviewed_at ?? undefined,
      stale: round.stale,
      comments: round.comments.map(commentFromDto)
    }))
  }
}

export class ReviewConflictError extends Error {
  constructor(public currentRevision?: number) {
    super('review_revision_conflict')
    this.name = 'ReviewConflictError'
  }
}

async function call(path: string, init?: RequestInit): Promise<ReviewWorkspace> {
  try {
    return reviewWorkspaceFromDto(await request<ReviewWorkspaceDto>(path, init))
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      let currentRevision: number | undefined
      try {
        const payload = JSON.parse(error.message) as { detail?: { current_revision?: number } }
        currentRevision = payload.detail?.current_revision
      } catch { /* response may not be JSON */ }
      throw new ReviewConflictError(currentRevision)
    }
    throw error
  }
}

const realReviewApi = {
  get(projectId: string, chapterId: string) {
    return call(`/reviews/projects/${projectId}/chapters/${chapterId}`)
  },
  submit(projectId: string, chapterId: string, submitNote?: string) {
    return call(`/reviews/projects/${projectId}/chapters/${chapterId}/submit`, {
      method: 'POST', body: JSON.stringify({ submit_note: submitNote || null })
    })
  },
  addComment(projectId: string, roundId: string, paragraphId: string, selectedText: string | undefined, content: string) {
    return call(`/reviews/projects/${projectId}/rounds/${roundId}/comments`, {
      method: 'POST',
      body: JSON.stringify({ paragraph_id: paragraphId, selected_text: selectedText || null, content })
    })
  },
  updateComment(projectId: string, roundId: string, commentId: string, expectedRevision: number, content: string) {
    return call(`/reviews/projects/${projectId}/rounds/${roundId}/comments/${commentId}`, {
      method: 'PUT', body: JSON.stringify({ expected_revision: expectedRevision, content })
    })
  },
  resolveComment(projectId: string, roundId: string, commentId: string, expectedRevision: number) {
    return call(`/reviews/projects/${projectId}/rounds/${roundId}/comments/${commentId}/resolve`, {
      method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision })
    })
  },
  decide(projectId: string, roundId: string, expectedRevision: number, decision: 'approved' | 'changes_requested', decisionNote?: string) {
    return call(`/reviews/projects/${projectId}/rounds/${roundId}/decision`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: expectedRevision, decision, decision_note: decisionNote || null })
    })
  }
}

const mockStates = new Map<string, ReviewWorkspace>()
let mockSequence = 10

function mockState(projectId: string, chapterId: string): ReviewWorkspace {
  const key = `${projectId}:${chapterId}`
  let state = mockStates.get(key)
  if (!state) {
    const isDemo = projectId === 'p1' && chapterId === 'ch87'
    state = {
      chapterId,
      currentBodyRevision: 1,
      canSubmit: true,
      canReview: true,
      canResolve: true,
      rounds: isDemo ? [{
        id: 'review-demo-1', chapterId, submittedBodyRevision: 1, status: 'submitted',
        submitNote: '请重点核对北狄退兵的判断是否铺垫充分。', revision: 1,
        submittedBy: 'demo-author', submittedByName: '执笔人', submittedAt: '2026-09-06T08:20:00Z',
        stale: false,
        comments: [{
          id: 'comment-demo-1', roundId: 'review-demo-1', chapterId, bodyRevision: 1,
          paragraphId: 'p-2', paragraphExcerpt: '他知道北狄不会就这么退。三日前那一场叩关，对方丢下四百具尸首，连一句话都没留。',
          selectedText: '这不像撤军，更像是在称他这道关的斤两。',
          content: '判断很关键，但证据还偏抽象。补一个敌军撤退阵形或斥候回报，会让结论更可信。',
          status: 'open', revision: 1, authorId: 'demo-editor', authorName: '主编',
          createdAt: '2026-09-06T08:35:00Z'
        }]
      }] : []
    }
    mockStates.set(key, state)
  }
  return state
}

async function mockResult(projectId: string, chapterId: string) {
  await delay(100)
  return structuredClone(mockState(projectId, chapterId))
}

const mockReviewApi = {
  get: mockResult,
  async submit(projectId: string, chapterId: string, submitNote?: string) {
    const state = mockState(projectId, chapterId)
    state.rounds.forEach((round) => { if (round.status === 'submitted') round.status = 'superseded' })
    state.rounds.unshift({
      id: `review-mock-${mockSequence++}`, chapterId, submittedBodyRevision: state.currentBodyRevision,
      status: 'submitted', submitNote: submitNote?.trim() || undefined, revision: 1,
      submittedBy: 'mock-user', submittedByName: '当前作者', submittedAt: new Date().toISOString(),
      stale: false, comments: []
    })
    return mockResult(projectId, chapterId)
  },
  async addComment(projectId: string, roundId: string, paragraphId: string, selectedText: string | undefined, content: string) {
    const state = [...mockStates.values()].find((item) => item.rounds.some((round) => round.id === roundId))
    const round = state?.rounds.find((item) => item.id === roundId)
    if (!state || !round) throw new Error('review_round_not_found')
    round.comments.push({
      id: `comment-mock-${mockSequence++}`, roundId, chapterId: round.chapterId,
      bodyRevision: round.submittedBodyRevision, paragraphId,
      paragraphExcerpt: selectedText || '当前段落', selectedText, content: content.trim(),
      status: 'open', revision: 1, authorId: 'mock-user', authorName: '当前主编',
      createdAt: new Date().toISOString()
    })
    return mockResult(projectId, state.chapterId)
  },
  async updateComment(projectId: string, roundId: string, commentId: string, expectedRevision: number, content: string) {
    const state = [...mockStates.values()].find((item) => item.rounds.some((round) => round.id === roundId))
    const comment = state?.rounds.flatMap((round) => round.comments).find((item) => item.id === commentId)
    if (!state || !comment || comment.revision !== expectedRevision) throw new ReviewConflictError(comment?.revision)
    comment.content = content.trim(); comment.revision += 1
    return mockResult(projectId, state.chapterId)
  },
  async resolveComment(projectId: string, roundId: string, commentId: string, expectedRevision: number) {
    const state = [...mockStates.values()].find((item) => item.rounds.some((round) => round.id === roundId))
    const comment = state?.rounds.flatMap((round) => round.comments).find((item) => item.id === commentId)
    if (!state || !comment || comment.revision !== expectedRevision) throw new ReviewConflictError(comment?.revision)
    comment.status = 'resolved'; comment.revision += 1; comment.resolvedAt = new Date().toISOString()
    return mockResult(projectId, state.chapterId)
  },
  async decide(projectId: string, roundId: string, expectedRevision: number, decision: 'approved' | 'changes_requested', decisionNote?: string) {
    const state = [...mockStates.values()].find((item) => item.rounds.some((round) => round.id === roundId))
    const round = state?.rounds.find((item) => item.id === roundId)
    if (!state || !round || round.revision !== expectedRevision) throw new ReviewConflictError(round?.revision)
    if (decision === 'approved' && round.comments.some((comment) => comment.status === 'open')) throw new ReviewConflictError(round.revision)
    round.status = decision; round.decisionNote = decisionNote?.trim() || undefined
    round.reviewedAt = new Date().toISOString(); round.reviewedByName = '当前主编'; round.revision += 1
    return mockResult(projectId, state.chapterId)
  }
}

export const reviewApi = USE_MOCK ? mockReviewApi : realReviewApi
