import { USE_MOCK, request } from './http'

export type ProvenanceSource = 'ai-raw' | 'ai-edited' | 'human'

export interface ProvenanceParagraph {
  id: string
  text: string
  words: number
  source: ProvenanceSource
  runId: string | null
}

export interface SuspectedSentence {
  id: string
  paragraphId: string
  text: string
  start: number
  end: number
  source: ProvenanceSource
  score: number
  reasons: string[]
  riskRules?: SentenceRiskRule[]
}

export interface SentenceRiskRule {
  id: string
  category: string
  label: string
  score: number
  confidence: number
}

export interface ProvenanceReport {
  scope: 'chapter' | 'book'
  totalWords: number
  aiRawWords: number
  aiEditedWords: number
  humanWords: number
  paragraphs: ProvenanceParagraph[]
  suspectedSentences: SuspectedSentence[]
  sentenceRiskVersion: string
  sentenceRiskDisclaimer: string
}

export interface ProvenanceDto {
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
  suspected_sentences: Array<{
    id: string
    paragraph_id: string
    text: string
    start: number
    end: number
    source: ProvenanceSource
    score: number
    reasons: string[]
    risk_rules?: Array<{
      id: string
      category: string
      label: string
      score: number
      confidence: number
    }>
  }>
  sentence_risk_version: string
  sentence_risk_disclaimer: string
}

export function mapProvenanceDto(dto: ProvenanceDto): ProvenanceReport {
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
    })),
    suspectedSentences: dto.suspected_sentences.map((item) => ({
      id: item.id,
      paragraphId: item.paragraph_id,
      text: item.text,
      start: item.start,
      end: item.end,
      source: item.source,
      score: item.score,
      reasons: item.reasons,
      riskRules: (item.risk_rules ?? []).map((rule) => ({ ...rule }))
    })),
    sentenceRiskVersion: dto.sentence_risk_version,
    sentenceRiskDisclaimer: dto.sentence_risk_disclaimer
  }
}

export const provenanceApi = {
  async report(projectId: string, chapterId: string | null, scope: 'chapter' | 'book'): Promise<ProvenanceReport> {
    if (USE_MOCK) {
      return {
        scope,
        totalWords: 0,
        aiRawWords: 0,
        aiEditedWords: 0,
        humanWords: 0,
        paragraphs: [],
        suspectedSentences: [],
        sentenceRiskVersion: 'zh-fiction-risk-v1',
        sentenceRiskDisclaimer: '疑似句式只提示表达风险，不代表平台检测结论或 AI 鉴定。'
      }
    }
    const params = new URLSearchParams({ scope })
    if (chapterId) params.set('chapter_id', chapterId)
    return mapProvenanceDto(await request<ProvenanceDto>(`/projects/${projectId}/provenance?${params}`))
  }
}
