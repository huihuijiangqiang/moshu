<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { EditorContent } from '@tiptap/vue-3'
import ChapterPanel from '@/components/layout/ChapterPanel.vue'
import AiSidePanel from '@/components/layout/AiSidePanel.vue'
import AiFloatingBar from '@/components/editor/AiFloatingBar.vue'
import ChapterVersionDrawer from '@/components/editor/ChapterVersionDrawer.vue'
import TextReplacementDrawer from '@/components/editor/TextReplacementDrawer.vue'
import CodexSuggestList from '@/components/editor/CodexSuggestList.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useNovelEditor } from '@/editor/use-novel-editor'
import { findParagraphTextSelection } from '@/editor/paragraph-selection'
import { useAutosave } from '@/composables/use-autosave'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { BodyConflictError, contentApi } from '@/api/content'
import { GenerationError, generationDraftApi, streamChapter, streamInline } from '@/api/generation'
import { ReviewConflictError, reviewApi } from '@/api/reviews'
import { scenesApi, type ChapterScene } from '@/api/scenes'
import type { GenerationControls, GenerationDraftDetail, GenerationDraftSummary, InlineGenerateOptions, ReviewAnchor, ReviewComment, ReviewRound, ReviewWorkspace, TextReplacementRun } from '@/types'

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
const generationErrorCode = ref('')
const lastGenerationOptions = ref<GenerationControls | null>(null)
const chapterActionError = ref('')
const paneTab = ref<'body' | 'outline'>('body')
const versionOpen = ref(false)
const replacementOpen = ref(false)
const restoringVersion = ref(false)
const restoreError = ref('')
const generationDrafts = ref<GenerationDraftSummary[]>([])
const draftsLoading = ref(false)
const reviewWorkspace = ref<ReviewWorkspace>()
const reviewAnchor = ref<ReviewAnchor>()
const reviewLoading = ref(false)
const reviewBusy = ref(false)
const reviewError = ref('')
const mobileReadOnly = ref(false)
const activeScene = ref<ChapterScene | null>(null)
let abort: (() => void) | null = null
let disposed = false
let draftLoadSequence = 0
let reviewLoadSequence = 0
let stoppedDraftTimer: ReturnType<typeof setTimeout> | null = null
let reviewHighlightTimer: ReturnType<typeof setTimeout> | null = null
let sentenceHighlightTimer: ReturnType<typeof setTimeout> | null = null
let locateParagraphId: string | null = null
let suppressRouteCleanupWatch = false

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
  flushAll,
  markClean,
  recordConflict
} = useAutosave(chapterId, html)

const requestedChapterId = computed(() => typeof route.query.chapter === 'string' ? route.query.chapter : null)
const requestedSceneId = computed(() => typeof route.query.scene === 'string' ? route.query.scene : null)
const autoGenerateRequested = computed(() => route.query.autoGenerate === '1')
const projectId = computed(() => store.project?.id)

function sentenceRouteTarget() {
  const paragraphId = typeof route.query.paragraph === 'string' ? route.query.paragraph : ''
  const start = typeof route.query.start === 'string' ? Number(route.query.start) : Number.NaN
  const end = typeof route.query.end === 'string' ? Number(route.query.end) : Number.NaN
  if (!paragraphId || !Number.isInteger(start) || !Number.isInteger(end)) return null
  return { paragraphId, start, end, rewrite: route.query.rewrite === '1' }
}

function highlightSentenceParagraph(paragraphId: string) {
  const target = document.querySelector(`[data-paragraph-id="${CSS.escape(paragraphId)}"]`)
  if (!(target instanceof HTMLElement)) return
  if (sentenceHighlightTimer) clearTimeout(sentenceHighlightTimer)
  document.querySelectorAll('.sentence-risk-located').forEach((element) => {
    element.classList.remove('sentence-risk-located')
  })
  target.classList.add('sentence-risk-located')
  target.scrollIntoView({ block: 'center', behavior: 'smooth' })
  sentenceHighlightTimer = setTimeout(() => {
    target.classList.remove('sentence-risk-located')
    sentenceHighlightTimer = null
  }, 2400)
}

// 切章或跨页打开指定章节：正文返回后再写入同一个编辑器实例。
watch(
  [() => store.activeId, () => store.chapters.length, requestedChapterId, requestedSceneId],
  async ([activeId, , requestedId, requestedScene]) => {
    if (route.path !== toProject('write')) return
    if (suppressRouteCleanupWatch && !requestedId) return
    const id = requestedId && store.chapters.some((chapter) => chapter.id === requestedId)
      ? requestedId
      : activeId
    if (!id) return
    const sentenceTarget = requestedId === id ? sentenceRouteTarget() : null
    await store.openChapter(id)
    if (disposed || route.path !== toProject('write') || store.activeId !== id) return

    if (requestedScene && requestedId === id) {
      try {
        const sceneCards = await scenesApi.list(id)
        if (disposed || route.path !== toProject('write') || store.activeId !== id) return
        activeScene.value = sceneCards.find((scene) => scene.id === requestedScene) ?? null
        paneTab.value = 'body'
      } catch {
        activeScene.value = null
      }
    } else if (activeScene.value?.chapterId !== id) {
      activeScene.value = null
    }

    const content = store.chapters.find((chapter) => chapter.id === id)?.content ?? ''
    await prepareChapter(id, content)
    if (disposed || route.path !== toProject('write') || store.activeId !== id) return
    const currentEditor = editor.value
    if (!currentEditor || currentEditor.isDestroyed) return
    currentEditor.commands.setContent(content, { emitUpdate: false })
    html.value = content
    syncReviewAnchor()
    await nextTick()
    if (locateParagraphId) {
      const target = document.querySelector(`[data-paragraph-id="${CSS.escape(locateParagraphId)}"]`)
      target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      locateParagraphId = null
    }

    let rewriteSelection = false
    if (sentenceTarget) {
      const selection = findParagraphTextSelection(
        currentEditor.state.doc,
        sentenceTarget.paragraphId,
        sentenceTarget.start,
        sentenceTarget.end
      )
      highlightSentenceParagraph(sentenceTarget.paragraphId)
      if (selection) {
        currentEditor.commands.setTextSelection(selection)
        currentEditor.commands.focus()
        rewriteSelection = sentenceTarget.rewrite
      }
    }

    // 查询参数只负责一次跨页定位，消费后移除，避免用户在章节栏切换时被拉回旧章节。
    if (requestedId === id) {
      const shouldAutoGenerate = autoGenerateRequested.value
        && !content.trim()
        && (store.chapters.find((chapter) => chapter.id === id)?.outline.length ?? 0) > 0
      const query = { ...route.query }
      delete query.chapter
      delete query.scene
      delete query.paragraph
      delete query.start
      delete query.end
      delete query.rewrite
      delete query.autoGenerate
      suppressRouteCleanupWatch = true
      try {
        await router.replace({ query })
        await nextTick()
      } finally {
        suppressRouteCleanupWatch = false
      }
      if (rewriteSelection) runInline('改写语气')
      else if (shouldAutoGenerate) {
        generate({
          targetWords: 3000,
          model: 'basic',
          useStyleProfile: false,
          dialogueDensity: 'mid',
          instruction: '根据已经确认的开书骨架完成第一章，严格遵守章纲，结尾保留进入下一章的钩子。'
        })
      }
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
  mobileReadOnly.value = window.innerWidth <= 700
  editor.value?.setEditable(!mobileReadOnly.value)
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

async function createChapter() {
  if (mobileReadOnly.value || !store.project || !store.project.volumes.length) return
  chapterActionError.value = ''
  const currentId = store.activeId
  if (currentId) {
    try {
      await flush(currentId)
    } catch {
      chapterActionError.value = '当前章节保存失败，未创建新章。请先处理保存问题。'
      return
    }
    if (saveState.value === 'offline' || saveState.value === 'error' || saveState.value === 'conflict' || saveState.value === 'saving') {
      chapterActionError.value = '当前章节尚未安全保存，未创建新章。'
      return
    }
  }
  const current = store.active
  const volume = store.project.volumes.find((item) => item.id === current?.volumeId) ?? store.project.volumes.at(-1)
  if (!volume) return
  const afterIndex = current?.volumeId === volume.id
    ? current.index
    : (store.chapters.filter((chapter) => chapter.volumeId === volume.id).at(-1)?.index ?? 0)
  try {
    const created = await store.insertChapterAfter(volume.id, afterIndex)
    store.activeId = created.id
  } catch (error) {
    chapterActionError.value = error instanceof Error && error.message ? error.message : '新建章节失败，请稍后重试。'
  }
}

async function handleChapterReplaced(event: Event) {
  const detail = (event as CustomEvent<{ chapterIds?: string[] }>).detail
  const id = store.activeId
  if (!id || !detail?.chapterIds?.includes(id)) return
  const chapter = store.chapters.find((item) => item.id === id)
  if (!chapter || chapter.content === undefined || chapter.rev === undefined) return
  const serverContent = chapter.content
  // Keep unsaved local prose visible and turn the external replacement into the
  // same explicit conflict flow used by another browser tab.
  if (saveState.value === 'dirty' || saveState.value === 'saving' || saveState.value === 'conflict') {
    await recordConflict({
      chapterId: id,
      serverContentHtml: serverContent,
      serverRev: chapter.rev,
      clientContentHtml: html.value
    })
    chapterActionError.value = '自然化候选已更新云端正文；本地未保存修改仍在，请处理版本冲突。'
    return
  }
  const currentEditor = editor.value
  if (!currentEditor || currentEditor.isDestroyed) return
  currentEditor.commands.setContent(serverContent, { emitUpdate: false })
  html.value = serverContent
  markClean(id, serverContent)
  syncReviewAnchor()
  chapterActionError.value = '正文已同步自然化修改，版本已更新。'
}

function handleWorkspaceShortcut(event: KeyboardEvent) {
  if (!mobileReadOnly.value && (event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'f') {
    event.preventDefault()
    void openTextReplacement()
  }
}

onMounted(() => {
  shell.setCrumb(crumb.value)
  syncViewport()
  window.addEventListener('resize', syncViewport)
  window.addEventListener('moshu:draft-action', handleDraftAction as EventListener)
  window.addEventListener('moshu:chapter-replaced', handleChapterReplaced)
  window.addEventListener('keydown', handleWorkspaceShortcut)
  editor.value?.on('selectionUpdate', syncReviewAnchor)
})

onBeforeUnmount(() => {
  disposed = true
  abort?.()
  abort = null
  if (stoppedDraftTimer) clearTimeout(stoppedDraftTimer)
  if (reviewHighlightTimer) clearTimeout(reviewHighlightTimer)
  if (sentenceHighlightTimer) clearTimeout(sentenceHighlightTimer)
  editor.value?.off('selectionUpdate', syncReviewAnchor)
  window.removeEventListener('resize', syncViewport)
  window.removeEventListener('moshu:draft-action', handleDraftAction as EventListener)
  window.removeEventListener('moshu:chapter-replaced', handleChapterReplaced)
  window.removeEventListener('keydown', handleWorkspaceShortcut)
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
const replacementDisabledReason = computed(() => {
  if (!online.value) return '联网后才能执行全书替换。当前草稿仍保存在本地。'
  if (saveConflict.value) return '请先处理正文版本冲突，再执行全书替换。'
  if (saveState.value === 'error') return '仍有正文未保存成功，请先重试保存。'
  if (saveState.value === 'dirty' || saveState.value === 'saving') return '正在同步本地草稿，完成后即可预览。'
  return undefined
})
const reviewSubmitDisabledReason = computed(() => {
  if (!online.value) return '联网后才能提交审稿。'
  if (saveConflict.value) return '先处理正文版本冲突，再提交审稿。'
  if (saveState.value === 'error') return '正文尚未保存成功。'
  if (saveState.value === 'dirty' || saveState.value === 'saving') return '正文保存后即可提交审稿。'
  return undefined
})

function syncReviewAnchor() {
  const currentEditor = editor.value
  if (!currentEditor || currentEditor.isDestroyed) {
    reviewAnchor.value = undefined
    return
  }
  const { from, to, $from } = currentEditor.state.selection
  let depth = $from.depth
  while (depth > 0) {
    const node = $from.node(depth)
    const pid = typeof node.attrs.pid === 'string' ? node.attrs.pid : ''
    if (pid) {
      const start = $from.start(depth)
      const end = $from.end(depth)
      const selected = currentEditor.state.doc.textBetween(
        Math.max(from, start), Math.min(to, end), '\n'
      ).trim()
      reviewAnchor.value = {
        paragraphId: pid,
        paragraphText: node.textContent,
        selectedText: selected || undefined
      }
      return
    }
    depth -= 1
  }
  reviewAnchor.value = undefined
}

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

async function openTextReplacement() {
  replacementOpen.value = true
  await flushAll()
}

async function refreshAfterReplacement(run: TextReplacementRun) {
  const ids = run.affectedChapters.map((item) => item.chapterId)
  await store.reloadReplacedChapters(ids)
  const active = store.active
  if (!active || !ids.includes(active.id)) return
  const content = active.content ?? ''
  markClean(active.id, content)
  replaceEditorContent(content, active.rev)
}

async function locateReplacement(chapterId: string, paragraphId?: string) {
  locateParagraphId = paragraphId ?? null
  replacementOpen.value = false
  if (store.activeId !== chapterId) {
    store.activeId = chapterId
    return
  }
  await nextTick()
  if (locateParagraphId) {
    const target = document.querySelector(`[data-paragraph-id="${CSS.escape(locateParagraphId)}"]`)
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    locateParagraphId = null
  }
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

async function loadGenerationDrafts() {
  const id = store.activeId
  const sequence = ++draftLoadSequence
  if (!id) {
    generationDrafts.value = []
    return
  }
  draftsLoading.value = true
  try {
    const rows = await generationDraftApi.list(id)
    if (!disposed && sequence === draftLoadSequence && store.activeId === id) generationDrafts.value = rows
  } catch (error) {
    if (!disposed && sequence === draftLoadSequence) {
      generationError.value = error instanceof Error ? error.message : '候选草稿加载失败'
    }
  } finally {
    if (!disposed && sequence === draftLoadSequence) draftsLoading.value = false
  }
}

watch(() => store.activeId, () => void loadGenerationDrafts(), { immediate: true })

async function loadReviews() {
  const pid = projectId.value
  const cid = store.activeId
  const sequence = ++reviewLoadSequence
  if (!pid || !cid) {
    reviewWorkspace.value = undefined
    return
  }
  reviewLoading.value = true
  reviewError.value = ''
  try {
    const result = await reviewApi.get(pid, cid)
    if (!disposed && sequence === reviewLoadSequence && store.activeId === cid) reviewWorkspace.value = result
  } catch (error) {
    if (!disposed && sequence === reviewLoadSequence) {
      reviewWorkspace.value = undefined
      reviewError.value = error instanceof Error ? error.message : '审稿记录加载失败'
    }
  } finally {
    if (!disposed && sequence === reviewLoadSequence) reviewLoading.value = false
  }
}

watch([projectId, () => store.activeId], () => {
  reviewAnchor.value = undefined
  void loadReviews()
}, { immediate: true })

async function runReviewAction(action: () => Promise<ReviewWorkspace>, failure: string) {
  if (reviewBusy.value) return
  reviewBusy.value = true
  reviewError.value = ''
  try {
    reviewWorkspace.value = await action()
  } catch (error) {
    if (error instanceof ReviewConflictError) {
      await loadReviews()
      reviewError.value = '审稿记录已被其他成员更新，已刷新为最新状态。'
    } else {
      reviewError.value = error instanceof Error && error.message ? error.message : failure
    }
  } finally {
    reviewBusy.value = false
  }
}

async function submitReview(note: string) {
  const pid = projectId.value
  const cid = store.activeId
  if (!pid || !cid || reviewSubmitDisabledReason.value) return
  await flush(cid)
  if (reviewSubmitDisabledReason.value) {
    reviewError.value = reviewSubmitDisabledReason.value
    return
  }
  await runReviewAction(() => reviewApi.submit(pid, cid, note), '提交审稿失败')
}

async function addReviewComment(roundId: string, content: string) {
  const pid = projectId.value
  const anchor = reviewAnchor.value
  if (!pid || !anchor) return
  await runReviewAction(
    () => reviewApi.addComment(pid, roundId, anchor.paragraphId, anchor.selectedText, content),
    '添加批注失败'
  )
}

async function updateReviewComment(roundId: string, comment: ReviewComment, content: string) {
  const pid = projectId.value
  if (!pid) return
  await runReviewAction(
    () => reviewApi.updateComment(pid, roundId, comment.id, comment.revision, content),
    '修改批注失败'
  )
}

async function resolveReviewComment(roundId: string, comment: ReviewComment) {
  const pid = projectId.value
  if (!pid) return
  await runReviewAction(
    () => reviewApi.resolveComment(pid, roundId, comment.id, comment.revision),
    '更新批注状态失败'
  )
}

async function decideReview(round: ReviewRound, decision: 'approved' | 'changes_requested', note: string) {
  const pid = projectId.value
  if (!pid) return
  await runReviewAction(
    () => reviewApi.decide(pid, round.id, round.revision, decision, note),
    decision === 'approved' ? '批准审稿失败' : '退回修改失败'
  )
}

async function locateReviewComment(comment: ReviewComment) {
  paneTab.value = 'body'
  await nextTick()
  const target = document.querySelector<HTMLElement>(
    `.wk-pane-paper [data-paragraph-id="${CSS.escape(comment.paragraphId)}"]`
  )
  if (!target) {
    reviewError.value = `这条批注基于正文第 ${comment.bodyRevision} 版，当前正文中已没有对应段落。`
    return
  }
  target.scrollIntoView({ block: 'center', behavior: 'smooth' })
  target.classList.add('review-located-paragraph')
  if (reviewHighlightTimer) clearTimeout(reviewHighlightTimer)
  reviewHighlightTimer = setTimeout(() => target.classList.remove('review-located-paragraph'), 1800)
}

function attachDraftIdentity(meta: { draftId?: string }) {
  if (meta.draftId) editor.value?.commands.setDraftCandidateId(meta.draftId)
}

async function insertGenerationDraft(draft: GenerationDraftDetail) {
  if (!editor.value || draft.chapterId !== store.activeId || !draft.content) return
  paneTab.value = 'body'
  editor.value.chain().focus().insertPersistedDraft({ id: draft.id, runId: draft.runId, content: draft.content }).run()
}

async function rejectGenerationDraft(id: string) {
  try {
    await generationDraftApi.reject(id)
    editor.value?.commands.rejectDraftById(id)
    await loadGenerationDrafts()
  } catch (error) {
    generationError.value = error instanceof Error ? error.message : '候选草稿舍弃失败'
  }
}

function continueGenerationDraft(draft: GenerationDraftDetail) {
  if (!editor.value || draft.chapterId !== store.activeId || !draft.content || generating.value || mobileReadOnly.value) return
  generationError.value = ''
  generationErrorCode.value = ''
  generating.value = true
  paneTab.value = 'body'
  try {
    const stop = generationDraftApi.continue(
      draft.id,
      { targetWords: 800, model: 'basic', useStyleProfile: true, dialogueDensity: 'mid' },
      {
        onChunk: (text) => editor.value?.commands.appendDraftText(text),
        onMeta: attachDraftIdentity,
        onDone: (result) => {
          if (typeof result?.runId === 'string') editor.value?.commands.setDraftRunId(result.runId)
          if (typeof result?.draftId === 'string') editor.value?.commands.setDraftCandidateId(result.draftId)
          editor.value?.commands.setDraftStatus('pending')
          generating.value = false
          abort = null
          void loadGenerationDrafts()
        },
        onError: (error) => {
          const failure = describeGenerationError(error, '续写失败，请重试')
          generationError.value = failure.message
          generationErrorCode.value = failure.code
          editor.value?.commands.setDraftStatus('pending')
          generating.value = false
          abort = null
          void loadGenerationDrafts()
        }
      }
    )
    editor.value.chain().focus('end').insertAiDraft().run()
    editor.value.commands.appendDraftText(`${draft.content.trimEnd()}\n`)
    abort = stop
  } catch (error) {
    const failure = describeGenerationError(error, '续写失败，请重试')
    generationError.value = failure.message
    generationErrorCode.value = failure.code
    editor.value?.commands.setDraftStatus('pending')
    generating.value = false
    abort = null
    void loadGenerationDrafts()
  }
}

async function handleDraftAction(event: Event) {
  const detail = (event as CustomEvent<{ action?: string; draftId?: string }>).detail
  if (!detail?.draftId) return
  if (detail.action === 'reject') {
    await rejectGenerationDraft(detail.draftId)
    return
  }
  if (detail.action !== 'accept') return
  try {
    await generationDraftApi.accept(detail.draftId)
    editor.value?.commands.acceptDraftById(detail.draftId)
    await nextTick()
    if (store.activeId) await flush(store.activeId)
    await loadGenerationDrafts()
  } catch (error) {
    generationError.value = error instanceof Error ? error.message : '候选草稿采纳失败'
  }
}

function generate(options: GenerationControls) {
  if (!editor.value || generating.value || mobileReadOnly.value) return
  if (!store.activeId) return
  lastGenerationOptions.value = { ...options }
  generationError.value = ''
  generationErrorCode.value = ''
  generating.value = true
  editor.value.chain().focus('end').insertAiDraft().run()
  abort = streamChapter(
    { chapterId: store.activeId, ...options },
    {
      onChunk: (t) => editor.value?.commands.appendDraftText(t),
      onMeta: attachDraftIdentity,
      onDone: (result) => {
        if (typeof result?.runId === 'string') editor.value?.commands.setDraftRunId(result.runId)
        if (typeof result?.draftId === 'string') editor.value?.commands.setDraftCandidateId(result.draftId)
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
        abort = null
        void loadGenerationDrafts()
      },
      onError: (error) => {
        const failure = describeGenerationError(error, '生成失败，请重试')
        generationError.value = failure.message
        generationErrorCode.value = failure.code
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
        abort = null
        void loadGenerationDrafts()
      }
    }
  )
}

function stop() {
  abort?.()
  abort = null
  generating.value = false
  editor.value?.commands.setDraftStatus('pending')
  stoppedDraftTimer = setTimeout(() => void loadGenerationDrafts(), 500)
}

function describeGenerationError(error: unknown, fallback: string) {
  if (error instanceof GenerationError) {
    return { code: error.code, message: error.message }
  }
  if (error instanceof TypeError || (error instanceof Error && /fetch|network|连接/i.test(error.message))) {
    return { code: 'network_error', message: '网络连接失败，已保留已生成内容。检查网络后可以重试。' }
  }
  return { code: 'generation_error', message: error instanceof Error && error.message ? error.message : fallback }
}

function retryGeneration() {
  if (lastGenerationOptions.value && !generating.value) generate({ ...lastGenerationOptions.value })
}

function downgradeGeneration() {
  if (lastGenerationOptions.value && !generating.value) {
    generate({ ...lastGenerationOptions.value, model: 'basic' })
  }
}

function runInline(action: string) {
  if (!editor.value || !store.activeId || generating.value || mobileReadOnly.value) return
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
      onMeta: attachDraftIdentity,
      onDone: (result) => {
        if (typeof result?.runId === 'string') editor.value?.commands.setDraftRunId(result.runId)
        if (typeof result?.draftId === 'string') editor.value?.commands.setDraftCandidateId(result.draftId)
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
        abort = null
        void loadGenerationDrafts()
      },
      onError: (error) => {
        const failure = describeGenerationError(error, '行内生成失败，请重试')
        generationError.value = failure.message
        generationErrorCode.value = failure.code
        editor.value?.commands.setDraftStatus('pending')
        generating.value = false
        abort = null
        void loadGenerationDrafts()
      }
    }
  )
}

function editChapterPlan() {
  if (!store.active) return
  router.push({ path: toProject('outline'), query: { chapter: store.active.id } })
}

function openActiveScenePlan() {
  if (!store.active || !activeScene.value) return
  router.push({
    path: toProject('outline'),
    query: { chapter: store.active.id, mode: 'scenes', scene: activeScene.value.id }
  })
}

function openActiveSceneGuard() {
  if (!store.active) return
  router.push({ path: toProject('guard'), query: { chapter: store.active.id } })
}
</script>

<template>
  <div class="wk-cols" :style="{ gridTemplateColumns: cols }">
    <!-- 左：章节 -->
    <aside class="wk-pane wk-pane-left" :data-collapsed="!shell.leftOpen" aria-label="章节">
      <button class="panel-mobile-close" type="button" title="关闭章节" aria-label="关闭章节" @click="shell.leftOpen = false">
        <AppIcon name="close" />
      </button>
      <ChapterPanel v-if="shell.leftOpen" :create-chapter-action="createChapter" @pick="handleChapterPick" />
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
        <button v-if="!mobileReadOnly" class="paper-history-button" type="button" title="正文版本历史" aria-label="正文版本历史" @click="openVersionHistory">
          <AppIcon name="history" :size="16" />
        </button>
        <button v-if="!mobileReadOnly" class="paper-history-button" type="button" title="全书查找与替换" aria-label="全书查找与替换" @click="openTextReplacement">
          <AppIcon name="search" :size="15" />
        </button>
        <button class="wk-btn wk-btn-xs" type="button" :title="shell.zen ? '退出纯净模式 ⌘\\' : '纯净模式 ⌘\\'" @click="shell.toggleZen()">
          {{ shell.zen ? '退出纯净' : '纯净模式' }}
        </button>
        <button class="paper-panel-toggle paper-panel-toggle-right" type="button" :title="mobileReadOnly ? '打开灵感速记' : '打开 AI 面板'" @click="openPanel('right')">
          {{ mobileReadOnly ? '速记' : 'AI' }}
        </button>
      </div>

      <div v-if="mobileReadOnly" class="mobile-readonly-banner">
        <span><strong>手机只读</strong> 正文不会在小屏上被误改</span>
        <button type="button" @click="openPanel('right')">记一条灵感</button>
      </div>

      <section v-if="activeScene" class="scene-context-strip" aria-label="当前场景卡片">
        <span class="scene-context-index">场 {{ String(activeScene.order).padStart(2, '0') }}</span>
        <div class="scene-context-main">
          <strong>{{ activeScene.goal || '未填写场景目标' }}</strong>
          <span>{{ activeScene.obstacle ? `阻力：${activeScene.obstacle}` : '阻力尚未补充' }}</span>
        </div>
        <div v-if="activeScene.turn" class="scene-context-turn"><span>转折</span><strong>{{ activeScene.turn }}</strong></div>
        <div class="scene-context-actions">
          <button class="wk-btn wk-btn-xs" type="button" @click="openActiveScenePlan"><AppIcon name="outline" />场景卡片</button>
          <button class="wk-btn wk-btn-xs" type="button" @click="openActiveSceneGuard"><AppIcon name="guard" />本章守卫</button>
          <button type="button" title="收起当前场景" aria-label="收起当前场景" @click="activeScene = null"><AppIcon name="close" :size="14" /></button>
        </div>
      </section>

      <div v-if="chapterActionError" class="workspace-action-error" role="alert" aria-live="polite">
        {{ chapterActionError }}
        <button type="button" @click="chapterActionError = ''">知道了</button>
      </div>

      <div v-if="!online" :style="{ padding: '5px var(--u4)', fontSize: 'var(--fs-sm)', fontWeight: 700, background: 'var(--alert)', color: 'var(--on-alert)' }">
        {{ mobileReadOnly ? '离线中 · 当前正文保持只读' : '离线中 · 你可以继续写，内容已存在本地，联网后自动同步' }}
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
        <template v-if="!mobileReadOnly"><span :style="{ marginLeft: 'auto' }" /><button class="wk-btn wk-btn-xs" type="button" @click="discardDraft">使用云端版本</button><button class="wk-btn wk-btn-xs" type="button" data-primary="true" @click="recoverDraft">恢复本地草稿</button></template>
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
        <div v-if="!mobileReadOnly" :style="{ display: 'flex', gap: 'var(--u2)', alignItems: 'center', flexWrap: 'wrap' }">
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
        <button v-if="!mobileReadOnly" class="wk-btn wk-btn-xs" type="button" @click="retrySave">重试保存</button>
      </div>

      <!-- v-show 而非 v-if：编辑器不能因为切标签被卸载 -->
      <div v-show="paneTab === 'body'" class="prose">
        <EditorContent :editor="editor" />
        <AiFloatingBar v-if="!mobileReadOnly" @run="runInline" />
      </div>

      <div v-if="paneTab === 'outline'" class="prose" :style="{ paddingTop: 'var(--u6)' }">
        <div class="row" :style="{ justifyContent: 'space-between', marginBottom: 'var(--u3)', paddingBottom: 'var(--u3)', borderBottom: 'var(--hair) solid var(--line)' }">
          <div class="wk-label">章纲节点</div>
          <button v-if="!mobileReadOnly" class="wk-btn wk-btn-xs" type="button" @click="editChapterPlan">修改章纲</button>
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
    <aside class="wk-pane wk-pane-right" :data-collapsed="!shell.rightOpen" :aria-label="mobileReadOnly ? '灵感速记' : 'AI 面板'">
      <button class="panel-mobile-close" type="button" title="关闭 AI 面板" aria-label="关闭 AI 面板" @click="shell.rightOpen = false">
        <AppIcon name="close" />
      </button>
      <AiSidePanel
        v-if="shell.rightOpen"
        :generating="generating"
        :generation-error="generationError"
        :generation-error-code="generationErrorCode"
        :last-generation-options="lastGenerationOptions"
        :drafts="generationDrafts"
        :drafts-loading="draftsLoading"
        :review-workspace="reviewWorkspace"
        :review-anchor="reviewAnchor"
        :review-loading="reviewLoading"
        :review-busy="reviewBusy"
        :review-error="reviewError"
        :review-submit-disabled-reason="reviewSubmitDisabledReason"
        :mobile-read-only="mobileReadOnly"
        @generate="generate"
        @retry-generation="retryGeneration"
        @downgrade-generation="downgradeGeneration"
        @stop="stop"
        @insert-draft="insertGenerationDraft"
        @continue-draft="continueGenerationDraft"
        @reject-draft="rejectGenerationDraft"
        @refresh-drafts="loadGenerationDrafts"
        @refresh-reviews="loadReviews"
        @submit-review="submitReview"
        @add-review-comment="addReviewComment"
        @update-review-comment="updateReviewComment"
        @resolve-review-comment="resolveReviewComment"
        @decide-review="decideReview"
        @locate-review-comment="locateReviewComment"
      />
      <button v-else class="wk-stub" type="button" :title="mobileReadOnly ? '打开灵感速记' : '展开 AI 面板 ⌘J'" @click="shell.rightOpen = true">
        {{ mobileReadOnly ? '灵感速记' : 'AI 面板' }}
      </button>
    </aside>

    <CodexSuggestList />
    <ChapterVersionDrawer
      v-if="!mobileReadOnly && versionOpen && store.activeId"
      :chapter-id="store.activeId"
      :chapter-title="store.active?.title || '未命名章节'"
      :current-content="html"
      :restore-disabled-reason="restoreDisabledReason"
      :restoring="restoringVersion"
      :restore-error="restoreError"
      @close="versionOpen = false"
      @restore="restoreVersion"
    />
    <TextReplacementDrawer
      v-if="!mobileReadOnly && replacementOpen && store.project"
      :project-id="store.project.id"
      :active-chapter-id="store.activeId ?? undefined"
      :active-volume-id="store.active?.volumeId || undefined"
      :disabled-reason="replacementDisabledReason"
      @close="replacementOpen = false"
      @changed="refreshAfterReplacement"
      @locate="locateReplacement"
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
.scene-context-strip { min-width: 0; display: grid; grid-template-columns: auto minmax(180px, 1fr) minmax(140px, .7fr) auto; align-items: stretch; border-bottom: var(--hair) solid var(--line-strong); background: var(--panel); box-shadow: inset 3px 0 0 var(--alert); }
.scene-context-index { display: grid; place-items: center; min-width: 58px; padding: 9px 10px; border-right: var(--hair) solid var(--line); color: var(--alert-ink); font: 700 10px/1 var(--font-mono); }
.scene-context-main, .scene-context-turn { min-width: 0; display: grid; align-content: center; gap: 3px; padding: 8px 12px; border-right: var(--hair) solid var(--line); }
.scene-context-main strong, .scene-context-turn strong { overflow: hidden; color: var(--ink); font-size: var(--fs-sm); text-overflow: ellipsis; white-space: nowrap; }
.scene-context-main span, .scene-context-turn span { overflow: hidden; color: var(--ink-3); font-size: var(--fs-xs); text-overflow: ellipsis; white-space: nowrap; }
.scene-context-turn span { color: var(--alert-ink); font: 700 9px/1.2 var(--font-mono); }
.scene-context-actions { display: flex; align-items: center; gap: 5px; padding: 7px 9px; }
.scene-context-actions > button:last-child { width: 26px; height: 26px; display: grid; place-items: center; padding: 0; border: 0; color: var(--ink-3); background: transparent; cursor: pointer; }
.scene-context-actions > button:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.mobile-readonly-banner { min-height: 38px; display: flex; align-items: center; justify-content: space-between; gap: var(--u3); padding: 6px var(--u3); border-bottom: var(--hair) solid var(--primary-line); color: var(--ink-3); background: var(--primary-soft); font-size: var(--fs-xs); }
.mobile-readonly-banner strong { margin-right: 5px; color: var(--primary); }
.mobile-readonly-banner button { flex: none; min-height: 26px; padding: 0 8px; border: var(--hair) solid var(--primary); border-radius: 3px; color: var(--primary); background: transparent; font-size: var(--fs-xs); cursor: pointer; }
.mobile-readonly-banner button:hover { color: var(--paper); background: var(--primary); }
.workspace-action-error { display: flex; align-items: center; justify-content: space-between; gap: var(--u3); padding: 7px var(--u4); color: var(--alert-ink); background: var(--alert-soft); border-bottom: var(--hair) solid var(--alert-line); font-size: var(--fs-sm); line-height: 1.5; }
.workspace-action-error button { flex: none; padding: 2px 6px; color: var(--alert-ink); background: transparent; border: var(--hair) solid var(--alert); border-radius: 3px; font-size: var(--fs-xs); cursor: pointer; }
@media (max-width: 1100px) {
  .scene-context-strip { grid-template-columns: auto minmax(0, 1fr) auto; }
  .scene-context-turn { display: none; }
}
@media (max-width: 700px) {
  .scene-context-strip { grid-template-columns: auto minmax(0, 1fr); }
  .scene-context-actions { grid-column: 1 / -1; justify-content: flex-end; border-top: var(--hair) solid var(--line); }
}
:deep(.review-located-paragraph) {
  background: var(--alert-soft);
  box-shadow: -4px 0 0 var(--alert);
  transition: background 160ms ease, box-shadow 160ms ease;
}
:deep(.sentence-risk-located) {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  box-shadow: -4px 0 0 var(--accent);
  transition: background 180ms ease, box-shadow 180ms ease;
}
</style>
