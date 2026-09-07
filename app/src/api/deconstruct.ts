import { USE_MOCK, requestResponse } from './http'

export interface DeconstructionBeat {
  position: number
  label: string
  excerpt: string
}

export interface DeconstructionChapter {
  index: number
  title: string
  word_count: number
  paragraph_count: number
  dialogue_ratio: number
  summary: string
  beats: DeconstructionBeat[]
  confidence: string
}

export interface DeconstructionResult {
  parser: string
  stats: {
    total_words: number
    chapter_count: number
    average_chapter_words: number
    median_chapter_words: number
  }
  chapters: DeconstructionChapter[]
  rhythm_nodes: Array<{ chapter_index: number; position: number; label: string; intensity: number }>
  payoff_distribution: Array<{
    range: string
    chapter_from: number | null
    chapter_to: number | null
    score: number
    peak_chapters: number[]
  }>
  warnings: string[]
  disclaimer: string
}

function mockDeconstruction(text: string): DeconstructionResult {
  const normalized = text.replace(/\r\n?/g, '\n').trim()
  const lines = normalized.split('\n')
  const heading = /^\s*(第[零一二三四五六七八九十百千万\d]+[章节回部卷].{0,80}|Chapter\s+\d+.*|序章|楔子|尾声|番外.*)\s*$/i
  const starts = lines.flatMap((line, index) => {
    const title = line.replace(/^\s{0,3}#{1,6}\s+/, '').trim().replace(/#+$/, '').trim()
    return heading.test(title) ? [{ index, title }] : []
  })
  const ranges = starts.length ? starts.map((start, index) => ({ title: start.title, body: lines.slice(start.index + 1, starts[index + 1]?.index ?? lines.length).join('\n') })) : [{ title: '全文', body: normalized }]
  const count = (value: string) => Array.from(value).filter((char) => /[A-Za-z0-9\u3400-\u9fff]/.test(char)).length
  const chapters = ranges.map((range, index) => {
    const paragraphs = range.body.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean)
    const body = paragraphs.join(' ')
    const dialogueWords = Array.from(body.matchAll(/[「“"]([^」”"\n]+)[」”"]/g)).reduce((sum, match) => sum + count(match[1] ?? ''), 0)
    const words = count(body)
    return {
      index: index + 1,
      title: range.title,
      word_count: words,
      paragraph_count: paragraphs.length,
      dialogue_ratio: Number(Math.min(1, dialogueWords / Math.max(1, words)).toFixed(3)),
      summary: body.slice(0, 180),
      beats: paragraphs.length ? [{ position: 0.5, label: '场景推进', excerpt: paragraphs[0].slice(0, 120) }] : [],
      confidence: range.title === '全文' ? 'low' : 'high'
    }
  })
  const total = count(normalized)
  return {
    parser: 'MOCK',
    stats: { total_words: total, chapter_count: chapters.length, average_chapter_words: Math.round(total / Math.max(1, chapters.length)), median_chapter_words: chapters[Math.floor(chapters.length / 2)]?.word_count ?? 0 },
    chapters,
    rhythm_nodes: chapters.map((chapter) => ({ chapter_index: chapter.index, position: 0.5, label: '场景推进', intensity: 20 })),
    payoff_distribution: Array.from({ length: 10 }, (_, index) => ({ range: `${index * 10 + 1}-${(index + 1) * 10}%`, chapter_from: null, chapter_to: null, score: 0, peak_chapters: [] })),
    warnings: ['当前为演示模式，结果不会上传服务器。', '节奏与爽点信号仅供参考，请结合原文人工复核。'],
    disclaimer: '仅供结构学习参考，不构成版权许可、审查或抄袭建议。'
  }
}

export const deconstructionApi = {
  async analyze(file: File): Promise<DeconstructionResult> {
    if (USE_MOCK) return mockDeconstruction(await file.text())
    const query = new URLSearchParams({ filename: file.name })
    const response = await requestResponse(`/analysis/deconstruct?${query}`, {
      method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' }
    })
    return response.json() as Promise<DeconstructionResult>
  }
}
