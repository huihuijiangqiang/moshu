<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { contentApi } from '@/api/content'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { ChapterVersionDetail, ChapterVersionSummary } from '@/types'
import { chapterDiff, htmlPlainText } from './chapter-diff'

const props = defineProps<{
  chapterId: string
  chapterTitle: string
  currentContent: string
  restoreDisabledReason?: string
  restoring?: boolean
  restoreError?: string
}>()

const emit = defineEmits<{
  close: []
  restore: [revision: number]
}>()

const versions = ref<ChapterVersionSummary[]>([])
const selected = ref<ChapterVersionDetail | null>(null)
const loadingList = ref(false)
const loadingDetail = ref(false)
const loadError = ref('')
const confirmRevision = ref<number | null>(null)
let listRequestSequence = 0
let detailRequestSequence = 0

const triggerLabels: Record<string, string> = {
  manual: '正文保存',
  autosave: '自动保存',
  accept_draft: '采纳 AI 草稿',
  restore_version: '恢复历史版本',
  bulk_replace: '全书替换',
  bulk_replace_undo: '撤销全书替换'
}

const diff = computed(() => selected.value ? chapterDiff(selected.value.content, props.currentContent) : [])
const preview = computed(() => selected.value ? htmlPlainText(selected.value.content) : '')
const canRestore = computed(() => Boolean(
  selected.value && !selected.value.isCurrent && !props.restoreDisabledReason && !props.restoring
))

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  })
}

function triggerLabel(trigger: string) {
  return triggerLabels[trigger] ?? trigger
}

async function selectVersion(version: ChapterVersionSummary) {
  if (selected.value?.rev === version.rev) return
  const sequence = ++detailRequestSequence
  loadingDetail.value = true
  loadError.value = ''
  confirmRevision.value = null
  try {
    const detail = await contentApi.getChapterVersion(props.chapterId, version.rev)
    if (sequence === detailRequestSequence) selected.value = detail
  } catch {
    if (sequence === detailRequestSequence) loadError.value = '历史正文加载失败，请重试。'
  } finally {
    if (sequence === detailRequestSequence) loadingDetail.value = false
  }
}

async function loadVersions() {
  const sequence = ++listRequestSequence
  detailRequestSequence += 1
  loadingList.value = true
  loadError.value = ''
  selected.value = null
  confirmRevision.value = null
  try {
    const rows = await contentApi.listChapterVersions(props.chapterId)
    if (sequence !== listRequestSequence) return
    versions.value = rows
    loadingList.value = false
    if (rows[0]) await selectVersion(rows[0])
  } catch {
    if (sequence === listRequestSequence) loadError.value = '版本记录加载失败，请重试。'
  } finally {
    if (sequence === listRequestSequence) loadingList.value = false
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
}

watch(() => props.chapterId, () => void loadVersions(), { immediate: true })
window.addEventListener('keydown', onKeydown)
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="version-backdrop" @click.self="emit('close')">
    <section class="version-drawer" role="dialog" aria-modal="true" aria-labelledby="version-title">
      <header class="version-head">
        <div>
          <div class="wk-label">正文版本</div>
          <h2 id="version-title">{{ chapterTitle }}</h2>
        </div>
        <button class="version-icon-btn" type="button" title="关闭版本历史" aria-label="关闭版本历史" @click="emit('close')">
          <AppIcon name="close" />
        </button>
      </header>

      <div class="version-body">
        <nav class="version-list" aria-label="历史版本">
          <div class="version-list-label">
            <span>保存记录</span>
            <span>{{ versions.length }}</span>
          </div>
          <div v-if="loadingList" class="version-state">正在读取版本记录…</div>
          <div v-else-if="!versions.length && !loadError" class="version-state">首次保存正文后，这里会出现版本记录。</div>
          <button
            v-for="version in versions"
            :key="version.id"
            class="version-row"
            type="button"
            :aria-selected="selected?.rev === version.rev"
            @click="selectVersion(version)"
          >
            <span class="version-row-top">
              <strong>第 {{ version.rev }} 版</strong>
              <span v-if="version.isCurrent" class="version-current">当前</span>
            </span>
            <span class="version-row-time">{{ formatTime(version.createdAt) }} · {{ version.words.toLocaleString() }} 字</span>
            <span class="version-row-trigger">{{ triggerLabel(version.trigger) }}</span>
            <span class="version-row-excerpt">{{ version.excerpt }}</span>
          </button>
        </nav>

        <main class="version-inspector">
          <div v-if="loadError" class="version-state version-state-error">
            <strong>{{ loadError }}</strong>
            <button class="wk-btn wk-btn-xs" type="button" @click="loadVersions">重试</button>
          </div>
          <div v-else-if="loadingDetail" class="version-state">正在读取历史正文…</div>
          <template v-else-if="selected">
            <div class="version-inspector-head">
              <div>
                <div class="wk-label">与当前正文比较</div>
                <strong>第 {{ selected.rev }} 版 · {{ formatTime(selected.createdAt) }}</strong>
              </div>
              <span class="version-legend"><i data-kind="removed" />旧版 <i data-kind="added" />当前</span>
            </div>
            <div class="version-scroll">
              <div class="version-diff" aria-label="版本差异">
                <div
                  v-for="(row, index) in diff"
                  :key="`${row.kind}-${index}`"
                  class="version-diff-row"
                  :data-kind="row.kind"
                >
                  <span>{{ row.kind === 'added' ? '+' : row.kind === 'removed' ? '−' : '' }}</span>
                  <p v-if="row.kind !== 'omitted'">{{ row.text }}</p>
                  <p v-else>中间 {{ row.count }} 段相同内容已折叠</p>
                </div>
              </div>
              <details class="version-preview">
                <summary>查看这一版完整纯文本</summary>
                <pre>{{ preview || '空白正文' }}</pre>
              </details>
            </div>
            <footer class="version-actions">
              <p v-if="restoreError" class="version-action-error">{{ restoreError }}</p>
              <p v-else-if="restoreDisabledReason" class="version-action-note">{{ restoreDisabledReason }}</p>
              <p v-else-if="selected.isCurrent" class="version-action-note">这是当前正在编辑的版本。</p>
              <template v-if="confirmRevision === selected.rev">
                <p>恢复后会生成一个新版本，现有历史不会被删除。</p>
                <button class="wk-btn" type="button" @click="confirmRevision = null">取消</button>
                <button class="wk-btn" data-primary="true" type="button" :disabled="!canRestore" @click="emit('restore', selected.rev)">
                  {{ restoring ? '正在恢复…' : '确认恢复' }}
                </button>
              </template>
              <button
                v-else
                class="wk-btn"
                type="button"
                :disabled="!canRestore"
                @click="confirmRevision = selected.rev"
              >
                <AppIcon name="restore" :size="15" />恢复这一版
              </button>
            </footer>
          </template>
        </main>
      </div>
    </section>
  </div>
</template>

<style scoped>
.version-backdrop { position: fixed; inset: 0; z-index: 85; background: rgb(20 24 25 / 38%); }
.version-drawer { position: absolute; inset: 0 0 0 auto; display: grid; grid-template-rows: 58px minmax(0, 1fr); width: min(840px, 92vw); color: var(--ink); background: var(--paper); border-left: var(--rule) solid var(--line-strong); box-shadow: -18px 0 42px rgb(20 24 25 / 18%); }
.version-head { display: flex; align-items: center; justify-content: space-between; gap: var(--u4); padding: 0 var(--u4); background: var(--panel); border-bottom: var(--hair) solid var(--line-strong); }
.version-head h2 { margin: 2px 0 0; font-family: var(--font-serif); font-size: 17px; line-height: 1.2; letter-spacing: 0; }
.version-icon-btn { display: grid; place-items: center; width: 32px; height: 32px; padding: 0; color: var(--ink-2); background: transparent; border: var(--hair) solid transparent; cursor: pointer; }
.version-icon-btn:hover { color: var(--ink); background: var(--panel-sunken); border-color: var(--line); }
.version-body { display: grid; grid-template-columns: 248px minmax(0, 1fr); min-height: 0; }
.version-list { min-width: 0; overflow: auto; background: var(--panel); border-right: var(--hair) solid var(--line-strong); }
.version-list-label { position: sticky; top: 0; z-index: 1; display: flex; justify-content: space-between; height: 30px; padding: 0 var(--u3); align-items: center; font-size: var(--fs-xs); font-weight: 700; color: var(--ink-3); background: var(--panel-sunken); border-bottom: var(--hair) solid var(--line); }
.version-row { display: block; width: 100%; min-height: 104px; padding: 11px var(--u3); text-align: left; color: var(--ink-2); background: transparent; border: 0; border-bottom: var(--hair) solid var(--line); cursor: pointer; }
.version-row:hover { background: var(--panel-sunken); }
.version-row[aria-selected='true'] { color: var(--ink); background: var(--paper); box-shadow: inset 3px 0 0 var(--primary); }
.version-row-top { display: flex; align-items: center; gap: var(--u2); }
.version-current { padding: 1px 4px; font-size: 10px; color: var(--primary); border: var(--hair) solid var(--primary); }
.version-row-time, .version-row-trigger, .version-row-excerpt { display: block; margin-top: 4px; }
.version-row-time { font-family: var(--font-mono); font-size: 10px; color: var(--ink-4); }
.version-row-trigger { font-size: var(--fs-xs); color: var(--ink-3); }
.version-row-excerpt { overflow: hidden; display: -webkit-box; color: var(--ink-3); font-size: var(--fs-xs); line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.version-inspector { display: grid; grid-template-rows: 58px minmax(0, 1fr) auto; min-width: 0; min-height: 0; }
.version-inspector-head { display: flex; align-items: center; justify-content: space-between; gap: var(--u3); padding: 0 var(--u4); background: var(--paper); border-bottom: var(--hair) solid var(--line); }
.version-inspector-head strong { display: block; margin-top: 3px; font-size: var(--fs-sm); }
.version-legend { display: flex; align-items: center; gap: 5px; flex: none; font-size: var(--fs-xs); color: var(--ink-3); }
.version-legend i { width: 9px; height: 9px; background: var(--panel-sunken); }
.version-legend i[data-kind='removed'] { background: var(--alert-soft); border-left: 2px solid var(--alert); }
.version-legend i[data-kind='added'] { background: var(--success-soft); border-left: 2px solid var(--success); }
.version-scroll { min-height: 0; overflow: auto; padding: var(--u4); }
.version-diff { border-top: var(--hair) solid var(--line); }
.version-diff-row { display: grid; grid-template-columns: 22px minmax(0, 1fr); min-height: 34px; border-bottom: var(--hair) solid var(--line); }
.version-diff-row > span { padding-top: 8px; text-align: center; font-family: var(--font-mono); font-weight: 700; color: var(--ink-4); }
.version-diff-row p { min-width: 0; margin: 0; padding: 7px var(--u2); font-family: var(--font-serif); font-size: var(--fs); line-height: 1.7; overflow-wrap: anywhere; }
.version-diff-row[data-kind='removed'] { background: var(--alert-soft); box-shadow: inset 3px 0 0 var(--alert); }
.version-diff-row[data-kind='removed'] > span { color: var(--alert-ink); }
.version-diff-row[data-kind='added'] { background: var(--success-soft); box-shadow: inset 3px 0 0 var(--success); }
.version-diff-row[data-kind='added'] > span { color: var(--success); }
.version-diff-row[data-kind='omitted'] { color: var(--ink-4); background: var(--panel-sunken); }
.version-diff-row[data-kind='omitted'] p { font-family: var(--font-ui); font-size: var(--fs-xs); text-align: center; }
.version-preview { margin-top: var(--u4); border-top: var(--hair) solid var(--line-strong); }
.version-preview summary { padding: var(--u3) 0; font-size: var(--fs-sm); font-weight: 700; cursor: pointer; }
.version-preview pre { margin: 0; padding: var(--u4); white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--font-serif); font-size: var(--fs); line-height: 1.9; background: var(--panel-sunken); border: var(--hair) solid var(--line); }
.version-actions { display: flex; min-height: 58px; align-items: center; justify-content: flex-end; gap: var(--u2); padding: var(--u3) var(--u4); background: var(--panel); border-top: var(--hair) solid var(--line-strong); }
.version-actions p { flex: 1; margin: 0; color: var(--ink-2); font-size: var(--fs-sm); line-height: 1.45; }
.version-action-note { color: var(--ink-3) !important; }
.version-action-error { color: var(--alert-ink) !important; font-weight: 700; }
.version-state { display: flex; align-items: center; justify-content: center; gap: var(--u3); min-height: 120px; padding: var(--u4); color: var(--ink-3); font-size: var(--fs-sm); text-align: center; }
.version-state-error { flex-direction: column; color: var(--alert-ink); }
.version-inspector > .version-state { grid-row: 1 / -1; }
.version-icon-btn:focus-visible, .version-row:focus-visible, .version-preview summary:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }

@media (max-width: 700px) {
  .version-drawer { width: 100%; border-left: 0; }
  .version-body { grid-template-columns: 112px minmax(0, 1fr); }
  .version-row { min-height: 82px; padding: 9px 8px; }
  .version-row-excerpt { display: none; }
  .version-inspector-head { padding: 0 var(--u3); }
  .version-legend { display: none; }
  .version-scroll { padding: var(--u3); }
  .version-actions { align-items: stretch; flex-wrap: wrap; padding: var(--u3); }
  .version-actions p { flex-basis: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .version-drawer { scroll-behavior: auto; }
}
</style>
