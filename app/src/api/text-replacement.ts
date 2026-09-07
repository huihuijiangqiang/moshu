import { ApiError, request } from './http'
import type { TextReplacementPreview, TextReplacementRun, TextReplacementSpec } from '@/types'

interface PreviewDto {
  preview_token: string
  total_matches: number
  chapters: Array<{ id: string; title: string; index: number; volume_id: string | null; rev: number; match_count: number }>
  matches: Array<{
    id: string
    chapter_id: string
    chapter_title: string
    chapter_index: number
    paragraph_id: string | null
    before: string
    matched: string
    after: string
    replacement: string
  }>
  warnings: Array<{
    entry_id: string
    name: string
    kind: string
    matched_term: string
    referenced_chapters: number
  }>
}

interface RunDto {
  id: string
  status: 'applied' | 'undone'
  total_matches?: number
  affected_chapters: Array<{
    chapter_id: string
    chapter_title: string
    before_rev: number
    after_rev: number
    match_count: number
  }>
}

function payload(spec: TextReplacementSpec) {
  return {
    query: spec.query,
    replacement: spec.replacement,
    scope: spec.scope,
    chapter_id: spec.chapterId,
    volume_id: spec.volumeId,
    case_sensitive: spec.caseSensitive
  }
}

function previewFromDto(dto: PreviewDto): TextReplacementPreview {
  return {
    previewToken: dto.preview_token,
    totalMatches: dto.total_matches,
    chapters: dto.chapters.map((item) => ({
      id: item.id,
      title: item.title,
      index: item.index,
      volumeId: item.volume_id ?? undefined,
      rev: item.rev,
      matchCount: item.match_count
    })),
    matches: dto.matches.map((item) => ({
      id: item.id,
      chapterId: item.chapter_id,
      chapterTitle: item.chapter_title,
      chapterIndex: item.chapter_index,
      paragraphId: item.paragraph_id ?? undefined,
      before: item.before,
      matched: item.matched,
      after: item.after,
      replacement: item.replacement
    })),
    warnings: dto.warnings.map((item) => ({
      entryId: item.entry_id,
      name: item.name,
      kind: item.kind,
      matchedTerm: item.matched_term,
      referencedChapters: item.referenced_chapters
    }))
  }
}

function runFromDto(dto: RunDto): TextReplacementRun {
  return {
    id: dto.id,
    status: dto.status,
    totalMatches: dto.total_matches ?? dto.affected_chapters.reduce((sum, item) => sum + item.match_count, 0),
    affectedChapters: dto.affected_chapters.map((item) => ({
      chapterId: item.chapter_id,
      chapterTitle: item.chapter_title,
      beforeRev: item.before_rev,
      afterRev: item.after_rev,
      matchCount: item.match_count
    }))
  }
}

export function textReplacementErrorCode(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined
  try {
    const payload = JSON.parse(error.message) as { detail?: { code?: string } }
    return payload.detail?.code
  } catch {
    return undefined
  }
}

export const textReplacementApi = {
  async preview(projectId: string, spec: TextReplacementSpec): Promise<TextReplacementPreview> {
    return previewFromDto(await request<PreviewDto>(`/projects/${projectId}/text-replacements/preview`, {
      method: 'POST',
      body: JSON.stringify(payload(spec))
    }))
  },

  async execute(
    projectId: string,
    spec: TextReplacementSpec,
    previewToken: string,
    selectedMatchIds: string[],
    acknowledgeCodexRisk: boolean
  ): Promise<TextReplacementRun> {
    const dto = await request<RunDto>(`/projects/${projectId}/text-replacements`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        ...payload(spec),
        preview_token: previewToken,
        selected_match_ids: selectedMatchIds,
        acknowledge_codex_risk: acknowledgeCodexRisk
      })
    })
    return runFromDto(dto)
  },

  async undo(projectId: string, runId: string): Promise<TextReplacementRun> {
    return runFromDto(await request<RunDto>(`/projects/${projectId}/text-replacements/${runId}/undo`, {
      method: 'POST'
    }))
  }
}
