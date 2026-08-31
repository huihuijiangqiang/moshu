import { USE_MOCK, request } from './http'
import { mockApi } from './mock'
import type { Chapter, ChapterPlanPatch, Project } from '@/types'

interface ProjectDto {
  id: string
  title: string
  target_words_daily: number
  style_profile_id: string | null
  volumes: Array<{ id: string; title: string; idx: number }>
}

interface ChapterListDto {
  id: string
  volume_id: string | null
  title: string
  idx: number
  words: number
  outline: string[]
  summary: string | null
}

interface ChapterDto extends ChapterListDto {
  content_html: string
  rev: number
}

const revisions = new Map<string, number>()

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
      title: volume.title
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
    outlineNote: '',
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
    return rows.map((row, index) =>
      chapterFromDto(row, row.words === 0 ? 'outlined' : index === rows.length - 1 ? 'drafting' : 'done')
    )
  },

  async getChapter(id: string): Promise<Chapter | undefined> {
    const dto = await request<ChapterDto>(`/chapters/${id}`)
    revisions.set(id, dto.rev)
    return { ...chapterFromDto(dto, dto.words > 0 ? 'done' : 'outlined'), content: dto.content_html, rev: dto.rev }
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

  async updateChapterPlan(_id: string, _patch: ChapterPlanPatch): Promise<Chapter | undefined> {
    throw new Error('real_outline_update_not_connected')
  },

  async insertChapter(_projectId: string, _volumeId: string, _afterIndex: number): Promise<Chapter> {
    throw new Error('real_chapter_create_not_connected')
  },

  async listCodex() { return [] },
  async confirmCodexEntry() {},
  async dropCodexEntry() {},
  async listGuardIssues() { return [] },
  async resolveGuardIssue() {},
  async getContextLayers() { return [] },
  draftParagraphs: []
}

export const contentApi = USE_MOCK ? mockApi : realApi
