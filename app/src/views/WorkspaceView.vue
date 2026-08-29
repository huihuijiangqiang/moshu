<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { EditorContent } from '@tiptap/vue-3'
import AppHeader from '@/components/layout/AppHeader.vue'
import ChapterSidebar from '@/components/layout/ChapterSidebar.vue'
import AiPanel from '@/components/layout/AiPanel.vue'
import AiFloatingBar from '@/components/editor/AiFloatingBar.vue'
import CodexSuggestList from '@/components/editor/CodexSuggestList.vue'
import { useNovelEditor } from '@/editor/use-novel-editor'
import { useAutosave } from '@/composables/use-autosave'
import { useProjectStore } from '@/stores/project'
import { streamChapter } from '@/api/generation'

const router = useRouter()
const store = useProjectStore()
const html = ref(store.active?.content ?? '')
const chapterId = computed(() => store.activeId)
const generating = ref(false)
let abort: (() => void) | null = null

const editor = useNovelEditor(html.value, (next, chars) => {
  html.value = next
  if (store.activeId) store.setWords(store.activeId, chars)
})

const { state: saveState, savedAt, online } = useAutosave(chapterId, html)

// 切章：卸载旧正文内容，只把新章塞进同一个编辑器实例（不重挂载）
watch(
  () => store.activeId,
  async (id) => {
    if (!id) return
    await store.openChapter(id)
    editor.value?.commands.setContent(store.active?.content ?? '', { emitUpdate: false })
    html.value = store.active?.content ?? ''
  }
)

function generate() {
  if (!editor.value || generating.value) return
  generating.value = true
  editor.value.chain().focus('end').insertAiDraft().run()
  abort = streamChapter(
    { chapterId: store.activeId ?? '', targetWords: 3000, model: 'basic', useStyleProfile: true, dialogueDensity: 'high' },
    {
      onChunk: (t) => editor.value?.commands.appendDraftText(t),
      onDone: () => { editor.value?.commands.setDraftStatus('pending'); generating.value = false },
      onError: () => { generating.value = false }
    }
  )
}

function stop() {
  abort?.()
  abort = null
  generating.value = false
  editor.value?.commands.setDraftStatus('pending')
}

function runInline(action: string) {
  // 真实实现：把选区文本 + 上下文送到 /api/generate/inline，结果同样落进 aiDraft
  if (!editor.value) return
  editor.value.chain().focus().insertAiDraft().appendDraftText(`（${action}：此处接后端行内生成）`).setDraftStatus('pending').run()
}
</script>

<template>
  <div class="app">
    <AppHeader
      :subtitle="store.active ? `第${store.active.index}章 · ${store.active.title || '未命名'}` : ''"
      :save-state="saveState"
      :saved-at="savedAt"
    >
      <template #actions>
        <button v-if="generating" class="btn btn-secondary" type="button" :style="{ height: '30px', fontSize: '12px' }" @click="stop">停止</button>
        <button v-else class="btn btn-secondary" type="button" :style="{ height: '30px', fontSize: '12px' }" @click="router.push('/export')">导出</button>
      </template>
    </AppHeader>

    <div v-if="!online" class="wire-note">离线中 · 你可以继续写，内容已存在本地，联网后自动同步</div>

    <div class="app-body" :style="{ gridTemplateColumns: 'var(--app-left-w) 1fr var(--app-right-w)' }">
      <ChapterSidebar />

      <main class="pane pane-center">
        <div class="row rule-b muted" :style="{ height: '42px', padding: '0 24px', fontSize: '12px', gap: '18px' }">
          <span :style="{ fontWeight: 700, color: 'var(--color-text)' }">正文</span>
          <span>章纲</span>
          <span :style="{ marginLeft: 'auto' }">{{ store.active?.words.toLocaleString() ?? 0 }} 字</span>
        </div>
        <div class="prose">
          <EditorContent :editor="editor" />
        </div>
        <AiFloatingBar @run="runInline" />
      </main>

      <AiPanel @generate="generate" />
    </div>

    <CodexSuggestList />
  </div>
</template>
