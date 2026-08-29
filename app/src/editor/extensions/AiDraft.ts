import { Node, mergeAttributes, type CommandProps } from '@tiptap/core'
import { VueNodeViewRenderer } from '@tiptap/vue-3'
import AiDraftBlock from '@/components/editor/AiDraftBlock.vue'

export type DraftStatus = 'streaming' | 'pending' | 'locked'

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    aiDraft: {
      insertAiDraft: () => ReturnType
      appendDraftText: (text: string) => ReturnType
      setDraftStatus: (status: DraftStatus) => ReturnType
      acceptDraftAt: (pos: number) => ReturnType
      rejectDraftAt: (pos: number) => ReturnType
      rejectAllDrafts: () => ReturnType
    }
  }
}

function findDraft(state: CommandProps['state']) {
  let found: { pos: number; size: number } | null = null
  state.doc.descendants((node, pos) => {
    if (node.type.name === 'aiDraft') found = { pos, size: node.nodeSize }
  })
  return found as { pos: number; size: number } | null
}

/**
 * AI 输出的待采纳容器。三条硬约束：
 * 1. 流式写入不进 history（addToHistory: false），否则撤销栈会被几百次增量污染。
 * 2. 采纳是一次事务 —— 用户按一次 Cmd+Z 应该整段退回。
 * 3. locked 状态的草稿不参与「重新生成未锁定部分」。
 */
export const AiDraft = Node.create({
  name: 'aiDraft',
  group: 'block',
  content: 'block+',
  defining: true,
  selectable: false,

  addAttributes() {
    return {
      status: { default: 'pending' as DraftStatus },
      label: { default: 'AI 草稿' }
    }
  },

  parseHTML() {
    return [{ tag: 'div[data-ai-draft]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-ai-draft': '' }), 0]
  },

  addNodeView() {
    return VueNodeViewRenderer(AiDraftBlock)
  },

  addCommands() {
    return {
      insertAiDraft:
        () =>
        ({ commands }) =>
          commands.insertContent({
            type: this.name,
            attrs: { status: 'streaming' },
            content: [{ type: 'paragraph' }]
          }),

      appendDraftText:
        (text) =>
        ({ state, tr, dispatch }) => {
          const found = findDraft(state)
          if (!found) return false
          // 容器内最后一个子节点的内部末尾
          const insertAt = found.pos + found.size - 2
          if (text === '\n') tr.split(insertAt)
          else tr.insertText(text, insertAt)
          tr.setMeta('addToHistory', false)
          if (dispatch) dispatch(tr)
          return true
        },

      setDraftStatus:
        (status) =>
        ({ state, tr, dispatch }) => {
          const found = findDraft(state)
          if (!found) return false
          const node = state.doc.nodeAt(found.pos)
          if (!node) return false
          tr.setNodeMarkup(found.pos, undefined, { ...node.attrs, status })
          tr.setMeta('addToHistory', false)
          if (dispatch) dispatch(tr)
          return true
        },

      /** 解包：草稿内容原地成为正文，一次事务完成 */
      acceptDraftAt:
        (pos) =>
        ({ state, tr, dispatch }) => {
          const node = state.doc.nodeAt(pos)
          if (!node || node.type.name !== 'aiDraft') return false
          tr.replaceWith(pos, pos + node.nodeSize, node.content)
          if (dispatch) dispatch(tr)
          return true
        },

      rejectDraftAt:
        (pos) =>
        ({ state, tr, dispatch }) => {
          const node = state.doc.nodeAt(pos)
          if (!node || node.type.name !== 'aiDraft') return false
          tr.delete(pos, pos + node.nodeSize)
          if (dispatch) dispatch(tr)
          return true
        },

      rejectAllDrafts:
        () =>
        ({ state, tr, dispatch }) => {
          const positions: { pos: number; size: number }[] = []
          state.doc.descendants((node, pos) => {
            if (node.type.name === 'aiDraft' && node.attrs.status !== 'locked') {
              positions.push({ pos, size: node.nodeSize })
            }
          })
          if (!positions.length) return false
          for (const p of positions.reverse()) tr.delete(p.pos, p.pos + p.size)
          if (dispatch) dispatch(tr)
          return true
        }
    }
  }
})
