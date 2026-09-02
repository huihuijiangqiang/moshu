import { USE_MOCK, request } from './http'

export type ProvenanceSource = 'ai-raw' | 'ai-edited' | 'human'

export interface ProvenanceParagraph {
  id: string
  text: string
  words: number
  source: ProvenanceSource
  runId: string | null
}

export interface ProvenanceReport {
  scope: 'chapter' | 'book'
  totalWords: number
  aiRawWords: number
  aiEditedWords: number
  humanWords: number
  paragraphs: ProvenanceParagraph[]
}

interface ProvenanceDto {
  scope: 'chapter' | 'book'
  total_words: number
  ai_raw_words: number
  ai_edited_words: number
  human_words: number
  paragraphs: Array<{
    id: string
    text: string
    words: number
    source: ProvenanceSource
    run_id: string | null
  }>
}

function fromDto(dto: ProvenanceDto): ProvenanceReport {
  return {
    scope: dto.scope,
    totalWords: dto.total_words,
    aiRawWords: dto.ai_raw_words,
    aiEditedWords: dto.ai_edited_words,
    humanWords: dto.human_words,
    paragraphs: dto.paragraphs.map((paragraph) => ({
      id: paragraph.id,
      text: paragraph.text,
      words: paragraph.words,
      source: paragraph.source,
      runId: paragraph.run_id
    }))
  }
}

export const provenanceApi = {
  async report(projectId: string, chapterId: string | null, scope: 'chapter' | 'book'): Promise<ProvenanceReport> {
    if (USE_MOCK) {
      return { scope, totalWords: 0, aiRawWords: 0, aiEditedWords: 0, humanWords: 0, paragraphs: [] }
    }
    const params = new URLSearchParams({ scope })
    if (chapterId) params.set('chapter_id', chapterId)
    return fromDto(await request<ProvenanceDto>(`/projects/${projectId}/provenance?${params}`))
  }
}
