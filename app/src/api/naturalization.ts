import { request, USE_MOCK } from './http'

export type NaturalizationScope = 'chapter' | 'selection'
export type NaturalizationMode = 'rules'
export type NaturalizationFindingStatus = 'pending' | 'accepted' | 'rejected' | 'stale'

export interface NaturalizationFinding {
  id: string
  runId: string
  paragraphId: string
  start: number
  end: number
  originalText: string
  candidateText: string
  sourceTextHash: string
  ruleIds: string[]
  reasons: string[]
  lockedFacts: Record<string, unknown>
  validation: Record<string, unknown>
  status: NaturalizationFindingStatus
  revision: number
}

export interface NaturalizationRun {
  id: string
  userId: string
  projectId: string
  chapterId: string
  sourceBodyRev: number
  sourceContentHash: string
  scope: NaturalizationScope
  mode: NaturalizationMode
  status: 'scanning' | 'ready' | 'stale' | 'failed' | 'completed'
  styleProfileId: string | null
  promptVersion: string
  model: string | null
  findingCount: number
  acceptedCount: number
  errorCode: string | null
  createdAt: string | null
  updatedAt: string | null
  findings: NaturalizationFinding[]
}

interface FindingDto {
  id: string
  run_id: string
  paragraph_id: string
  start: number
  end: number
  original_text: string
  candidate_text: string
  source_text_hash: string
  rule_ids: string[]
  reasons: string[]
  locked_facts: Record<string, unknown>
  validation: Record<string, unknown>
  status: NaturalizationFindingStatus
  revision: number
}

interface RunDto {
  id: string
  user_id: string
  project_id: string
  chapter_id: string
  source_body_rev: number
  source_content_hash: string
  scope: NaturalizationScope
  mode: NaturalizationMode
  status: NaturalizationRun['status']
  style_profile_id: string | null
  prompt_version: string
  model: string | null
  finding_count: number
  accepted_count: number
  error_code: string | null
  created_at: string | null
  updated_at: string | null
}

interface RunDetailDto extends RunDto {
  findings: FindingDto[]
}

function mapFinding(dto: FindingDto): NaturalizationFinding {
  return {
    id: dto.id,
    runId: dto.run_id,
    paragraphId: dto.paragraph_id,
    start: dto.start,
    end: dto.end,
    originalText: dto.original_text,
    candidateText: dto.candidate_text,
    sourceTextHash: dto.source_text_hash,
    ruleIds: dto.rule_ids ?? [],
    reasons: dto.reasons ?? [],
    lockedFacts: dto.locked_facts ?? {},
    validation: dto.validation ?? {},
    status: dto.status,
    revision: dto.revision
  }
}

function mapRun(dto: RunDetailDto): NaturalizationRun {
  return {
    id: dto.id,
    userId: dto.user_id,
    projectId: dto.project_id,
    chapterId: dto.chapter_id,
    sourceBodyRev: dto.source_body_rev,
    sourceContentHash: dto.source_content_hash,
    scope: dto.scope,
    mode: dto.mode,
    status: dto.status,
    styleProfileId: dto.style_profile_id,
    promptVersion: dto.prompt_version,
    model: dto.model,
    findingCount: dto.finding_count,
    acceptedCount: dto.accepted_count,
    errorCode: dto.error_code,
    createdAt: dto.created_at,
    updatedAt: dto.updated_at,
    findings: dto.findings?.map(mapFinding) ?? []
  }
}

export const naturalizationApi = {
  async scan(projectId: string, input: {
    chapterId: string
    scope?: NaturalizationScope
    mode?: NaturalizationMode
    paragraphIds?: string[]
    sourceBodyRev?: number
    styleProfileId?: string | null
  }): Promise<NaturalizationRun> {
    if (USE_MOCK) {
      return {
        id: `mock-naturalization-${Date.now()}`,
        userId: 'mock-user',
        projectId,
        chapterId: input.chapterId,
        sourceBodyRev: input.sourceBodyRev ?? 0,
        sourceContentHash: '',
        scope: input.scope ?? 'chapter',
        mode: input.mode ?? 'rules',
        status: 'ready',
        styleProfileId: input.styleProfileId ?? null,
        promptVersion: 'zh-fiction-risk-v1:naturalize-v1',
        model: null,
        findingCount: 0,
        acceptedCount: 0,
        errorCode: null,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        findings: []
      }
    }
    const dto = await request<RunDetailDto>(`/projects/${projectId}/naturalization/scans`, {
      method: 'POST',
      body: JSON.stringify({
        chapter_id: input.chapterId,
        scope: input.scope ?? 'chapter',
        mode: input.mode ?? 'rules',
        paragraph_ids: input.paragraphIds ?? [],
        ...(input.sourceBodyRev === undefined ? {} : { source_body_rev: input.sourceBodyRev }),
        style_profile_id: input.styleProfileId ?? null
      })
    })
    return mapRun(dto)
  },

  async getRun(runId: string): Promise<NaturalizationRun> {
    if (USE_MOCK) throw new Error('naturalization_run_not_available_in_mock')
    return mapRun(await request<RunDetailDto>(`/naturalization/runs/${runId}`))
  },

  async regenerateCandidate(runId: string, findingId: string): Promise<NaturalizationFinding> {
    if (USE_MOCK) throw new Error('naturalization_candidate_not_available_in_mock')
    return mapFinding(await request<FindingDto>(`/naturalization/runs/${runId}/findings/${findingId}/candidate`, { method: 'POST' }))
  },

  async accept(runId: string, findingId: string): Promise<NaturalizationRun> {
    if (USE_MOCK) throw new Error('naturalization_accept_not_available_in_mock')
    return mapRun(await request<RunDetailDto>(`/naturalization/runs/${runId}/findings/${findingId}/accept`, { method: 'POST' }))
  },

  async reject(runId: string, findingId: string): Promise<NaturalizationFinding> {
    if (USE_MOCK) throw new Error('naturalization_reject_not_available_in_mock')
    return mapFinding(await request<FindingDto>(`/naturalization/runs/${runId}/findings/${findingId}/reject`, { method: 'POST' }))
  }
}
