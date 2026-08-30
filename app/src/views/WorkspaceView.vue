<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorContent } from '@tiptap/vue-3'
import ChapterPanel from '@/components/layout/ChapterPanel.vue'
import AiSidePanel from '@/components/layout/AiSidePanel.vue'
import AiFloatingBar from '@/components/editor/AiFloatingBar.vue'
import CodexSuggestList from '@/components/editor/CodexSuggestList.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useNovelEditor } from '@/editor/use-novel-editor'
import { useAutosave } from '@/composables/use-autosave'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { streamChapter } from '@/api/generation'

defineOptions({ name: 'WorkspaceView' })

const store = useProjectStore()
const shell = useShellStore()
const html = ref(store.active?.content ?? '')
const chapterId = computed(() => store.activeId)
const generating = ref(false)
const paneTab = ref<'body' | 'outline'>('body')
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

const crumb = computed(() => {
  const c = store.active
  if (!c) return '写作台'
  const vol = store.project?.volumes.find((v) => v.id === c.volumeId)
  return `第${vol?.index ?? 1}卷 · 第 ${c.index} 章 ${c.title || '未命名'}`
})

watch(crumb, (v) => shell.setCrumb(v), { immediate: true })

function syncViewport() {
  const compact = window.innerWidth <= 840
  if (compact) {
    shell.leftOpen = false
    shell.rightOpen = false
  } else if (window.innerWidth <= 1200) {
    shell.rightOpen = false
  }
}

function openPanel(side: 'left' | 'right') {
  if (side === 'left') {
    shell.rightOpen = false
    shell.leftOpen = true
  } else {
    shell.leftOpen = false
    shell.rightOpen = true
  }
}

onMounted(() => {
  shell.setCrumb(crumb.value)
  syncViewport()
  window.addEventListener('resize', syncViewport)
})

onBeforeUnmount(() => window.removeEventListener('resize', syncViewport))

/** 折叠不用 display:none —— 改列宽，编辑器实例保持挂载 */
const cols = computed(() => {
  const l = shell.leftOpen ? 'var(--left-w)' : '18px'
  const r = shell.rightOpen ? 'var(--right-w)' : '18px'
  return `${l} minmax(0, 1fr) ${r}`
})

const saveLabel = computed(() => {
  switch (saveState.value) {
    case 'saving':
      return '保存中…'
    case 'dirty':
      return '未保存'
    case 'offline':
      return '离线 · 已存本地'
    case 'saved':
      return `已保存 ${savedAt.value?.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) ?? ''}`
    default:
      return '就绪'
  }
})

const readMinutes = computed(() => Math.max(1, Math.round((store.active?.words ?? 0) / 350)))

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
  <div class="wk-cols" :style="{ gridTemplateColumns: cols }">
    <!-- 左：章节 -->
    <aside class="wk-pane wk-pane-left" :data-collapsed="!shell.leftOpen" aria-label="章节">
      <button class="panel-mobile-close" type="button" title="关闭章节" aria-label="关闭章节" @click="shell.leftOpen = false">
        <AppIcon name="close" />
      </button>
      <ChapterPanel v-if="shell.leftOpen" />
      <button v-else class="wk-stub" type="button" title="展开章节栏 ⌘B" @click="shell.leftOpen = true">
        章节 {{ store.chapters.length }}
      </button>
    </aside>

    <!-- 中：正文。这一栏永不卸载 -->
    <main class="wk-pane wk-pane-paper">
      <div class="paper-bar">
        <button class="paper-panel-toggle paper-panel-toggle-left" type="button" title="打开章节" @click="openPanel('left')">
          章节
        </button>
        <button
          class="paper-bar-tab"
          type="button"
          :aria-selected="paneTab === 'body'"
          @click="paneTab = 'body'"
        >正文</button>
        <button
          class="paper-bar-tab"
          type="button"
          :aria-selected="paneTab === 'outline'"
          @click="paneTab = 'outline'"
        >章纲 {{ store.active?.outline.length ?? 0 }}</button>

        <span class="paper-stats" :style="{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }">
          {{ (store.active?.words ?? 0).toLocaleString() }} 字
        </span>
        <span class="paper-read-time">约 {{ readMinutes }} 分钟</span>
        <span class="paper-divider" :style="{ width: 'var(--hair)', height: '12px', background: 'var(--line-strong)' }" />
        <span
          class="paper-save-state"
          :style="{
            color: saveState === 'offline' || saveState === 'dirty' ? 'var(--alert-ink)' : 'var(--ink-3)',
            fontWeight: saveState === 'offline' ? 700 : 400
          }"
        >{{ saveLabel }}</span>
        <button class="wk-btn wk-btn-xs" type="button" :title="shell.zen ? '退出纯净模式 ⌘\\' : '纯净模式 ⌘\\'" @click="shell.toggleZen()">
          {{ shell.zen ? '退出纯净' : '纯净模式' }}
        </button>
        <button class="paper-panel-toggle paper-panel-toggle-right" type="button" title="打开 AI 面板" @click="openPanel('right')">
          AI
        </button>
      </div>

      <div v-if="!online" :style="{ padding: '5px var(--u4)', fontSize: 'var(--fs-sm)', fontWeight: 700, background: 'var(--alert)', color: 'var(--on-alert)' }">
        离线中 · 你可以继续写，内容已存在本地，联网后自动同步
      </div>

      <!-- v-show 而非 v-if：编辑器不能因为切标签被卸载 -->
      <div v-show="paneTab === 'body'" class="prose">
        <EditorContent :editor="editor" />
      </div>

      <div v-if="paneTab === 'outline'" class="prose" :style="{ paddingTop: 'var(--u6)' }">
        <div class="wk-label" :style="{ marginBottom: 'var(--u3)' }">章纲节点</div>
        <ol
          v-if="store.active?.outline.length"
          :style="{ margin: 0, padding: '0 0 0 22px', fontSize: 'var(--fs-md)', lineHeight: 2.1 }"
        >
          <li v-for="(o, i) in store.active.outline" :key="i">{{ o }}</li>
        </ol>
        <p v-else :style="{ color: 'var(--ink-3)' }">本章还没有章纲。</p>
        <p v-if="store.active?.outlineNote" :style="{ marginTop: 'var(--u5)', paddingTop: 'var(--u4)', borderTop: 'var(--hair) solid var(--line)', color: 'var(--ink-2)', lineHeight: 1.9 }">
          {{ store.active.outlineNote }}
        </p>
      </div>

      <AiFloatingBar v-show="paneTab === 'body'" @run="runInline" />
    </main>

    <!-- 右：AI -->
    <aside class="wk-pane wk-pane-right" :data-collapsed="!shell.rightOpen" aria-label="AI 面板">
      <button class="panel-mobile-close" type="button" title="关闭 AI 面板" aria-label="关闭 AI 面板" @click="shell.rightOpen = false">
        <AppIcon name="close" />
      </button>
      <AiSidePanel v-if="shell.rightOpen" :generating="generating" @generate="generate" @stop="stop" />
      <button v-else class="wk-stub" type="button" title="展开 AI 面板 ⌘J" @click="shell.rightOpen = true">
        AI 面板
      </button>
    </aside>

    <CodexSuggestList />
  </div>
</template>
