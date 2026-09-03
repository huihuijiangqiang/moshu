<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { EditorContent } from '@tiptap/vue-3'
import ChapterPanel from '@/components/layout/ChapterPanel.vue'
import AiSidePanel from '@/components/layout/AiSidePanel.vue'
import AiFloatingBar from '@/components/editor/AiFloatingBar.vue'
import ChapterVersionDrawer from '@/components/editor/ChapterVersionDrawer.vue'
import CodexSuggestList from '@/components/editor/CodexSuggestList.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useNovelEditor } from '@/editor/use-novel-editor'
import { useAutosave } from '@/composables/use-autosave'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { BodyConflictError, contentApi } from '@/api/content'
import { streamChapter, streamInline } from '@/api/generation'
import type { GenerationControls, InlineGenerateOptions } from '@/types'

defineOptions({ name: 'WorkspaceView' })

const store = useProjectStore()
const shell = useShellStore()
const route = useRoute()
const router = useRouter()
const { toProject } = useProjectNavigation()
const html = ref(store.active?.content ?? '')
const chapterId = computed(() => store.activeId)
const generating = ref(false)
const generationError = ref('')
const paneTab = ref<'body' | 'outline'>('body')
const versionOpen = ref(false)
const restoringVersion = ref(false)
const restoreError = ref('')
let abort: (() => void) | null = null
let disposed = false

const editor = useNovelEditor(html.value, (next, chars) => {
  html.value = next
  if (store.activeId) {
    store.setWords(store.activeId, chars)
    store.setContent(store.activeId, next)
  }
})

const {
  state: saveState,
  savedAt,
  online,
  recoveryDraft,
  conflict: saveConflict,
  storageWarning,
  retry: retrySave,
  prepareChapter,
  restoreLocalDraft,
  discardLocalDraft,
  acceptServerVersion,
  keepLocalVersion,
  flush,
  markClean,
  recordConflict
} = useAutosave(chapterId, html)

const requestedChapterId = computed(() => typeof route.query.chapter === 'string' ? route.query.chapter : null)

// 切章或跨页打开指定章节：正文返回后再写入同一个编辑器实例。
watch(
  [() => store.activeId, () => store.chapters.length, requestedChapterId],
  async ([activeId, , requestedId]) => {
    const id = requestedId && store.chapters.some((chapter) => chapter.id === requestedId)
      ? requestedId
      : activeId
    if (!id) return
    await store.openChapter(id)
    if (disposed || store.activeId !== id) return

    const content = store.chapters.find((chapter) => chapter.id === id)?.content ?? ''
    await prepareChapter(id, content)
    if (disposed || store.activeId !== id) return
    const currentEditor = editor.value
    if (!currentEditor || currentEditor.isDestroyed) return
    currentEditor.commands.setContent(content, { emitUpdate: false })
    html.value = content

    // 查询参数只负责一次跨页定位，消费后移除，避免用户在章节栏切换时被拉回旧章节。
    if (requestedId === id) {
      const query = { ...route.query }
      delete query.chapter
      await router.replace({ query })
    }
  },
  { immediate: true }
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

function handleChapterPick() {
  if (window.innerWidth <= 840) shell.leftOpen = false
}

onMounted(() => {
  shell.setCrumb(crumb.value)
  syncViewport()
  window.addEventListener('resize', syncViewport)
})

onBeforeUnmount(() => {
  disposed = true
  abort?.()
  abort = null
  window.removeEventListener('resize', syncViewport)
})

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
    case 'error':
      return '保存失败 · 已存本地'
    case 'conflict':
      return '版本冲突 · 等待处理'
    case 'saved':
      return `已保存 ${savedAt.value?.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) ?? ''}`
    default:
      return '就绪'
  }
})

const activeRecovery = computed(() => recoveryDraft.value?.chapterId === store.activeId ? recoveryDraft.value : null)
const activeSaveConflict = computed(() => saveConflict.value?.chapterId === store.activeId ? saveConflict.value : null)
const restoreDisabledReason = computed(() => {
  if (!online.value) return '联网后才能恢复历史版本。当前草稿仍保存在本地。'
  if (saveConflict.value) return '请先处理正文版本冲突，再恢复历史版本。'
  if (saveState.value === 'saving') return '当前正文正在保存，请稍候。'
  if (saveState.value === 'error') return '当前正文尚未保存成功，请先重试保存。'
  return undefined
})

function excerpt(content: string) {
  const text = new DOMParser().parseFromString(content, 'text/html').body.textContent?.trim() ?? ''
  return text.length > 180 ? `${text.slice(0, 180)}…` : text || '空白正文'
}

const serverConflictExcerpt = computed(() => excerpt(activeSaveConflict.value?.serverContentHtml ?? ''))
const localConflictExcerpt = computed(() => excerpt(activeSaveConflict.value?.clientContentHtml ?? ''))

function replaceEditorContent(content: string, rev?: number) {
  const id = store.activeId
  const currentEditor = editor.value
  if (!id || !currentEditor || currentEditor.isDestroyed) return
  currentEditor.commands.setContent(content, { emitUpdate: false })
  html.value = content
  store.setContent(id, content, rev)
}

function recoverDraft() {
  const draft = restoreLocalDraft()
  if (draft && draft.chapterId === store.activeId) replaceEditorContent(draft.html)
}

async function discardDraft() {
  await discardLocalDraft()
}

async function useServerVersion() {
  const accepted = await acceptServerVersion()
  if (accepted && accepted.chapterId === store.activeId) {
    replaceEditorContent(accepted.html, accepted.rev)
  }
}

function openVersionHistory() {
  if (!store.activeId) return
  restoreError.value = ''
  versionOpen.value = true
}

async function restoreVersion(revision: number) {
  const id = store.activeId
  if (!id || restoringVersion.value || restoreDisabledReason.value) return
  restoreError.value = ''
  restoringVersion.value = true
  try {
    await flush(id)
    if (saveState.value === 'error' || saveState.value === 'offline' || saveState.value === 'conflict') {
      restoreError.value = '当前正文未能安全保存，历史版本没有恢复。'
      return
    }
    const restored = await contentApi.restoreChapterVersion(id, revision)
    markClean(id, restored.content)
    replaceEditorContent(restored.content, restored.rev)
    store.setWords(id, restored.words)
    versionOpen.value = false
  } catch (error) {
    if (error instanceof BodyConflictError) {
      await recordConflict(error.conflict)
      replaceEditorContent(error.conflict.clientContentHtml)
      store.setWords(id, new DOMParser().parseFromString(error.conflict.clientContentHtml, 'text/html').body.textContent?.length ?? 0)
      versionOpen.value = false
      return
    }
    restoreError.value = '历史版本恢复失败，正文没有被更改。请重试。'
  } finally {
    restoringVersion.value = false
  }
}

function openConflictChapter() {
  if (saveConflict.value) store.activeId = saveConflict.value.chapterId
}

const readMinutes = computed(() => Math.max(1, Math.round((store.active?.words ?? 0) / 350)))

function generate(options: GenerationControls) {
  if (!editor.value || generating.value) return
  if (!store.activeId) return
  generationError.value = ''
  generating.value = true
  editor.value.chain().focus('end').insertAiDraft().run()
  abort = streamChapter(
    { chapterId: store.activeId, ...options },
    {
      onChunk: (t) => editor.value?.commands.appendDraftText(t),
      onDone: (result) => {
        if (typeof result?.runId === 'string') editor.value?.commands.setDraftRunId(result.runId)
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
      },
      onError: (error) => {
        generationError.value = error instanceof Error ? error.message : '生成失败，请重试'
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
      }
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
  if (!editor.value || !store.activeId || generating.value) return
  const { from, to } = editor.value.state.selection
  const selectedText = editor.value.state.doc.textBetween(from, to, '\n')
  const contextFrom = Math.max(0, from - 6000)
  const contextTo = Math.min(editor.value.state.doc.content.size, to + 3000)
  const nearbyText = editor.value.state.doc.textBetween(contextFrom, contextTo, '\n')
  const inlineAction = action as InlineGenerateOptions['action']
  const targetWords = inlineAction === '续写'
    ? 800
    : Math.max(300, Math.min(3000, selectedText.length * (inlineAction === '扩写' ? 2 : 1)))

  generationError.value = ''
  generating.value = true
  editor.value.chain().focus().insertAiDraft().run()
  abort = streamInline(
    {
      chapterId: store.activeId,
      targetWords,
      model: 'basic',
      useStyleProfile: inlineAction === '按我的风格',
      dialogueDensity: 'mid',
      action: inlineAction,
      selectedText,
      nearbyText
    },
    {
      onChunk: (text) => editor.value?.commands.appendDraftText(text),
      onDone: (result) => {
        if (typeof result?.runId === 'string') editor.value?.commands.setDraftRunId(result.runId)
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
      },
      onError: (error) => {
        generationError.value = error instanceof Error ? error.message : '行内生成失败，请重试'
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
      }
    }
  )
}

function editChapterPlan() {
  if (!store.active) return
  router.push({ path: toProject('outline'), query: { chapter: store.active.id } })
}
</script>

<template>
  <div class="wk-cols" :style="{ gridTemplateColumns: cols }">
    <!-- 左：章节 -->
    <aside class="wk-pane wk-pane-left" :data-collapsed="!shell.leftOpen" aria-label="章节">
      <button class="panel-mobile-close" type="button" title="关闭章节" aria-label="关闭章节" @click="shell.leftOpen = false">
        <AppIcon name="close" />
      </button>
      <ChapterPanel v-if="shell.leftOpen" @pick="handleChapterPick" />
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
            color: saveState === 'offline' || saveState === 'error' || saveState === 'dirty' ? 'var(--alert-ink)' : 'var(--ink-3)',
            fontWeight: saveState === 'offline' || saveState === 'error' ? 700 : 400
          }"
        >{{ saveLabel }}</span>
        <button class="paper-history-button" type="button" title="正文版本历史" aria-label="正文版本历史" @click="openVersionHistory">
          <AppIcon name="history" :size="16" />
        </button>
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

      <div
        v-if="storageWarning"
        :style="{ padding: '6px var(--u4)', fontSize: 'var(--fs-sm)', fontWeight: 700, background: 'var(--alert)', color: 'var(--on-alert)' }"
      >
        本地草稿存储不可用 · 请保持联网，云端自动保存仍会继续
      </div>

      <div
        v-if="activeRecovery"
        :style="{ padding: '8px var(--u4)', display: 'flex', alignItems: 'center', gap: 'var(--u3)', flexWrap: 'wrap', background: 'var(--alert-soft)', borderBottom: 'var(--hair) solid var(--alert-line)' }"
      >
        <strong :style="{ color: 'var(--alert-ink)' }">发现未同步的本地草稿</strong>
        <span :style="{ color: 'var(--ink-3)', fontSize: 'var(--fs-sm)' }">
          {{ new Date(activeRecovery.at).toLocaleString('zh-CN', { hour12: false }) }}
        </span>
        <span :style="{ marginLeft: 'auto' }" />
        <button class="wk-btn wk-btn-xs" type="button" @click="discardDraft">使用云端版本</button>
        <button class="wk-btn wk-btn-xs" type="button" data-primary="true" @click="recoverDraft">恢复本地草稿</button>
      </div>

      <div
        v-if="activeSaveConflict"
        :style="{ padding: 'var(--u3) var(--u4)', display: 'flex', alignItems: 'stretch', gap: 'var(--u3)', flexWrap: 'wrap', background: 'var(--alert-soft)', borderBottom: 'var(--hair) solid var(--alert-line)' }"
      >
        <div :style="{ width: '100%', fontWeight: 700, color: 'var(--alert-ink)' }">云端正文已更新，请选择保留哪一版</div>
        <div :style="{ flex: '1 1 260px', minWidth: 0, paddingLeft: 'var(--u3)', borderLeft: '3px solid var(--line-strong)' }">
          <div class="wk-label">云端 · 第 {{ activeSaveConflict.serverRev }} 版</div>
          <p :style="{ margin: '4px 0 0', color: 'var(--ink-2)', lineHeight: 1.55 }">{{ serverConflictExcerpt }}</p>
        </div>
        <div :style="{ flex: '1 1 260px', minWidth: 0, paddingLeft: 'var(--u3)', borderLeft: '3px solid var(--alert)' }">
          <div class="wk-label" :style="{ color: 'var(--alert-ink)' }">本地草稿</div>
          <p :style="{ margin: '4px 0 0', color: 'var(--ink-2)', lineHeight: 1.55 }">{{ localConflictExcerpt }}</p>
        </div>
        <div :style="{ display: 'flex', gap: 'var(--u2)', alignItems: 'center', flexWrap: 'wrap' }">
          <button class="wk-btn wk-btn-xs" type="button" @click="useServerVersion">采用云端</button>
          <button class="wk-btn wk-btn-xs" type="button" data-primary="true" @click="keepLocalVersion">保留本地并保存</button>
        </div>
      </div>

      <div
        v-else-if="saveConflict"
        :style="{ padding: '7px var(--u4)', display: 'flex', alignItems: 'center', gap: 'var(--u3)', background: 'var(--alert-soft)', borderBottom: 'var(--hair) solid var(--alert-line)', color: 'var(--alert-ink)' }"
      >
        <strong>另一章有版本冲突</strong>
        <button class="wk-btn wk-btn-xs" type="button" @click="openConflictChapter">打开处理</button>
      </div>

      <div
        v-if="saveState === 'error'"
        :style="{ padding: '7px var(--u4)', display: 'flex', alignItems: 'center', gap: 'var(--u3)', background: 'var(--alert-soft)', borderBottom: 'var(--hair) solid var(--alert-line)', color: 'var(--alert-ink)' }"
      >
        <strong>云端保存失败，本地草稿仍在</strong>
        <button class="wk-btn wk-btn-xs" type="button" @click="retrySave">重试保存</button>
      </div>

      <!-- v-show 而非 v-if：编辑器不能因为切标签被卸载 -->
      <div v-show="paneTab === 'body'" class="prose">
        <EditorContent :editor="editor" />
        <AiFloatingBar @run="runInline" />
      </div>

      <div v-if="paneTab === 'outline'" class="prose" :style="{ paddingTop: 'var(--u6)' }">
        <div class="row" :style="{ justifyContent: 'space-between', marginBottom: 'var(--u3)', paddingBottom: 'var(--u3)', borderBottom: 'var(--hair) solid var(--line)' }">
          <div class="wk-label">章纲节点</div>
          <button class="wk-btn wk-btn-xs" type="button" @click="editChapterPlan">修改章纲</button>
        </div>
        <p
          v-if="store.active?.bodyNeedsRevision"
          :style="{ margin: '0 0 var(--u4)', padding: 'var(--u2) var(--u3)', borderLeft: '3px solid var(--alert)', background: 'var(--alert-soft)', color: 'var(--alert-ink)', fontWeight: 700 }"
        >章纲已更新，这一章的正文被标记为待调整。</p>
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
    </main>

    <!-- 右：AI -->
    <aside class="wk-pane wk-pane-right" :data-collapsed="!shell.rightOpen" aria-label="AI 面板">
      <button class="panel-mobile-close" type="button" title="关闭 AI 面板" aria-label="关闭 AI 面板" @click="shell.rightOpen = false">
        <AppIcon name="close" />
      </button>
      <AiSidePanel
        v-if="shell.rightOpen"
        :generating="generating"
        :generation-error="generationError"
        @generate="generate"
        @stop="stop"
      />
      <button v-else class="wk-stub" type="button" title="展开 AI 面板 ⌘J" @click="shell.rightOpen = true">
        AI 面板
      </button>
    </aside>

    <CodexSuggestList />
    <ChapterVersionDrawer
      v-if="versionOpen && store.activeId"
      :chapter-id="store.activeId"
      :chapter-title="store.active?.title || '未命名章节'"
      :current-content="html"
      :restore-disabled-reason="restoreDisabledReason"
      :restoring="restoringVersion"
      :restore-error="restoreError"
      @close="versionOpen = false"
      @restore="restoreVersion"
    />
  </div>
</template>

<style scoped>
.paper-history-button {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  padding: 0;
  color: var(--ink-3);
  background: transparent;
  border: var(--hair) solid transparent;
  border-radius: 4px;
  cursor: pointer;
}
.paper-history-button:hover { color: var(--ink); background: var(--panel-sunken); border-color: var(--line); }
.paper-history-button:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
</style>
