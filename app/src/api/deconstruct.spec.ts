import { describe, expect, it } from 'vitest'
import { deconstructionApi } from './deconstruct'

describe('reference deconstruction API', () => {
  it('returns a non-persistent mock analysis with a chapter fallback', async () => {
    const result = await deconstructionApi.analyze(new File(['一段没有章节标题的参考文本。'], 'reference.txt'))

    expect(result.stats.chapter_count).toBe(1)
    expect(result.chapters[0]?.title).toBe('全文')
    expect(result.disclaimer).toContain('版权')
  })
})
