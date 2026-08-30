import { describe, expect, it } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { AiDraft } from './AiDraft'

/** NodeView 依赖 Vue 渲染，单测里用不带 NodeView 的扩展验证命令与事务行为 */
const Plain = AiDraft.extend({ addNodeView: undefined })

function makeEditor() {
  return new Editor({
    element: document.createElement('div'),
    extensions: [StarterKit, Plain],
    content: '<p>原有正文。</p>'
  })
}

/** 草稿容器内部的段落文本。只看容器内，不受容器之后的正文影响。 */
function draftParagraphTexts(editor: Editor): string[] {
  const texts: string[] = []
  editor.state.doc.descendants((node) => {
    if (node.type.name !== 'aiDraft') return
    node.descendants((child) => {
      if (child.type.name === 'paragraph') texts.push(child.textContent)
    })
  })
  return texts
}

describe('AiDraft', () => {
  it('插入草稿容器并流式追加文本', () => {
    const editor = makeEditor()
    editor.commands.insertAiDraft()
    editor.commands.appendDraftText('风雪')
    editor.commands.appendDraftText('停了')
    expect(editor.getHTML()).toContain('data-ai-draft')
    expect(editor.getText()).toContain('风雪停了')
    editor.destroy()
  })

  it("'\n' 追加时开新段落", () => {
    const editor = makeEditor()
    editor.commands.insertAiDraft()
    editor.commands.appendDraftText('第一段')
    editor.commands.appendDraftText('\n')
    editor.commands.appendDraftText('第二段')
    // 数节点而不是数 HTML 里的 <p>：字符串切片会一直切到文档末尾，
    // 把草稿容器之后的正文段落也算进来。
    expect(draftParagraphTexts(editor)).toEqual(['第一段', '第二段'])
    editor.destroy()
  })

  it('采纳后草稿解包为正文，容器消失', () => {
    const editor = makeEditor()
    editor.commands.insertAiDraft()
    editor.commands.appendDraftText('被采纳的句子')
    let pos = -1
    editor.state.doc.descendants((node, p) => {
      if (node.type.name === 'aiDraft') pos = p
    })
    expect(pos).toBeGreaterThan(-1)
    editor.commands.acceptDraftAt(pos)
    expect(editor.getHTML()).not.toContain('data-ai-draft')
    expect(editor.getText()).toContain('被采纳的句子')
    editor.destroy()
  })

  it('流式写入不进 history —— 撤销一次即回到插入前', () => {
    const editor = makeEditor()
    const before = editor.getHTML()
    editor.commands.insertAiDraft()
    for (const ch of '很多很多增量字符') editor.commands.appendDraftText(ch)
    editor.commands.undo()
    expect(editor.getHTML()).toBe(before)
    editor.destroy()
  })

  it('rejectAllDrafts 跳过已锁定的草稿', () => {
    const editor = makeEditor()
    editor.commands.insertAiDraft()
    editor.commands.appendDraftText('锁定内容')
    editor.commands.setDraftStatus('locked')
    editor.commands.rejectAllDrafts()
    expect(editor.getHTML()).toContain('data-ai-draft')
    editor.destroy()
  })
})
