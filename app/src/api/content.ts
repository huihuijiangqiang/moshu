import { ApiError, USE_MOCK, request } from './http'
import { mockApi } from './mock'
import type { Chapter, ChapterPlanPatch, CodexEntry, CodexEntryDraft, CodexKind, ContextLayer, GuardIssue, GuardOverview, GuardResolutionAction, Project, ProjectPatch, ProjectTrash, Volume } from '@/types'

interface ProjectDto {
  id: string
  title: string
  genre: string | null
  status: 'ongoing' | 'finished' | 'archived'
  target_words_daily: number
  style_profile_id: string | null
  volumes: Array<{ id: string; title: string; idx: number; summary?: string | null }>
}

interface TrashDto {
  volumes: Array<{ id: string; title: string; deleted_at: string }>
  chapters: Array<{
    id: string
    title: string
    words: number
    volume_id: string | null
    volume_title: string | null
    deleted_at: string
  }>
}

interface ChapterListDto {
  id: string
  volume_id: string | null
  title: string
  idx: number
  words: number
  outline: string[]
  summary: string | null
  outline_note?: string
  outline_revision?: number
  outline_updated_at?: string | null
  body_needs_revision?: boolean
}

interface ChapterDto extends ChapterListDto {
  content_html: string
  rev: number
}

export interface ChapterSaveResult {
  rev: number
}

export interface BodyConflict {
  chapterId: string
  serverContentHtml: string
  serverRev: number
  clientContentHtml: string
}

export class BodyConflictError extends Error {
  constructor(public conflict: BodyConflict) {
    super('body_revision_conflict')
    this.name = 'BodyConflictError'
  }
}

interface OutlineDto {
  chapter_id: string
  title: string
  nodes: string[]
  note: string
  outline_revision: number
  outline_updated_at: string | null
  body_needs_revision: boolean
}

interface GuardIssueDto {
  id: string
  chapter_id: string
  issue_type: string
  severity: string
  description: string
  status: string
  resolved: boolean
  issue_rev: number
  confidence: number
  chapter_index: number
  chapter_title: string
  evidence: Array<{ label: string; text: string; accent?: boolean }>
  actions: string[]
  arbitration_status: GuardIssue['arbitrationStatus']
  arbitration_confidence: number | null
  arbitration_rationale: string | null
  updated_at: string
}

interface GuardOverviewDto {
  status: GuardOverview['status']
  queued: number
  running: number
  completed: number
  failed: number
  outbox_pending: number
  outbox_dead_letter: number
  latest_activity_at: string | null
  runs: Array<{
    chapter_id: string
    chapter_index: number
    chapter_title: string
    body_rev: number
    status: string
    phases: { extract: string; summary: string; scan: string }
    error_code: string | null
    error_detail: string | null
    updated_at: string
  }>
}

export interface CodexDto {
  id: string
  project_id: string
  kind: 'character' | 'location' | 'item' | 'faction' | 'event' | 'rule'
  name: string
  description: string
  aliases: string[]
  attrs: Record<string, unknown>
  resident: boolean
  status: 'confirmed' | 'pending'
  ref_chapters: string[]
  conflicts: string[]
  planted_at: string | null
  expected_by: string | null
}

const CODEX_KIND_FROM_DTO: Record<CodexDto['kind'], CodexKind> = {
  character: 'character',
  location: 'place',
  item: 'item',
  faction: 'faction',
  event: 'foreshadow',
  rule: 'system'
}

const CODEX_KIND_TO_DTO: Record<CodexKind, CodexDto['kind']> = {
  character: 'character',
  place: 'location',
  item: 'item',
  faction: 'faction',
  foreshadow: 'event',
  system: 'rule'
}

const revisions = new Map<string, number>()
const chapterCache = new Map<string, Chapter>()
const codexProjectIds = new Map<string, string>()

const ISSUE_LABELS: Record<string, string> = {
  alive_conflict: '生死状态冲突',
  ownership_conflict: '物品归属冲突',
  knowledge_boundary: '知情边界冲突'
}

const RESOLUTION_LABELS: Record<GuardResolutionAction, string> = {
  accept_old_fact: '保留原设定',
  accept_new_fact: '采用新事实',
  intentional_exception: '标记为有意例外',
  false_positive: '这是误报',
  fixed_in_body: '正文已修正',
  defer: '稍后处理'
}

export function guardIssueFromDto(dto: GuardIssueDto): GuardIssue {
  const actionCodes = dto.actions.filter((action): action is GuardResolutionAction => action in RESOLUTION_LABELS)
  return {
    id: dto.id,
    kind: 'conflict',
    severity: dto.severity === 'high' ? 'high' : 'mid',
    category: ISSUE_LABELS[dto.issue_type] ?? '一致性冲突',
    title: dto.description,
    chapterRef: `第 ${dto.chapter_index} 章 · ${dto.chapter_title}`,
    detail: `规则置信度 ${Math.round(dto.confidence * 100)}% · 扫描结果不会自动修改正文`,
    evidence: dto.evidence,
    actions: actionCodes.map((action) => RESOLUTION_LABELS[action]),
    actionCodes,
    issueRev: dto.issue_rev,
    chapterId: dto.chapter_id,
    resolved: dto.resolved,
    arbitrationStatus: dto.arbitration_status,
    arbitrationConfidence: dto.arbitration_confidence ?? undefined,
    arbitrationRationale: dto.arbitration_rationale ?? undefined
  }
}

export function guardOverviewFromDto(dto: GuardOverviewDto): GuardOverview {
  return {
    status: dto.status,
    queued: dto.queued,
    running: dto.running,
    completed: dto.completed,
    failed: dto.failed,
    outboxPending: dto.outbox_pending,
    outboxDeadLetter: dto.outbox_dead_letter,
    latestActivityAt: dto.latest_activity_at ?? undefined,
    runs: dto.runs.map((run) => ({
      chapterId: run.chapter_id,
      chapterIndex: run.chapter_index,
      chapterTitle: run.chapter_title,
      bodyRev: run.body_rev,
      status: run.status,
      phases: run.phases,
      errorCode: run.error_code ?? undefined,
      errorDetail: run.error_detail ?? undefined,
      updatedAt: run.updated_at
    }))
  }
}

function chapterNumber(value: string | null): number | undefined {
  if (!value) return undefined
  const match = value.match(/(\d+)$/)
  return match ? Number(match[1]) : undefined
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

export function codexFromDto(dto: CodexDto): CodexEntry {
  const nestedCharacter = dto.attrs.character
  const characterAttrs = nestedCharacter && typeof nestedCharacter === 'object'
    ? { ...dto.attrs, ...(nestedCharacter as Record<string, unknown>) }
    : dto.attrs
  const facts = Array.isArray(dto.attrs.facts)
    ? dto.attrs.facts.flatMap((fact) => {
      if (!fact || typeof fact !== 'object') return []
      const row = fact as Record<string, unknown>
      const label = stringValue(row.label)
      const value = stringValue(row.value)
      return label && value ? [{ label, value }] : []
    })
    : undefined
  const relations = Array.isArray(dto.attrs.relations)
    ? dto.attrs.relations.flatMap((relation) => {
      if (!relation || typeof relation !== 'object') return []
      const row = relation as Record<string, unknown>
      const name = stringValue(row.name)
      const relationName = stringValue(row.relation)
      const note = stringValue(row.note)
      if (!name || !relationName || !note) return []
      return [{
        targetId: stringValue(row.target_id) ?? stringValue(row.targetId),
        name,
        relation: relationName,
        note
      }]
    })
    : undefined
  const plantedAt = chapterNumber(dto.planted_at)
  const expectedChapter = chapterNumber(dto.expected_by)
  const arc = characterAttrs.arc && typeof characterAttrs.arc === 'object'
    ? characterAttrs.arc as Record<string, unknown>
    : undefined

  return {
    id: dto.id,
    kind: CODEX_KIND_FROM_DTO[dto.kind],
    name: dto.name,
    aliases: dto.aliases,
    summary: dto.description,
    resident: dto.resident,
    refChapters: dto.ref_chapters.flatMap((chapterId) => chapterNumber(chapterId) ?? []),
    status: dto.status,
    conflicts: dto.conflicts.length,
    character: dto.kind === 'character' ? {
      role: stringValue(characterAttrs.role),
      age: stringValue(characterAttrs.age),
      personality: Array.isArray(characterAttrs.personality)
        ? characterAttrs.personality.filter((value): value is string => typeof value === 'string')
        : undefined,
      ability: stringValue(characterAttrs.ability) ?? stringValue(characterAttrs.skills),
      limitation: stringValue(characterAttrs.limitation) ?? stringValue(characterAttrs.limits),
      desire: stringValue(characterAttrs.desire),
      motivation: stringValue(characterAttrs.motivation),
      flaw: stringValue(characterAttrs.flaw),
      fear: stringValue(characterAttrs.fear),
      appearance: stringValue(characterAttrs.appearance),
      speech: stringValue(characterAttrs.speech),
      background: stringValue(characterAttrs.background),
      currentState: stringValue(characterAttrs.current_state) ?? stringValue(characterAttrs.currentState),
      arc: arc ? {
        past: stringValue(arc.past),
        current: stringValue(arc.current),
        next: stringValue(arc.next)
      } : undefined
    } : undefined,
    facts,
    relations,
    plantedAt,
    expectedBy: dto.expected_by
      ? expectedChapter ? `第 ${expectedChapter} 章` : dto.expected_by
      : undefined,
    rawAttrs: structuredClone(dto.attrs)
  }
}

const CHARACTER_ATTR_KEYS = [
  'role', 'age', 'appearance', 'personality', 'desire', 'motivation', 'flaw',
  'fear', 'ability', 'skills', 'limitation', 'limits', 'speech', 'background',
  'current_state', 'currentState', 'arc', 'character'
] as const

function normalizedAliases(aliases: string[]): string[] {
  return [...new Set(aliases.map((alias) => alias.trim()).filter(Boolean))]
}

function assignText(target: Record<string, unknown>, key: string, value?: string) {
  const normalized = value?.trim()
  if (normalized) target[key] = normalized
}

export function codexDraftAttrs(draft: CodexEntryDraft, existing?: CodexEntry): Record<string, unknown> {
  // Pinia 会把 entry 变成 Proxy；structuredClone 不能克隆 Proxy。attrs 来自 JSON API，
  // 用 JSON round-trip 可安全取得普通对象，同时严格保持后端支持的数据类型边界。
  const attrs = JSON.parse(JSON.stringify(existing?.rawAttrs ?? {})) as Record<string, unknown>
  const nestedCharacter = attrs.character && typeof attrs.character === 'object'
    ? attrs.character as Record<string, unknown>
    : undefined
  const characterExtensions = nestedCharacter
    ? Object.fromEntries(Object.entries(nestedCharacter).filter(([key]) => !CHARACTER_ATTR_KEYS.includes(key as typeof CHARACTER_ATTR_KEYS[number])))
    : {}
  CHARACTER_ATTR_KEYS.forEach((key) => delete attrs[key])
  delete attrs.facts

  if (draft.kind === 'character') {
    const character = draft.character ?? {}
    assignText(attrs, 'role', character.role)
    assignText(attrs, 'age', character.age)
    assignText(attrs, 'appearance', character.appearance)
    assignText(attrs, 'desire', character.desire)
    assignText(attrs, 'motivation', character.motivation)
    assignText(attrs, 'flaw', character.flaw)
    assignText(attrs, 'fear', character.fear)
    assignText(attrs, 'ability', character.ability)
    assignText(attrs, 'limitation', character.limitation)
    assignText(attrs, 'speech', character.speech)
    assignText(attrs, 'background', character.background)
    assignText(attrs, 'current_state', character.currentState)
    const personality = character.personality?.map((item) => item.trim()).filter(Boolean)
    if (personality?.length) attrs.personality = personality
    const arc = Object.fromEntries(
      Object.entries(character.arc ?? {})
        .map(([key, value]) => [key, value?.trim()])
        .filter((entry): entry is [string, string] => Boolean(entry[1]))
    )
    if (Object.keys(arc).length) attrs.arc = arc
    if (Object.keys(characterExtensions).length) attrs.character = characterExtensions
  } else if (draft.facts?.length) {
    attrs.facts = draft.facts
      .map((fact) => ({ label: fact.label.trim(), value: fact.value.trim() }))
      .filter((fact) => fact.label && fact.value)
  }
  return attrs
}

function projectFromDto(dto: ProjectDto): Project {
  return {
    id: dto.id,
    title: dto.title,
    genre: dto.genre,
    status: dto.status,
    dailyGoal: dto.target_words_daily,
    dailyWords: 0,
    styleProfile: dto.style_profile_id,
    volumes: dto.volumes.map((volume, index) => ({
      id: volume.id,
      index: index + 1,
      title: volume.title,
      summary: volume.summary ?? undefined
    }))
  }
}

function chapterFromDto(dto: ChapterListDto, status: Chapter['status']): Chapter {
  return {
    id: dto.id,
    volumeId: dto.volume_id ?? '',
    index: dto.idx,
    title: dto.title,
    words: dto.words,
    status,
    outline: dto.outline ?? [],
    outlineNote: dto.outline_note ?? '',
    outlineRevision: dto.outline_revision ?? 0,
    outlineUpdatedAt: dto.outline_updated_at ?? undefined,
    bodyNeedsRevision: dto.body_needs_revision ?? false,
    summary: dto.summary ?? undefined
  }
}

export function htmlToDocument(html: string): Record<string, unknown> {
  const document = new DOMParser().parseFromString(html, 'text/html')
  const content = Array.from(document.body.children).map((element, index) => ({
    type: 'paragraph',
    attrs: {
      pid: element.getAttribute('data-paragraph-id') ?? `p-${index}`,
      ...(element.getAttribute('data-ai-run-id') ? { aiRunId: element.getAttribute('data-ai-run-id') } : {}),
      ...(element.getAttribute('data-ai-source-hash') ? { aiSourceHash: element.getAttribute('data-ai-source-hash') } : {})
    },
    content: element.textContent ? [{ type: 'text', text: element.textContent }] : []
  }))
  return { type: 'doc', content }
}

export function bodyConflictFromError(chapterId: string, error: ApiError): BodyConflict | null {
  if (error.status !== 409) return null
  try {
    const response = JSON.parse(error.message) as { detail?: Record<string, unknown> }
    const detail = response.detail
    if (!detail || detail.conflict !== true || typeof detail.server_rev !== 'number') return null
    if (typeof detail.server_content_html !== 'string' || typeof detail.client_content_html !== 'string') return null
    return {
      chapterId,
      serverContentHtml: detail.server_content_html,
      serverRev: detail.server_rev,
      clientContentHtml: detail.client_content_html
    }
  } catch {
    return null
  }
}

const realApi = {
  async getProject(projectId = 'p1'): Promise<Project> {
    return projectFromDto(await request<ProjectDto>(`/projects/${projectId}`))
  },

  async listChapters(projectId = 'p1'): Promise<Chapter[]> {
    const rows = await request<ChapterListDto[]>(`/projects/${projectId}/chapters`)
    const chapters = rows.map((row, index) =>
      chapterFromDto(row, row.words === 0 ? 'outlined' : index === rows.length - 1 ? 'drafting' : 'done')
    )
    chapters.forEach((chapter) => chapterCache.set(chapter.id, chapter))
    return chapters
  },

  async getChapter(id: string): Promise<Chapter | undefined> {
    const dto = await request<ChapterDto>(`/chapters/${id}`)
    revisions.set(id, dto.rev)
    const chapter = { ...chapterFromDto(dto, dto.words > 0 ? 'done' : 'outlined'), content: dto.content_html, rev: dto.rev }
    chapterCache.set(id, chapter)
    return chapter
  },

  async saveChapter(id: string, patch: Partial<Chapter>): Promise<ChapterSaveResult> {
    const content = patch.content ?? ''
    try {
      const result = await request<ChapterSaveResult>(`/chapters/${id}/body`, {
        method: 'PUT',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({
          content_html: content,
          content_json: htmlToDocument(content),
          base_rev: revisions.get(id) ?? patch.rev ?? 0
        })
      })
      revisions.set(id, result.rev)
      const cached = chapterCache.get(id)
      if (cached) cached.rev = result.rev
      return result
    } catch (error) {
      if (error instanceof ApiError) {
        const conflict = bodyConflictFromError(id, error)
        if (conflict) {
          revisions.set(id, conflict.serverRev)
          throw new BodyConflictError(conflict)
        }
      }
      throw error
    }
  },

  async updateChapterPlan(id: string, patch: ChapterPlanPatch): Promise<Chapter | undefined> {
    try {
      const dto = await request<OutlineDto>(`/chapters/${id}/outline`, {
        method: 'PUT',
        body: JSON.stringify({
          title: patch.title,
          nodes: patch.outline,
          note: patch.outlineNote,
          base_outline_revision: patch.baseRevision,
          body_policy: patch.bodyNeedsRevision ? 'mark_body_for_revision' : 'plan_only'
        })
      })
      const previous = chapterCache.get(id)
      if (!previous) return undefined
      const updated: Chapter = {
        ...previous,
        title: dto.title,
        outline: dto.nodes,
        outlineNote: dto.note,
        outlineRevision: dto.outline_revision,
        outlineUpdatedAt: dto.outline_updated_at ?? undefined,
        bodyNeedsRevision: dto.body_needs_revision
      }
      chapterCache.set(id, updated)
      return updated
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new Error('outline_revision_conflict')
      throw error
    }
  },

  async insertChapter(projectId: string, volumeId: string, afterIndex: number): Promise<Chapter> {
    const dto = await request<ChapterListDto>(`/projects/${projectId}/chapters`, {
      method: 'POST',
      body: JSON.stringify({ volume_id: volumeId, after_index: afterIndex })
    })
    const chapter = chapterFromDto(dto, 'outlined')
    chapterCache.set(chapter.id, chapter)
    return chapter
  },

  async updateProject(projectId: string, patch: ProjectPatch): Promise<Project> {
    return projectFromDto(await request<ProjectDto>(`/projects/${projectId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        ...(patch.title !== undefined ? { title: patch.title } : {}),
        ...(patch.genre !== undefined ? { genre: patch.genre } : {}),
        ...(patch.status !== undefined ? { status: patch.status } : {}),
        ...(patch.dailyGoal !== undefined ? { target_words_daily: patch.dailyGoal } : {})
      })
    }))
  },

  async createVolume(projectId: string, title: string, summary = ''): Promise<Volume> {
    const dto = await request<{ id: string; title: string; idx: number; summary: string | null }>(`/projects/${projectId}/volumes`, {
      method: 'POST', body: JSON.stringify({ title, summary })
    })
    return { id: dto.id, index: 0, title: dto.title, summary: dto.summary ?? undefined }
  },

  async updateVolume(projectId: string, volumeId: string, patch: { title?: string; summary?: string }): Promise<void> {
    await request(`/projects/${projectId}/volumes/${volumeId}`, { method: 'PATCH', body: JSON.stringify(patch) })
  },

  async reorderVolumes(projectId: string, volumeIds: string[]): Promise<void> {
    await request(`/projects/${projectId}/volumes/order`, {
      method: 'PUT', body: JSON.stringify({ volume_ids: volumeIds })
    })
  },

  async moveChapter(projectId: string, chapterId: string, volumeId: string, placement: 'first' | 'last' | 'after', afterChapterId?: string): Promise<void> {
    await request(`/projects/${projectId}/chapters/${chapterId}/position`, {
      method: 'PUT',
      body: JSON.stringify({ volume_id: volumeId, placement, after_chapter_id: afterChapterId })
    })
  },

  async trashChapter(projectId: string, chapterId: string): Promise<void> {
    await request(`/projects/${projectId}/chapters/${chapterId}`, { method: 'DELETE' })
  },

  async trashVolume(projectId: string, volumeId: string, targetVolumeId?: string): Promise<void> {
    const query = targetVolumeId ? `?target_volume_id=${encodeURIComponent(targetVolumeId)}` : ''
    await request(`/projects/${projectId}/volumes/${volumeId}${query}`, { method: 'DELETE' })
  },

  async getTrash(projectId: string): Promise<ProjectTrash> {
    const dto = await request<TrashDto>(`/projects/${projectId}/trash`)
    return {
      volumes: dto.volumes.map((item) => ({ id: item.id, title: item.title, deletedAt: item.deleted_at })),
      chapters: dto.chapters.map((item) => ({
        id: item.id, title: item.title, words: item.words, volumeId: item.volume_id,
        volumeTitle: item.volume_title, deletedAt: item.deleted_at
      }))
    }
  },

  async restoreVolume(projectId: string, volumeId: string): Promise<void> {
    await request(`/projects/${projectId}/trash/volumes/${volumeId}/restore`, { method: 'POST' })
  },

  async restoreChapter(projectId: string, chapterId: string, volumeId?: string): Promise<void> {
    await request(`/projects/${projectId}/trash/chapters/${chapterId}/restore`, {
      method: 'POST', body: JSON.stringify({ volume_id: volumeId })
    })
  },

  async deleteTrashItem(projectId: string, kind: 'volumes' | 'chapters', id: string): Promise<void> {
    await request(`/projects/${projectId}/trash/${kind}/${id}`, { method: 'DELETE' })
  },

  async listCodex(projectId = 'p1'): Promise<CodexEntry[]> {
    const rows = await request<CodexDto[]>(`/codex/${projectId}/entries`)
    rows.forEach((row) => codexProjectIds.set(row.id, projectId))
    return rows.map(codexFromDto)
  },
  async createCodexEntry(projectId: string, draft: CodexEntryDraft): Promise<CodexEntry> {
    const dto = await request<CodexDto>(`/codex/${projectId}/entries`, {
      method: 'POST',
      body: JSON.stringify({
        kind: CODEX_KIND_TO_DTO[draft.kind],
        name: draft.name.trim(),
        description: draft.summary.trim(),
        aliases: normalizedAliases(draft.aliases),
        attrs: codexDraftAttrs(draft),
        resident: draft.resident,
        status: draft.status
      })
    })
    codexProjectIds.set(dto.id, projectId)
    return codexFromDto(dto)
  },
  async updateCodexEntry(id: string, draft: CodexEntryDraft, existing: CodexEntry): Promise<CodexEntry> {
    const projectId = codexProjectIds.get(id)
    if (!projectId) throw new Error('codex_entry_not_loaded')
    const aliases = normalizedAliases(draft.aliases)
    const dto = await request<CodexDto>(`/codex/${projectId}/entries/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        kind: CODEX_KIND_TO_DTO[draft.kind],
        name: draft.name.trim(),
        description: draft.summary.trim(),
        attrs: codexDraftAttrs(draft, existing),
        resident: draft.resident,
        status: draft.status,
        aliases
      })
    })
    return codexFromDto(dto)
  },
  async confirmCodexEntry(id: string): Promise<void> {
    const projectId = codexProjectIds.get(id)
    if (!projectId) throw new Error('codex_entry_not_loaded')
    await request(`/codex/${projectId}/entries/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'confirmed' })
    })
  },
  async dropCodexEntry(id: string): Promise<void> {
    const projectId = codexProjectIds.get(id)
    if (!projectId) throw new Error('codex_entry_not_loaded')
    try {
      await request(`/codex/${projectId}/entries/${id}`, { method: 'DELETE' })
      codexProjectIds.delete(id)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new Error('codex_entry_in_use')
      throw error
    }
  },
  async listGuardIssues(projectId: string): Promise<GuardIssue[]> {
    const rows = await request<GuardIssueDto[]>(`/consistency/issues/${projectId}`)
    return rows.map(guardIssueFromDto)
  },
  async getGuardOverview(projectId: string): Promise<GuardOverview> {
    return guardOverviewFromDto(await request<GuardOverviewDto>(`/consistency/projects/${projectId}/overview`))
  },
  async scanProject(projectId: string): Promise<{ queued: number; run_ids: number[] }> {
    return request(`/consistency/projects/${projectId}/scan`, { method: 'POST' })
  },
  async resolveGuardIssue(projectId: string, id: string, issueRev: number, action: GuardResolutionAction): Promise<void> {
    await request(`/consistency/issues/${projectId}/${id}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ action, issue_rev: issueRev })
    })
  },
  async getContextLayers(_projectId: string, chapterId: string): Promise<ContextLayer[]> {
    const result = await request<{ layers: ContextLayer[] }>(`/generate/context/${chapterId}`)
    return result.layers
  },
  draftParagraphs: []
}

export const contentApi = USE_MOCK ? mockApi : realApi
