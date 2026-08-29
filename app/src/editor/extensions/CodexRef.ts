import { Node, mergeAttributes } from '@tiptap/core'
import { VueNodeViewRenderer } from '@tiptap/vue-3'
import CodexRefChip from '@/components/editor/CodexRefChip.vue'

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    codexRef: {
      insertCodexRef: (id: string) => ReturnType
    }
  }
}

/**
 * 存的是条目 id，不是文字 —— 条目改名后正文自动跟随，
 * 且服务端能精确知道本章引用了哪些条目（一致性检查与上下文装配都依赖这份关系）。
 */
export const CodexRef = Node.create({
  name: 'codexRef',
  inline: true,
  group: 'inline',
  atom: true,

  addAttributes() {
    return { id: { default: '' } }
  },

  parseHTML() {
    return [{ tag: 'span[data-codex-ref]', getAttrs: (el) => ({ id: (el as HTMLElement).getAttribute('data-codex-ref') }) }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes({ 'data-codex-ref': HTMLAttributes.id })]
  },

  addNodeView() {
    return VueNodeViewRenderer(CodexRefChip)
  },

  addCommands() {
    return {
      insertCodexRef:
        (id) =>
        ({ commands }) =>
          commands.insertContent([{ type: this.name, attrs: { id } }, { type: 'text', text: ' ' }])
    }
  }
})
