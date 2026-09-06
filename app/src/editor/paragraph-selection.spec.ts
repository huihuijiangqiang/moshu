import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { describe, expect, it } from 'vitest'
import { ParagraphIdentity } from './extensions/ParagraphIdentity'
import { findParagraphTextSelection } from './paragraph-selection'

describe('findParagraphTextSelection', () => {
  it('maps backend code-point offsets to the matching ProseMirror selection', () => {
    const editor = new Editor({
      extensions: [StarterKit, ParagraphIdentity],
      content: '<p data-paragraph-id="p-risk">开场😀。值得注意的是，她已经到了。</p>'
    })
    const text = '开场😀。值得注意的是，她已经到了。'
    const start = [...text].indexOf('值')
    const end = start + [...'值得注意的是'].length

    const selection = findParagraphTextSelection(editor.state.doc, 'p-risk', start, end)

    expect(selection).not.toBeNull()
    expect(editor.state.doc.textBetween(selection!.from, selection!.to)).toBe('值得注意的是')
    editor.destroy()
  })

  it('rejects missing paragraphs and invalid ranges', () => {
    const editor = new Editor({
      extensions: [StarterKit, ParagraphIdentity],
      content: '<p data-paragraph-id="p-1">正文</p>'
    })

    expect(findParagraphTextSelection(editor.state.doc, 'missing', 0, 1)).toBeNull()
    expect(findParagraphTextSelection(editor.state.doc, 'p-1', 2, 1)).toBeNull()
    editor.destroy()
  })
})
