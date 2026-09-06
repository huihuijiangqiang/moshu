import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { describe, expect, it } from 'vitest'
import { ensureParagraphIds, ParagraphIdentity } from './ParagraphIdentity'

describe('ParagraphIdentity', () => {
  it('preserves server anchors and assigns stable ids to unanchored blocks', () => {
    const editor = new Editor({
      extensions: [StarterKit, ParagraphIdentity],
      content: '<p data-paragraph-id="known">第一段</p><p>第二段</p>'
    })

    ensureParagraphIds(editor)
    const html = editor.getHTML()
    expect(html).toContain('<p data-paragraph-id="known">第一段</p>')
    expect(html).toContain('<p data-paragraph-id="p-1">第二段</p>')
    editor.destroy()
  })

  it('repairs duplicate anchors before they can be saved', () => {
    const editor = new Editor({
      extensions: [StarterKit, ParagraphIdentity],
      content: '<p data-paragraph-id="duplicate">甲</p><p data-paragraph-id="duplicate">乙</p>'
    })

    ensureParagraphIds(editor)
    const ids: string[] = []
    editor.state.doc.descendants((node) => {
      if (node.type.name === 'paragraph') ids.push(node.attrs.pid as string)
    })
    expect(ids[0]).toBe('duplicate')
    expect(ids[1]).toBeTruthy()
    expect(new Set(ids).size).toBe(2)
    editor.destroy()
  })
})
