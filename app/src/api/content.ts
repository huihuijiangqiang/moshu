import { ApiError, USE_MOCK, request } from './http'
import { mockApi } from './mock'
import type { Chapter, ChapterPlanPatch, ChapterVersionDetail, ChapterVersionRestoreResult, ChapterVersionSummary, CharacterStatistics, CodexEntry, CodexEntryDraft, CodexKind, CodexRelation, CodexRelationDraft, CodexStateDraft, CodexStateHistoryItem, CodexStateSource, ContextLayer, GuardIssue, GuardOverview, GuardResolutionAction, Project, ProjectPatch, ProjectTrash, TemporalDecisionResult, TemporalReviewItem, TimelineBoard, TimelineEntry, TimelineEntryDraft, TimelinePlacementStatus, TimelineReflowResult, Volume } from '@/types'

interface ProjectDto {
  id: string
  org_id: string | null
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
  pov_entry_id?: string | null
  pov_revision?: number
  outline_note?: string
  outline_revision?: number
  outline_updated_at?: string | null
  body_needs_revision?: boolean
}

interface CharacterStatisticsDto {
  entry_id: string
  name: string
  appearance_chapters: number
  explicit_references: number
  extracted_claims: number
  pov_chapters: number
  pov_words: number
  first_appearance: number | null
  last_appearance: number | null
  hiatus_chapters: number | null
  chapters: Array<{
    chapter_id: string
    chapter_index: number
    chapter_title: string
    words: number
    explicit_references: number
    extracted_claims: number
    is_pov: boolean
  }>
}

interface ChapterDto extends ChapterListDto {
  content_html: string
  rev: number
}

interface ChapterVersionSummaryDto {
  id: number
  rev: number
  trigger: string
  words: number
  excerpt: string
  created_at: string
  is_current: boolean
}

interface ChapterVersionDetailDto extends ChapterVersionSummaryDto {
  content_html: string
  content_json: Record<string, unknown>
}

interface ChapterVersionRestoreDto {
  rev: number
  restored_from_rev: number
  content_html: string
  content_json: Record<string, unknown>
  words: number
  consistency_status: string
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

interface TimelineReflowDto {
  claims_examined: number
  claims_changed: number
  affected_chapter_ids: string[]
  resolved: number
  unresolved: number
  ambiguous: number
  cyclic: number
  cycles: string[][]
  rescans_queued: number
  rescan_run_ids: number[]
}

interface TimelineBoardEventDto {
  event_id: string
  source: 'extracted' | 'planned'
  claim_id: number | null
  entry_id: string | null
  timeline_id: string
  event_ref: string
  detail: string | null
  chapter_id: string | null
  chapter_index: number | null
  chapter_title: string | null
  time_text: string | null
  story_order: number | null
  placement_status: TimelinePlacementStatus
  dependency_status: string
  relation: string | null
  relation_ref: string | null
  source_anchor: string | null
  confidence: number | null
  resolution_source: string | null
  time_start: string | null
  time_end: string | null
  editable: boolean
  revision: number | null
}

interface TimelineBoardDto {
  lanes: Array<{
    timeline_id: string
    label: string
    event_count: number
    placed_count: number
    review_count: number
    events: TimelineBoardEventDto[]
  }>
  event_count: number
  placed_count: number
  review_count: number
  unplaced_count: number
  story_order_min: number | null
  story_order_max: number | null
}

interface TimelineEntryDto {
  id: string
  project_id: string
  chapter_id: string | null
  timeline_id: string
  title: string
  detail: string | null
  time_text: string | null
  story_order: number | null
  time_start: string | null
  time_end: string | null
  status: 'active' | 'archived'
  rev: number
  created_at: string
  updated_at: string
}

interface TemporalReviewItemDto {
  claim_id: number
  chapter_id: string | null
  chapter_index: number | null
  chapter_title: string | null
  event_ref: string | null
  relation: 'before' | 'after' | 'simultaneous' | null
  relation_ref: string | null
  original: string
  normalized: string
  offset_min_seconds: number
  offset_max_seconds: number
  dependency_status: string
  override_seconds: number | null
  override_version: number
}

interface TemporalDecisionDto {
  item: TemporalReviewItemDto
  reflow: TimelineReflowDto
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
  foreshadow_resolved?: boolean
  resolved_at?: string | null
  relations?: Array<{
    id: string
    target_id: string
    target_name: string
    target_kind: CodexDto['kind']
    relation_type: string
    description: string | null
    direction: 'outgoing' | 'incoming'
  }>
}

export interface CodexStateHistoryItemDto {
  id: string
  source: CodexStateSource
  editable: boolean
  state_key: string
  value: string
  polarity: 'positive' | 'negative'
  note: string | null
  chapter_id: string
  chapter_index: number
  chapter_title: string
  body_revision: number | null
  paragraph_id: string | null
  confidence: number | null
  revision: number | null
  created_at: string
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
  knowledge_boundary: '知情边界冲突',
  timeline_conflict: '时间线冲突',
  ability_boundary: '能力边界冲突',
  location_conflict: '地点冲突',
  foreshadow_overdue: '伏笔逾期'
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

export function timelineReflowFromDto(dto: TimelineReflowDto): TimelineReflowResult {
  return {
    claimsExamined: dto.claims_examined,
    claimsChanged: dto.claims_changed,
    affectedChapterIds: dto.affected_chapter_ids,
    resolved: dto.resolved,
    unresolved: dto.unresolved,
    ambiguous: dto.ambiguous,
    cyclic: dto.cyclic,
    cycles: dto.cycles,
    rescansQueued: dto.rescans_queued,
    rescanRunIds: dto.rescan_run_ids
  }
}

export function timelineBoardFromDto(dto: TimelineBoardDto): TimelineBoard {
  return {
    lanes: dto.lanes.map((lane) => ({
      timelineId: lane.timeline_id,
      label: lane.label,
      eventCount: lane.event_count,
      placedCount: lane.placed_count,
      reviewCount: lane.review_count,
      events: lane.events.map((event) => ({
        eventId: event.event_id,
        source: event.source,
        claimId: event.claim_id ?? undefined,
        entryId: event.entry_id ?? undefined,
        timelineId: event.timeline_id,
        eventRef: event.event_ref,
        detail: event.detail ?? undefined,
        chapterId: event.chapter_id ?? undefined,
        chapterIndex: event.chapter_index ?? undefined,
        chapterTitle: event.chapter_title ?? undefined,
        timeText: event.time_text ?? undefined,
        storyOrder: event.story_order ?? undefined,
        placementStatus: event.placement_status,
        dependencyStatus: event.dependency_status,
        relation: event.relation ?? undefined,
        relationRef: event.relation_ref ?? undefined,
        sourceAnchor: event.source_anchor ?? undefined,
        confidence: event.confidence ?? undefined,
        resolutionSource: event.resolution_source ?? undefined,
        timeStart: event.time_start ?? undefined,
        timeEnd: event.time_end ?? undefined,
        editable: event.editable,
        revision: event.revision ?? undefined
      }))
    })),
    eventCount: dto.event_count,
    placedCount: dto.placed_count,
    reviewCount: dto.review_count,
    unplacedCount: dto.unplaced_count,
    storyOrderMin: dto.story_order_min ?? undefined,
    storyOrderMax: dto.story_order_max ?? undefined
  }
}

function timelineEntryFromDto(dto: TimelineEntryDto): TimelineEntry {
  return {
    id: dto.id,
    projectId: dto.project_id,
    chapterId: dto.chapter_id ?? undefined,
    timelineId: dto.timeline_id,
    title: dto.title,
    detail: dto.detail ?? undefined,
    timeText: dto.time_text ?? undefined,
    storyOrder: dto.story_order ?? undefined,
    timeStart: dto.time_start ?? undefined,
    timeEnd: dto.time_end ?? undefined,
    status: dto.status,
    rev: dto.rev,
    createdAt: dto.created_at,
    updatedAt: dto.updated_at
  }
}

function timelineEntryPayload(draft: TimelineEntryDraft) {
  return {
    title: draft.title,
    detail: draft.detail || null,
    timeline_id: draft.timelineId,
    chapter_id: draft.chapterId || null,
    time_text: draft.timeText || null,
    story_order: draft.storyOrder ?? null,
    time_start: draft.timeStart || null,
    time_end: draft.timeEnd || null
  }
}

export function temporalReviewFromDto(dto: TemporalReviewItemDto): TemporalReviewItem {
  return {
    claimId: dto.claim_id,
    chapterId: dto.chapter_id ?? undefined,
    chapterIndex: dto.chapter_index ?? undefined,
    chapterTitle: dto.chapter_title ?? undefined,
    eventRef: dto.event_ref ?? undefined,
    relation: dto.relation ?? undefined,
    relationRef: dto.relation_ref ?? undefined,
    original: dto.original,
    normalized: dto.normalized,
    offsetMinSeconds: dto.offset_min_seconds,
    offsetMaxSeconds: dto.offset_max_seconds,
    dependencyStatus: dto.dependency_status,
    overrideSeconds: dto.override_seconds ?? undefined,
    overrideVersion: dto.override_version
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
  const legacyRelations = Array.isArray(dto.attrs.relations)
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
        note,
        direction: 'outgoing' as const
      }]
    })
    : undefined
  const relations = dto.relations !== undefined
    ? dto.relations.map((relation) => ({
      id: relation.id,
      targetId: relation.target_id,
      targetKind: CODEX_KIND_FROM_DTO[relation.target_kind],
      direction: relation.direction,
      name: relation.target_name,
      relation: relation.relation_type,
      note: relation.description ?? undefined
    }))
    : legacyRelations
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
    plantedChapterId: dto.planted_at ?? undefined,
    expectedChapterId: dto.expected_by ?? undefined,
    foreshadowResolved: dto.foreshadow_resolved ?? false,
    resolvedChapterId: dto.resolved_at ?? undefined,
    expectedBy: dto.expected_by
      ? expectedChapter ? `第 ${expectedChapter} 章` : dto.expected_by
      : undefined,
    rawAttrs: structuredClone(dto.attrs)
  }
}

export function codexStateFromDto(dto: CodexStateHistoryItemDto): CodexStateHistoryItem {
  return {
    id: dto.id,
    source: dto.source,
    editable: dto.editable,
    stateKey: dto.state_key,
    value: dto.value,
    polarity: dto.polarity,
    note: dto.note ?? undefined,
    chapterId: dto.chapter_id,
    chapterIndex: dto.chapter_index,
    chapterTitle: dto.chapter_title,
    bodyRevision: dto.body_revision ?? undefined,
    paragraphId: dto.paragraph_id ?? undefined,
    confidence: dto.confidence ?? undefined,
    revision: dto.revision ?? undefined,
    createdAt: dto.created_at
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
    orgId: dto.org_id,
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
    summary: dto.summary ?? undefined,
    povEntryId: dto.pov_entry_id ?? undefined,
    povRevision: dto.pov_revision ?? 0
  }
}

export function characterStatisticsFromDto(dto: CharacterStatisticsDto): CharacterStatistics {
  return {
    entryId: dto.entry_id,
    name: dto.name,
    appearanceChapters: dto.appearance_chapters,
    explicitReferences: dto.explicit_references,
    extractedClaims: dto.extracted_claims,
    povChapters: dto.pov_chapters,
    povWords: dto.pov_words,
    firstAppearance: dto.first_appearance ?? undefined,
    lastAppearance: dto.last_appearance ?? undefined,
    hiatusChapters: dto.hiatus_chapters ?? undefined,
    chapters: dto.chapters.map((chapter) => ({
      chapterId: chapter.chapter_id,
      chapterIndex: chapter.chapter_index,
      chapterTitle: chapter.chapter_title,
      words: chapter.words,
      explicitReferences: chapter.explicit_references,
      extractedClaims: chapter.extracted_claims,
      isPov: chapter.is_pov
    }))
  }
}

function chapterVersionSummaryFromDto(dto: ChapterVersionSummaryDto): ChapterVersionSummary {
  return {
    id: dto.id,
    rev: dto.rev,
    trigger: dto.trigger,
    words: dto.words,
    excerpt: dto.excerpt,
    createdAt: dto.created_at,
    isCurrent: dto.is_current
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

  async updateChapterPov(
    id: string,
    entryId: string | undefined,
    expectedRevision: number
  ): Promise<{ entryId?: string; revision: number }> {
    try {
      const dto = await request<{ entry_id: string | null; revision: number }>(`/chapters/${id}/pov`, {
        method: 'PUT',
        body: JSON.stringify({ entry_id: entryId ?? null, expected_revision: expectedRevision })
      })
      return { entryId: dto.entry_id ?? undefined, revision: dto.revision }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new Error('pov_revision_conflict')
      throw error
    }
  },

  async getCharacterStatistics(projectId: string, entryId: string): Promise<CharacterStatistics> {
    const dto = await request<CharacterStatisticsDto>(
      `/codex/${projectId}/entries/${entryId}/statistics`
    )
    return characterStatisticsFromDto(dto)
  },

  async listChapterVersions(id: string): Promise<ChapterVersionSummary[]> {
    const rows = await request<ChapterVersionSummaryDto[]>(`/chapters/${id}/versions`)
    return rows.map(chapterVersionSummaryFromDto)
  },

  async getChapterVersion(id: string, rev: number): Promise<ChapterVersionDetail> {
    const dto = await request<ChapterVersionDetailDto>(`/chapters/${id}/versions/${rev}`)
    return {
      ...chapterVersionSummaryFromDto(dto),
      content: dto.content_html,
      contentJson: dto.content_json
    }
  },

  async restoreChapterVersion(id: string, rev: number): Promise<ChapterVersionRestoreResult> {
    try {
      const dto = await request<ChapterVersionRestoreDto>(`/chapters/${id}/versions/${rev}/restore`, {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ base_rev: revisions.get(id) ?? 0 })
      })
      revisions.set(id, dto.rev)
      const cached = chapterCache.get(id)
      if (cached) Object.assign(cached, { content: dto.content_html, rev: dto.rev, words: dto.words })
      return {
        rev: dto.rev,
        restoredFromRev: dto.restored_from_rev,
        content: dto.content_html,
        contentJson: dto.content_json,
        words: dto.words,
        consistencyStatus: dto.consistency_status
      }
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
        status: draft.status,
        planted_at: draft.kind === 'foreshadow' ? draft.plantedChapterId : undefined,
        expected_by: draft.kind === 'foreshadow' ? draft.expectedChapterId ?? null : undefined,
        foreshadow_resolved: draft.foreshadowResolved,
        resolved_at: draft.kind === 'foreshadow' ? draft.resolvedChapterId ?? null : undefined
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
        aliases,
        planted_at: draft.kind === 'foreshadow' ? draft.plantedChapterId : undefined,
        expected_by: draft.kind === 'foreshadow' ? draft.expectedChapterId ?? null : undefined,
        foreshadow_resolved: draft.foreshadowResolved,
        resolved_at: draft.kind === 'foreshadow' ? draft.resolvedChapterId ?? null : undefined
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
  async createCodexRelation(projectId: string, entryId: string, draft: CodexRelationDraft): Promise<CodexRelation> {
    try {
      const dto = await request<NonNullable<CodexDto['relations']>[number]>(`/codex/${projectId}/entries/${entryId}/relations`, {
        method: 'POST',
        body: JSON.stringify({
          target_id: draft.targetId,
          relation_type: draft.relation.trim(),
          description: draft.note?.trim() || null
        })
      })
      return {
        id: dto.id, targetId: dto.target_id, targetKind: CODEX_KIND_FROM_DTO[dto.target_kind],
        direction: dto.direction, name: dto.target_name, relation: dto.relation_type,
        note: dto.description ?? undefined
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new Error('codex_relation_duplicate')
      if (error instanceof ApiError && error.status === 422) throw new Error('invalid_codex_relation')
      throw error
    }
  },
  async updateCodexRelation(projectId: string, entryId: string, relationId: string, draft: CodexRelationDraft): Promise<CodexRelation> {
    try {
      const dto = await request<NonNullable<CodexDto['relations']>[number]>(`/codex/${projectId}/entries/${entryId}/relations/${relationId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          target_id: draft.targetId,
          relation_type: draft.relation.trim(),
          description: draft.note?.trim() || null
        })
      })
      return {
        id: dto.id, targetId: dto.target_id, targetKind: CODEX_KIND_FROM_DTO[dto.target_kind],
        direction: dto.direction, name: dto.target_name, relation: dto.relation_type,
        note: dto.description ?? undefined
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new Error('codex_relation_duplicate')
      if (error instanceof ApiError && error.status === 422) throw new Error('invalid_codex_relation')
      throw error
    }
  },
  async deleteCodexRelation(projectId: string, entryId: string, relationId: string): Promise<void> {
    await request(`/codex/${projectId}/entries/${entryId}/relations/${relationId}`, { method: 'DELETE' })
  },
  async listCodexStateHistory(projectId: string, entryId: string): Promise<CodexStateHistoryItem[]> {
    const rows = await request<CodexStateHistoryItemDto[]>(`/codex/${projectId}/entries/${entryId}/states`)
    return rows.map(codexStateFromDto)
  },
  async createCodexStateChange(projectId: string, entryId: string, draft: CodexStateDraft): Promise<CodexStateHistoryItem> {
    try {
      const dto = await request<CodexStateHistoryItemDto>(`/codex/${projectId}/entries/${entryId}/states`, {
        method: 'POST',
        body: JSON.stringify({
          chapter_id: draft.chapterId,
          state_key: draft.stateKey,
          value: draft.value,
          note: draft.note ?? null
        })
      })
      return codexStateFromDto(dto)
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) throw new Error('invalid_codex_state')
      throw error
    }
  },
  async updateCodexStateChange(projectId: string, entryId: string, changeId: string, expectedRevision: number, draft: CodexStateDraft): Promise<CodexStateHistoryItem> {
    try {
      const dto = await request<CodexStateHistoryItemDto>(`/codex/${projectId}/entries/${entryId}/states/${changeId}`, {
        method: 'PUT',
        body: JSON.stringify({
          chapter_id: draft.chapterId,
          state_key: draft.stateKey,
          value: draft.value,
          note: draft.note ?? null,
          expected_revision: expectedRevision
        })
      })
      return codexStateFromDto(dto)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new Error('codex_state_revision_conflict')
      if (error instanceof ApiError && error.status === 422) throw new Error('invalid_codex_state')
      throw error
    }
  },
  async deleteCodexStateChange(projectId: string, entryId: string, changeId: string, expectedRevision: number): Promise<void> {
    try {
      await request(`/codex/${projectId}/entries/${entryId}/states/${changeId}`, {
        method: 'DELETE', body: JSON.stringify({ expected_revision: expectedRevision })
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new Error('codex_state_revision_conflict')
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
  async reflowProjectTimeline(projectId: string): Promise<TimelineReflowResult> {
    const dto = await request<TimelineReflowDto>(`/consistency/projects/${projectId}/timeline/reflow`, { method: 'POST' })
    return timelineReflowFromDto(dto)
  },
  async getTimelineBoard(projectId: string): Promise<TimelineBoard> {
    return timelineBoardFromDto(await request<TimelineBoardDto>(`/consistency/projects/${projectId}/timeline/board`))
  },
  async createTimelineEntry(projectId: string, draft: TimelineEntryDraft): Promise<TimelineEntry> {
    const dto = await request<TimelineEntryDto>(`/consistency/projects/${projectId}/timeline/entries`, {
      method: 'POST', body: JSON.stringify(timelineEntryPayload(draft))
    })
    return timelineEntryFromDto(dto)
  },
  async updateTimelineEntry(projectId: string, entryId: string, expectedRev: number, draft: TimelineEntryDraft): Promise<TimelineEntry> {
    const dto = await request<TimelineEntryDto>(`/consistency/projects/${projectId}/timeline/entries/${entryId}`, {
      method: 'PUT', body: JSON.stringify({ ...timelineEntryPayload(draft), expected_rev: expectedRev })
    })
    return timelineEntryFromDto(dto)
  },
  async archiveTimelineEntry(projectId: string, entryId: string, expectedRev: number): Promise<TimelineEntry> {
    const dto = await request<TimelineEntryDto>(`/consistency/projects/${projectId}/timeline/entries/${entryId}`, {
      method: 'DELETE', body: JSON.stringify({ expected_rev: expectedRev })
    })
    return timelineEntryFromDto(dto)
  },
  async listTemporalReviews(projectId: string): Promise<TemporalReviewItem[]> {
    const rows = await request<TemporalReviewItemDto[]>(`/consistency/projects/${projectId}/timeline/reviews`)
    return rows.map(temporalReviewFromDto)
  },
  async decideTemporalReview(
    projectId: string,
    claimId: number,
    action: 'confirm' | 'clear',
    expectedVersion: number,
    offsetSeconds?: number
  ): Promise<TemporalDecisionResult> {
    const dto = await request<TemporalDecisionDto>(`/consistency/projects/${projectId}/timeline/reviews/${claimId}`, {
      method: 'POST',
      body: JSON.stringify({ action, expected_version: expectedVersion, offset_seconds: offsetSeconds })
    })
    return { item: temporalReviewFromDto(dto.item), reflow: timelineReflowFromDto(dto.reflow) }
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
