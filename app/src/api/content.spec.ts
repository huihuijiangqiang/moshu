import { describe, expect, it } from 'vitest'
import { htmlToDocument } from './content'

describe('htmlToDocument', () => {
  it('uses the backend paragraph pid contract', () => {
    const document = htmlToDocument(
      '<p data-paragraph-id="known-pid">第一段</p><p>第二段</p>'
    ) as { content: Array<{ attrs: { pid: string } }> }

    expect(document.content.map((node) => node.attrs.pid)).toEqual(['known-pid', 'p-1'])
  })
})
