import { ApiError, USE_MOCK, request } from './http'
import { mockApi } from './mock'
import type { Chapter, ChapterPlanPatch, CodexEntry, CodexKind, ContextLayer, Project } from '@/types'

interface ProjectDto {
  id: string
  title: string
  target_words_daily: number
  style_profile_id: string | null
  volumes: Array<{ id: string; title: string; idx: number; summary?: string | null }>
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

interface OutlineDto {
  chapter_id: string
  title: string
  nodes: string[]
  note: string
  outline_revision: number
  outline_updated_at: string | null
  body_needs_revision: boolean
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

const revisions = new Map<string, number>()
const chapterCache = new Map<string, Chapter>()
const codexProjectIds = new Map<string, string>()

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
      currentState: stringValue(characterAttrs.current_state) ?? stringValue(characterAttrs.currentState)
    } : undefined,
    facts,
    relations,
    plantedAt,
    expectedBy: dto.expected_by
      ? expectedChapter ? `第 ${expectedChapter} 章` : dto.expected_by
      : undefined
  }
}

function projectFromDto(dto: ProjectDto): Project {
  return {
    id: dto.id,
    title: dto.title,
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
    attrs: { pid: element.getAttribute('data-paragraph-id') ?? `p-${index}` },
    content: element.textContent ? [{ type: 'text', text: element.textContent }] : []
  }))
  return { type: 'doc', content }
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

  async saveChapter(id: string, patch: Partial<Chapter>): Promise<void> {
    const content = patch.content ?? ''
    const result = await request<{ rev: number }>(`/chapters/${id}/body`, {
      method: 'PUT',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        content_html: content,
        content_json: htmlToDocument(content),
        base_rev: revisions.get(id) ?? patch.rev ?? 0
      })
    })
    revisions.set(id, result.rev)
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

  async listCodex(projectId = 'p1'): Promise<CodexEntry[]> {
    const rows = await request<CodexDto[]>(`/codex/${projectId}/entries`)
    rows.forEach((row) => codexProjectIds.set(row.id, projectId))
    return rows.map(codexFromDto)
  },
  async confirmCodexEntry(id: string): Promise<void> {
    const projectId = codexProjectIds.get(id)
    if (!projectId) throw new Error('codex_entry_not_loaded')
    await request(`/codex/${projectId}/entries/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'confirmed' })
    })
  },
  async dropCodexEntry(): Promise<void> {
    throw new Error('real_codex_drop_not_connected')
  },
  async listGuardIssues() { return [] },
  async resolveGuardIssue() {},
  async getContextLayers(_projectId: string, chapterId: string): Promise<ContextLayer[]> {
    const result = await request<{ layers: ContextLayer[] }>(`/generate/context/${chapterId}`)
    return result.layers
  },
  draftParagraphs: []
}

export const contentApi = USE_MOCK ? mockApi : realApi
