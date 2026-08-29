import { Extension } from '@tiptap/core'
import Suggestion from '@tiptap/suggestion'
import { reactive } from 'vue'
import type { CodexEntry } from '@/types'

/** 下拉状态放在模块级 reactive 里，由 CodexSuggestList 消费 —— 不额外引 tippy。 */
export const suggestState = reactive({
  open: false,
  items: [] as CodexEntry[],
  index: 0,
  rect: { left: 0, top: 0 },
  select: (_i: number) => {}
})

export function createCodexSuggestion(search: (q: string) => CodexEntry[]) {
  return Extension.create({
    name: 'codexSuggestion',
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          char: '@',
          allowSpaces: false,
          items: ({ query }) => search(query),
          command: ({ editor, range, props }) => {
            editor.chain().focus().deleteRange(range).insertCodexRef((props as CodexEntry).id).run()
          },
          render: () => ({
            onStart: (p) => {
              suggestState.items = p.items as CodexEntry[]
              suggestState.index = 0
              suggestState.open = p.items.length > 0
              const r = p.clientRect?.()
              if (r) suggestState.rect = { left: r.left, top: r.bottom + 6 }
              suggestState.select = (i) => p.command(p.items[i])
            },
            onUpdate: (p) => {
              suggestState.items = p.items as CodexEntry[]
              suggestState.open = p.items.length > 0
              const r = p.clientRect?.()
              if (r) suggestState.rect = { left: r.left, top: r.bottom + 6 }
              suggestState.select = (i) => p.command(p.items[i])
            },
            onKeyDown: (p) => {
              const n = suggestState.items.length
              if (!n) return false
              if (p.event.key === 'ArrowDown') { suggestState.index = (suggestState.index + 1) % n; return true }
              if (p.event.key === 'ArrowUp') { suggestState.index = (suggestState.index - 1 + n) % n; return true }
              if (p.event.key === 'Enter') { suggestState.select(suggestState.index); return true }
              if (p.event.key === 'Escape') { suggestState.open = false; return true }
              return false
            },
            onExit: () => { suggestState.open = false }
          })
        })
      ]
    }
  })
}
