import { useEditor } from '@tiptap/vue-3'
import { Fragment, type Node as ProseMirrorNode } from '@tiptap/pm/model'
import { getHTMLFromFragment, type Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import CharacterCount from '@tiptap/extension-character-count'
import { AiDraft } from './extensions/AiDraft'
import { CodexRef } from './extensions/CodexRef'
import { Provenance } from './extensions/Provenance'
import { createCodexSuggestion } from './extensions/codex-suggestion'
import { useCodexStore } from '@/stores/codex'

export function useNovelEditor(content: string, onUpdate: (html: string, chars: number) => void) {
  const codex = useCodexStore()

  return useEditor({
    content,
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2] } }),
      Placeholder.configure({ placeholder: '继续写，或按 Tab 让 AI 接着往下铺；输入 @ 引用设定' }),
      CharacterCount,
      AiDraft,
      Provenance,
      CodexRef,
      createCodexSuggestion((q) => codex.search(q))
    ],
    editorProps: {
      attributes: { class: 'prose-body', spellcheck: 'false' }
    },
    onUpdate: ({ editor }) => {
      const canonical = canonicalBody(editor)
      onUpdate(canonical.html, canonical.characters)
    }
  })
}

/** AI candidates remain visible in the editor but are not canonical body content until accepted. */
export function canonicalBody(editor: Editor): { html: string; characters: number } {
  const nodes: ProseMirrorNode[] = []
  editor.state.doc.content.forEach((node) => {
    if (node.type.name !== 'aiDraft') nodes.push(node)
  })
  const fragment = Fragment.fromArray(nodes)
  return {
    html: getHTMLFromFragment(fragment, editor.schema),
    characters: nodes.reduce((total, node) => total + node.textContent.length, 0)
  }
}
