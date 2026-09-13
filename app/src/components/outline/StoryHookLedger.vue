<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storyHooksApi } from '@/api/story-hooks'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { Chapter, StoryHook, StoryHookStatus } from '@/types'

interface HookDraft {
  sourceChapterId: string
  hookType: string
  concreteEvent: string
  unresolvedQuestion: string
  payoffByChapter?: number
  payoffChapterId: string
  resolution: string
}

const props = defineProps<{
  projectId: string
  chapters: Chapter[]
  focusChapterId?: string
}>()

const hooks = ref<StoryHook[]>([])
const selectedId = ref<string | null>(null)
const filter = ref<'active' | 'closed' | 'all'>('active')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const creating = ref(false)
const resolving = ref(false)
const draft = ref<HookDraft>(emptyDraft())

const sortedChapters = computed(() => [...props.chapters].sort((a, b) => a.index - b.index))
const numberedChapters = computed(() => sortedChapters.value.map((chapter, index) => ({
  chapter,
  number: index + 1
})))
const chapterById = computed(() => new Map(props.chapters.map((chapter) => [chapter.id, chapter])))
const chapterNumberById = computed(() => new Map(numberedChapters.value.map((item) => [item.chapter.id, item.number])))
const focusChapterNumber = computed(() => chapterNumberById.value.get(props.focusChapterId ?? '') ?? 1)
const selected = computed(() => hooks.value.find((hook) => hook.id === selectedId.value) ?? null)
const visibleHooks = computed(() => hooks.value.filter((hook) => {
  if (filter.value === 'active') return hook.status === 'open' || hook.status === 'deferred'
  if (filter.value === 'closed') return hook.status === 'resolved' || hook.status === 'abandoned'
  return true
}))
const counts = computed(() => ({
  active: hooks.value.filter((hook) => hook.status === 'open' || hook.status === 'deferred').length,
  due: hooks.value.filter((hook) => urgency(hook) === 'due').length,
  overdue: hooks.value.filter((hook) => urgency(hook) === 'overdue').length,
  resolved: hooks.value.filter((hook) => hook.status === 'resolved').length
}))
const sourceChapter = computed(() => chapterById.value.get(draft.value.sourceChapterId))
const canSave = computed(() => {
  const due = draft.value.payoffByChapter
  return !!draft.value.sourceChapterId
    && !!draft.value.hookType.trim()
    && !!draft.value.concreteEvent.trim()
    && !!draft.value.unresolvedQuestion.trim()
    && (due === undefined || (!!sourceChapter.value && due > (chapterNumberById.value.get(sourceChapter.value.id) ?? 0)))
})

watch(() => props.projectId, () => void load(), { immediate: true })

function emptyDraft(): HookDraft {
  const source = props?.chapters?.find((chapter) => chapter.id === props.focusChapterId)
    ?? props?.chapters?.[0]
  return {
    sourceChapterId: source?.id ?? '',
    hookType: '',
    concreteEvent: '',
    unresolvedQuestion: '',
    payoffByChapter: undefined,
    payoffChapterId: '',
    resolution: ''
  }
}

function draftFrom(hook: StoryHook): HookDraft {
  return {
    sourceChapterId: hook.sourceChapterId,
    hookType: hook.hookType,
    concreteEvent: hook.concreteEvent,
    unresolvedQuestion: hook.unresolvedQuestion,
    payoffByChapter: hook.payoffByChapter,
    payoffChapterId: hook.payoffChapterId ?? '',
    resolution: hook.resolution
  }
}

async function load() {
  const projectId = props.projectId
  loading.value = true
  error.value = ''
  try {
    const loaded = await storyHooksApi.list(projectId)
    if (props.projectId !== projectId) return
    hooks.value = loaded
    if (selectedId.value) {
      const current = loaded.find((hook) => hook.id === selectedId.value)
      if (current) draft.value = draftFrom(current)
      else selectedId.value = null
    }
  } catch {
    error.value = '伏笔台账没有载入，请重试。'
  } finally {
    if (props.projectId === projectId) loading.value = false
  }
}

function selectHook(hook: StoryHook) {
  selectedId.value = hook.id
  creating.value = false
  resolving.value = false
  draft.value = draftFrom(hook)
  error.value = ''
  notice.value = ''
}

function beginCreate() {
  selectedId.value = null
  creating.value = true
  resolving.value = false
  draft.value = emptyDraft()
  error.value = ''
  notice.value = ''
}

function sourceLabel(hook: StoryHook) {
  const chapter = chapterById.value.get(hook.sourceChapterId)
  const number = chapterNumberById.value.get(hook.sourceChapterId)
  return chapter && number ? `第 ${number} 章 · ${chapter.title || '未命名'}` : '原章节已移除'
}

function payoffLabel(hook: StoryHook) {
  if (hook.payoffChapterId) {
    const chapter = chapterById.value.get(hook.payoffChapterId)
    const number = chapterNumberById.value.get(hook.payoffChapterId)
    if (chapter && number) return `第 ${number} 章已兑现`
  }
  return hook.payoffByChapter ? `计划第 ${hook.payoffByChapter} 章` : '尚未排期'
}

function urgency(hook: StoryHook) {
  if (hook.status === 'resolved') return 'resolved'
  if (hook.status === 'abandoned') return 'abandoned'
  if (!hook.payoffByChapter) return hook.status === 'deferred' ? 'deferred' : 'open'
  const focus = focusChapterNumber.value
  if (hook.payoffByChapter < focus) return 'overdue'
  if (hook.payoffByChapter === focus) return 'due'
  return hook.status === 'deferred' ? 'deferred' : 'upcoming'
}

function urgencyLabel(hook: StoryHook) {
  return {
    resolved: '已兑现', abandoned: '已放弃', deferred: '已延期', open: '未排期',
    overdue: '已逾期', due: '本章到期', upcoming: '待推进'
  }[urgency(hook)]
}

function setDueChapter(event: Event) {
  const raw = (event.target as HTMLInputElement).value
  draft.value.payoffByChapter = raw ? Number(raw) : undefined
}

function replaceHook(updated: StoryHook) {
  const index = hooks.value.findIndex((hook) => hook.id === updated.id)
  if (index >= 0) hooks.value[index] = updated
  else hooks.value.push(updated)
  selectHook(updated)
}

async function saveHook() {
  if (!canSave.value || saving.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    if (creating.value) {
      const created = await storyHooksApi.create(props.projectId, {
        sourceChapterId: draft.value.sourceChapterId,
        hookType: draft.value.hookType.trim(),
        concreteEvent: draft.value.concreteEvent.trim(),
        unresolvedQuestion: draft.value.unresolvedQuestion.trim(),
        payoffByChapter: draft.value.payoffByChapter
      })
      replaceHook(created)
      notice.value = '伏笔已加入台账，后续生成会持续追踪。'
    } else if (selected.value) {
      const updated = await storyHooksApi.update(props.projectId, selected.value.id, {
        expectedRevision: selected.value.revision,
        hookType: draft.value.hookType.trim(),
        concreteEvent: draft.value.concreteEvent.trim(),
        unresolvedQuestion: draft.value.unresolvedQuestion.trim(),
        payoffByChapter: draft.value.payoffByChapter ?? null
      })
      replaceHook(updated)
      notice.value = '伏笔计划已保存。'
    }
  } catch (caught) {
    await handleFailure(caught, '伏笔没有保存，请检查是否存在重复内容。')
  } finally {
    saving.value = false
  }
}

async function changeStatus(status: StoryHookStatus) {
  const hook = selected.value
  if (!hook || saving.value) return
  if (status === 'abandoned' && !window.confirm('放弃后，这条伏笔不再进入生成上下文。继续吗？')) return
  saving.value = true
  error.value = ''
  try {
    const updated = await storyHooksApi.update(props.projectId, hook.id, {
      expectedRevision: hook.revision,
      status
    })
    replaceHook(updated)
    notice.value = status === 'deferred' ? '已延期，仍会保留在生成上下文中。'
      : status === 'open' ? '伏笔已重新打开。' : '伏笔已从后续生成中移除。'
  } catch (caught) {
    await handleFailure(caught, '状态没有更新，请重试。')
  } finally {
    saving.value = false
  }
}

function beginResolve() {
  const preferred = props.chapters.find((chapter) => chapter.id === props.focusChapterId)
  draft.value.payoffChapterId ||= preferred?.id ?? ''
  resolving.value = true
  error.value = ''
  notice.value = ''
}

async function resolveHook() {
  const hook = selected.value
  if (!hook || !draft.value.payoffChapterId || !draft.value.resolution.trim() || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await storyHooksApi.update(props.projectId, hook.id, {
      expectedRevision: hook.revision,
      status: 'resolved',
      payoffChapterId: draft.value.payoffChapterId,
      resolution: draft.value.resolution.trim()
    })
    replaceHook(updated)
    notice.value = '伏笔已兑现，结果会作为后续事实继续保留。'
  } catch (caught) {
    await handleFailure(caught, '兑现结果没有保存，请重试。')
  } finally {
    saving.value = false
  }
}

async function handleFailure(caught: unknown, fallback: string) {
  if (caught instanceof Error && caught.message.includes('revision_conflict')) {
    await load()
    error.value = '这条伏笔已在其他位置修改，已载入最新版本。'
    return
  }
  error.value = fallback
}
</script>

<template>
  <section class="hook-ledger-host" aria-labelledby="hook-ledger-title">
    <main class="hook-ledger-board">
      <header class="hook-ledger-head">
        <div>
          <span class="kicker">STORY DEBT LEDGER</span>
          <h2 id="hook-ledger-title">伏笔台账</h2>
          <p>记录已经发生的钩子和仍欠读者的答案。到期项目会直接进入对应章节的生成上下文。</p>
        </div>
        <button class="wk-btn" data-primary="true" type="button" @click="beginCreate">
          <AppIcon name="plus" />新增伏笔
        </button>
      </header>

      <div class="hook-ledger-summary" aria-label="伏笔统计">
        <div><strong>{{ counts.active }}</strong><span>未兑现</span></div>
        <div :data-alert="counts.due > 0"><strong>{{ counts.due }}</strong><span>本章到期</span></div>
        <div :data-alert="counts.overdue > 0"><strong>{{ counts.overdue }}</strong><span>已经逾期</span></div>
        <div><strong>{{ counts.resolved }}</strong><span>已兑现</span></div>
      </div>

      <div class="hook-ledger-toolbar">
        <div class="hook-filter" role="group" aria-label="筛选伏笔状态">
          <button v-for="item in ([['active', '未兑现'], ['closed', '已结束'], ['all', '全部']] as const)" :key="item[0]" type="button" :aria-pressed="filter === item[0]" @click="filter = item[0]">{{ item[1] }}</button>
        </div>
        <span>以第 {{ focusChapterNumber }} 章为当前写作位置</span>
      </div>

      <p v-if="loading" class="hook-empty" role="status">正在载入伏笔台账…</p>
      <p v-else-if="error && !hooks.length" class="hook-error" role="alert">{{ error }}</p>
      <div v-else-if="visibleHooks.length" class="hook-list">
        <article
          v-for="hook in visibleHooks"
          :key="hook.id"
          :data-hook-id="hook.id"
          :data-active="selectedId === hook.id"
          :data-urgency="urgency(hook)"
          tabindex="0"
          @click="selectHook(hook)"
          @keydown.enter="selectHook(hook)"
        >
          <header>
            <span>{{ hook.hookType }}</span>
            <strong>{{ urgencyLabel(hook) }}</strong>
          </header>
          <p>{{ hook.concreteEvent }}</p>
          <blockquote>{{ hook.unresolvedQuestion }}</blockquote>
          <div class="hook-route" aria-label="伏笔埋设与兑现位置">
            <span>{{ sourceLabel(hook) }}</span>
            <span>{{ payoffLabel(hook) }}</span>
          </div>
        </article>
      </div>
      <div v-else class="hook-empty">
        <strong>{{ filter === 'active' ? '当前没有未兑现伏笔' : '这个筛选下没有记录' }}</strong>
        <p>把章尾已经启动的动作或尚未回答的问题登记进来，生成下一章时系统会主动承接。</p>
        <button v-if="filter === 'active'" class="wk-btn" type="button" @click="beginCreate"><AppIcon name="plus" />登记第一条</button>
      </div>
    </main>

    <aside class="hook-ledger-editor">
      <template v-if="creating || selected">
        <header>
          <div><span>{{ creating ? 'NEW STORY DEBT' : `REVISION ${selected?.revision ?? 1}` }}</span><h3>{{ creating ? '登记伏笔' : '处理伏笔' }}</h3></div>
          <button v-if="creating" type="button" aria-label="取消新增" title="取消新增" @click="creating = false"><AppIcon name="close" /></button>
        </header>

        <label class="hook-field">
          <span>埋设章节</span>
          <select v-model="draft.sourceChapterId" :disabled="!creating">
            <option v-for="item in numberedChapters" :key="item.chapter.id" :value="item.chapter.id">第 {{ item.number }} 章 · {{ item.chapter.title || '未命名' }}</option>
          </select>
        </label>
        <label class="hook-field">
          <span>钩子类型</span>
          <input v-model="draft.hookType" type="text" maxlength="50" list="hook-type-options" placeholder="例如：身份疑云">
          <datalist id="hook-type-options"><option value="身份疑云" /><option value="证据缺口" /><option value="倒计时" /><option value="危险逼近" /><option value="关系裂痕" /><option value="资源承诺" /></datalist>
        </label>
        <label class="hook-field">
          <span>已经发生的具体事件</span>
          <textarea v-model="draft.concreteEvent" rows="4" maxlength="4000" placeholder="必须是读者已经看见的动作、物件或局面变化"></textarea>
        </label>
        <label class="hook-field">
          <span>还欠读者的答案</span>
          <textarea v-model="draft.unresolvedQuestion" rows="3" maxlength="2000" placeholder="这件事接下来必须回答什么"></textarea>
        </label>
        <label class="hook-field">
          <span>预计最晚兑现章 <small>可留空</small></span>
          <input :value="draft.payoffByChapter ?? ''" type="number" min="1" placeholder="章节序号" @input="setDueChapter">
          <small v-if="draft.payoffByChapter && sourceChapter && draft.payoffByChapter <= (chapterNumberById.get(sourceChapter.id) ?? 0)" class="is-error">兑现章必须晚于埋设章。</small>
        </label>

        <section v-if="resolving || selected?.status === 'resolved'" class="hook-resolution">
          <label class="hook-field"><span>实际兑现章节</span><select v-model="draft.payoffChapterId"><option value="">选择章节</option><option v-for="item in numberedChapters" :key="item.chapter.id" :value="item.chapter.id">第 {{ item.number }} 章 · {{ item.chapter.title || '未命名' }}</option></select></label>
          <label class="hook-field"><span>最终答案与可见后果</span><textarea v-model="draft.resolution" rows="4" maxlength="4000" placeholder="谁做了什么，局面因此发生了什么变化"></textarea></label>
        </section>

        <p v-if="error" class="hook-error" role="alert">{{ error }}</p>
        <p v-else-if="notice" class="hook-notice" role="status">{{ notice }}</p>

        <div v-if="creating" class="hook-editor-actions">
          <button class="wk-btn" data-primary="true" type="button" :disabled="!canSave || saving" @click="saveHook">{{ saving ? '保存中…' : '加入台账' }}</button>
        </div>
        <div v-else-if="selected" class="hook-editor-actions is-stacked">
          <button v-if="selected.status === 'open' || selected.status === 'deferred'" class="wk-btn" data-primary="true" type="button" :disabled="!canSave || saving" @click="saveHook">保存修改</button>
          <button v-if="resolving && selected.status !== 'resolved'" class="wk-btn" type="button" :disabled="!draft.payoffChapterId || !draft.resolution.trim() || saving" @click="resolveHook"><AppIcon name="check" />确认兑现</button>
          <button v-else-if="selected.status === 'open' || selected.status === 'deferred'" class="wk-btn" type="button" @click="beginResolve"><AppIcon name="check" />标记兑现</button>
          <button v-if="selected.status === 'open'" class="wk-btn" type="button" :disabled="saving" @click="changeStatus('deferred')"><AppIcon name="history" />延期</button>
          <button v-else-if="selected.status === 'deferred' || selected.status === 'resolved' || selected.status === 'abandoned'" class="wk-btn" type="button" :disabled="saving" @click="changeStatus('open')"><AppIcon name="restore" />重新打开</button>
          <button v-if="selected.status === 'open' || selected.status === 'deferred'" class="wk-btn is-danger" type="button" :disabled="saving" @click="changeStatus('abandoned')"><AppIcon name="trash" />放弃</button>
        </div>
      </template>
      <div v-else class="hook-editor-empty">
        <span class="kicker">处理区</span>
        <strong>选择一条伏笔查看细节</strong>
        <p>这里可以调整预计兑现章，或在正文真正回答问题后记录兑现结果。</p>
      </div>
    </aside>
  </section>
</template>

<style scoped>
.hook-ledger-host { grid-column: 1 / -1; min-width: 0; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 390px; background: var(--panel-sunken); }
.hook-ledger-board { min-width: 0; overflow: auto; padding: 28px 30px 48px; }
.hook-ledger-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding-bottom: 18px; border-bottom: 2px solid var(--ink); }
.hook-ledger-head h2 { margin: 5px 0 6px; font: 700 24px/1.2 var(--font-prose); letter-spacing: 0; }
.hook-ledger-head p { max-width: 680px; margin: 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.65; }
.hook-ledger-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 20px; border-block: var(--hair) solid var(--line-strong); }
.hook-ledger-summary > div { min-width: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: baseline; gap: 8px; padding: 13px 14px; border-right: var(--hair) solid var(--line); }
.hook-ledger-summary > div:last-child { border-right: 0; }
.hook-ledger-summary strong { font: 700 22px/1 var(--font-mono); }
.hook-ledger-summary span { color: var(--ink-4); font-size: var(--fs-xs); }
.hook-ledger-summary [data-alert='true'] strong, .hook-ledger-summary [data-alert='true'] span { color: var(--alert-ink); }
.hook-ledger-toolbar { min-height: 54px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.hook-ledger-toolbar > span { color: var(--ink-4); font: var(--fs-xs)/1.4 var(--font-mono); }
.hook-filter { display: flex; border: var(--hair) solid var(--line-strong); }
.hook-filter button { min-width: 72px; height: 30px; padding: 0 10px; border: 0; border-right: var(--hair) solid var(--line); background: var(--paper); color: var(--ink-3); font: inherit; font-size: var(--fs-xs); cursor: pointer; }
.hook-filter button:last-child { border-right: 0; }
.hook-filter button[aria-pressed='true'] { background: var(--ink); color: var(--paper); }
.hook-list { display: grid; gap: 9px; }
.hook-list article { padding: 14px 16px 15px; border: var(--hair) solid var(--line-strong); border-left: 3px solid transparent; border-radius: 4px; background: var(--paper); cursor: pointer; }
.hook-list article:hover, .hook-list article:focus-visible { border-left-color: var(--primary); outline: none; }
.hook-list article[data-active='true'] { border-left-color: var(--primary); background: var(--primary-soft); }
.hook-list article[data-urgency='due'], .hook-list article[data-urgency='overdue'] { border-left-color: var(--alert); }
.hook-list article[data-urgency='resolved'], .hook-list article[data-urgency='abandoned'] { opacity: .72; }
.hook-list article > header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.hook-list article > header span { color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.hook-list article > header strong { padding: 3px 6px; border: var(--hair) solid var(--line-strong); color: var(--ink-3); font-size: 10px; }
.hook-list article[data-urgency='due'] > header strong, .hook-list article[data-urgency='overdue'] > header strong { border-color: var(--alert); background: var(--alert-soft); color: var(--alert-ink); }
.hook-list article > p { margin: 10px 0 6px; color: var(--ink); font-weight: 700; line-height: 1.55; }
.hook-list blockquote { margin: 0; color: var(--ink-3); font-family: var(--font-prose); line-height: 1.6; }
.hook-route { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 42px; margin-top: 14px; color: var(--ink-4); font: 11px/1.4 var(--font-mono); }
.hook-route::before { content: ''; position: absolute; top: 50%; left: calc(50% - 14px); width: 28px; border-top: var(--hair) solid var(--line-strong); }
.hook-route span:last-child { text-align: right; }
.hook-empty { max-width: 480px; margin: 60px auto; color: var(--ink-3); text-align: center; line-height: 1.65; }
.hook-empty strong { color: var(--ink); }
.hook-empty .wk-btn { margin: 8px auto 0; }
.hook-ledger-editor { min-width: 0; overflow: auto; padding: 22px 20px 32px; border-left: var(--hair) solid var(--line-strong); background: var(--paper); }
.hook-ledger-editor > header { min-height: 54px; display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 18px; padding-bottom: 12px; border-bottom: var(--hair) solid var(--line-strong); }
.hook-ledger-editor header span { color: var(--ink-4); font: 9px/1 var(--font-mono); }
.hook-ledger-editor h3 { margin: 6px 0 0; font-size: 18px; letter-spacing: 0; }
.hook-ledger-editor > header button { width: 30px; height: 30px; display: grid; place-items: center; border: 0; background: transparent; color: var(--ink-3); cursor: pointer; }
.hook-field { display: grid; gap: 6px; margin-bottom: 14px; color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.hook-field > span { display: flex; justify-content: space-between; }
.hook-field > span small { color: var(--ink-4); font-weight: 400; }
.hook-field input, .hook-field select, .hook-field textarea { width: 100%; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink); font: inherit; }
.hook-field input, .hook-field select { height: 38px; padding: 0 10px; }
.hook-field textarea { min-height: 72px; padding: 9px 10px; line-height: 1.6; resize: vertical; }
.hook-field input:focus-visible, .hook-field select:focus-visible, .hook-field textarea:focus-visible, .hook-ledger-editor button:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.hook-field small.is-error, .hook-error { color: var(--alert-ink); }
.hook-resolution { margin: 18px -20px 0; padding: 18px 20px 4px; border-block: var(--hair) solid var(--primary); background: var(--primary-soft); }
.hook-error, .hook-notice { margin: 12px 0; font-size: var(--fs-sm); line-height: 1.55; }
.hook-notice { color: var(--primary); }
.hook-editor-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 18px; padding-top: 14px; border-top: var(--hair) solid var(--line-strong); }
.hook-editor-actions.is-stacked .wk-btn:first-child { flex: 1 0 100%; justify-content: center; }
.hook-editor-actions .is-danger { color: var(--alert-ink); }
.hook-editor-empty { margin-top: 40px; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.65; text-align: center; }
.hook-editor-empty strong { display: block; margin-top: 18px; color: var(--ink); }

@media (max-width: 840px) {
  .hook-ledger-host { grid-template-columns: minmax(0, 1fr); }
  .hook-ledger-board { min-height: 520px; padding: 22px 16px 36px; }
  .hook-ledger-editor { min-height: 460px; border-top: var(--hair) solid var(--line-strong); border-left: 0; }
}

@media (max-width: 560px) {
  .hook-ledger-head { align-items: stretch; flex-direction: column; }
  .hook-ledger-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hook-ledger-summary > div:nth-child(2) { border-right: 0; }
  .hook-ledger-summary > div:nth-child(-n + 2) { border-bottom: var(--hair) solid var(--line); }
  .hook-ledger-toolbar { align-items: flex-start; flex-direction: column; padding-block: 12px; }
  .hook-route { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  .hook-route::before { display: none; }
  .hook-route span:last-child { text-align: left; }
}
</style>
