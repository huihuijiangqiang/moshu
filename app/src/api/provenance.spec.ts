import { describe, expect, it } from 'vitest'
import { mapProvenanceDto } from './provenance'

describe('mapProvenanceDto', () => {
  it('maps locatable sentence-risk fields from the API contract', () => {
    const report = mapProvenanceDto({
      scope: 'chapter',
      total_words: 18,
      ai_raw_words: 18,
      ai_edited_words: 0,
      human_words: 0,
      paragraphs: [{ id: 'p-risk', text: '值得注意的是，她已经到了。', words: 14, source: 'ai-raw', run_id: 'run-1' }],
      suspected_sentences: [{
        id: 'p-risk:0:14', paragraph_id: 'p-risk', text: '值得注意的是，她已经到了。',
        start: 0, end: 14, source: 'ai-raw', score: 71, reasons: ['模板化衔接']
      }],
      sentence_risk_version: 'zh-fiction-risk-v1',
      sentence_risk_disclaimer: '只用于人工复核。'
    })

    expect(report.suspectedSentences[0]).toMatchObject({
      paragraphId: 'p-risk', start: 0, end: 14, score: 71
    })
    expect(report.sentenceRiskVersion).toBe('zh-fiction-risk-v1')
  })
})
