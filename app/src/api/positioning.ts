import { delay, request, USE_MOCK } from './http'

export type TargetPlatform = 'fanqie' | 'qimao' | 'qidian' | 'general'
export type PositioningStatus = 'draft' | 'active' | 'archived'

export interface ProjectPositioning {
  id: string
  projectId: string
  platform: TargetPlatform
  titleCandidates: string[]
  sellingPoint: string
  synopsis: string
  tags: string[]
  protagonistDilemma: string
  firstPayoff: string
  longTermArc: string
  revision: number
  status: PositioningStatus
  createdAt: string
  updatedAt: string
}

export interface PositioningPatch {
  expectedRevision: number
  platform?: TargetPlatform
  titleCandidates?: string[]
  sellingPoint?: string
  synopsis?: string
  tags?: string[]
  protagonistDilemma?: string
  firstPayoff?: string
  longTermArc?: string
  status?: PositioningStatus
}

interface PositioningDto {
  id: string
  project_id: string
  platform: TargetPlatform
  title_candidates: string[]
  selling_point: string
  synopsis: string
  tags: string[]
  protagonist_dilemma: string
  first_payoff: string
  long_term_arc: string
  revision: number
  status: PositioningStatus
  created_at: string
  updated_at: string
}

function fromDto(row: PositioningDto): ProjectPositioning {
  return {
    id: row.id, projectId: row.project_id, platform: row.platform,
    titleCandidates: row.title_candidates ?? [], sellingPoint: row.selling_point ?? '', synopsis: row.synopsis ?? '',
    tags: row.tags ?? [], protagonistDilemma: row.protagonist_dilemma ?? '', firstPayoff: row.first_payoff ?? '',
    longTermArc: row.long_term_arc ?? '', revision: row.revision, status: row.status,
    createdAt: row.created_at, updatedAt: row.updated_at
  }
}

const mockCards = new Map<string, ProjectPositioning>()
function mockCard(projectId: string): ProjectPositioning {
  const existing = mockCards.get(projectId)
  if (existing) return existing
  const now = new Date().toISOString()
  const card: ProjectPositioning = {
    id: `positioning-${projectId}`, projectId, platform: 'general', titleCandidates: [], sellingPoint: '', synopsis: '',
    tags: [], protagonistDilemma: '', firstPayoff: '', longTermArc: '', revision: 0, status: 'draft', createdAt: now, updatedAt: now
  }
  mockCards.set(projectId, card)
  return card
}

export const positioningApi = {
  async get(projectId: string): Promise<ProjectPositioning | null> {
    if (USE_MOCK) { await delay(50); return structuredClone(mockCard(projectId)) }
    const row = await request<PositioningDto | null>(`/projects/${projectId}/positioning`)
    return row ? fromDto(row) : null
  },
  async update(projectId: string, patch: PositioningPatch): Promise<ProjectPositioning> {
    if (USE_MOCK) {
      await delay(80)
      const card = mockCard(projectId)
      if (patch.expectedRevision !== card.revision) throw new Error('positioning_revision_conflict')
      Object.entries(patch).forEach(([key, value]) => {
        if (key !== 'expectedRevision' && value !== undefined) {
          (card as unknown as Record<string, unknown>)[key] = Array.isArray(value) ? [...value] : value
        }
      })
      card.revision += 1; card.updatedAt = new Date().toISOString()
      return structuredClone(card)
    }
    const body: Record<string, unknown> = { expected_revision: patch.expectedRevision }
    const map: Record<string, string> = {
      platform: 'platform', titleCandidates: 'title_candidates', sellingPoint: 'selling_point', synopsis: 'synopsis',
      tags: 'tags', protagonistDilemma: 'protagonist_dilemma', firstPayoff: 'first_payoff', longTermArc: 'long_term_arc', status: 'status'
    }
    Object.entries(patch).forEach(([key, value]) => { if (key !== 'expectedRevision' && value !== undefined) body[map[key] ?? key] = value })
    return fromDto(await request<PositioningDto>(`/projects/${projectId}/positioning`, { method: 'PUT', body: JSON.stringify(body) }))
  },
  async revisions(projectId: string): Promise<ProjectPositioning[]> {
    if (USE_MOCK) return []
    return (await request<PositioningDto[]>(`/projects/${projectId}/positioning/revisions`)).map(fromDto)
  }
}
