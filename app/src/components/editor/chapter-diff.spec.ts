import { describe, expect, it } from 'vitest'
import { chapterDiff, htmlBlocks, htmlPlainText } from './chapter-diff'

describe('chapter paragraph diff', () => {
  it('marks the changed middle and keeps matching edges', () => {
    const rows = chapterDiff(
      '<p>开头</p><p>旧场景</p><p>结尾</p>',
      '<p>开头</p><p>新场景</p><p>结尾</p>'
    )

    expect(rows).toEqual([
      { kind: 'same', text: '开头' },
      { kind: 'removed', text: '旧场景' },
      { kind: 'added', text: '新场景' },
      { kind: 'same', text: '结尾' }
    ])
  })

  it('folds long unchanged edges instead of rendering a whole chapter twice', () => {
    const paragraphs = Array.from({ length: 20 }, (_, index) => `<p>第${index}段</p>`)
    const current = [...paragraphs]
    current[10] = '<p>改写段落</p>'

    const rows = chapterDiff(paragraphs.join(''), current.join(''))

    expect(rows.filter((row) => row.kind === 'omitted')).toHaveLength(2)
    expect(rows).toContainEqual({ kind: 'removed', text: '第10段' })
    expect(rows).toContainEqual({ kind: 'added', text: '改写段落' })
    expect(rows.length).toBeLessThan(20)
  })

  it('extracts text without preserving executable historical markup', () => {
    const html = '<h2>旧章</h2><p>正文<img src=x onerror=alert(1)></p>'

    expect(htmlBlocks(html)).toEqual(['旧章', '正文'])
    expect(htmlPlainText(html)).toBe('旧章\n\n正文')
  })
})
