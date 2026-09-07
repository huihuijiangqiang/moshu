<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { textReplacementApi, textReplacementErrorCode } from '@/api/text-replacement'
import type { TextReplacementPreview, TextReplacementRun, TextReplacementScope, TextReplacementSpec } from '@/types'

const props = defineProps<{
  projectId: string
  activeChapterId?: string
  activeVolumeId?: string
  disabledReason?: string
}>()
const emit = defineEmits<{
  close: []
  changed: [run: TextReplacementRun]
  locate: [chapterId: string, paragraphId?: string]
}>()

const query = ref('')
const replacement = ref('')
const scope = ref<TextReplacementScope>('project')
const caseSensitive = ref(true)
const preview = ref<TextReplacementPreview | null>(null)
const selected = ref(new Set<string>())
const loading = ref(false)
const executing = ref(false)
const undoing = ref(false)
const error = ref('')
const acknowledged = ref(false)
const completedRun = ref<TextReplacementRun | null>(null)

const spec = computed<TextReplacementSpec>(() => ({
  query: query.value,
  replacement: replacement.value,
  scope: scope.value,
  chapterId: scope.value === 'chapter' ? props.activeChapterId : undefined,
  volumeId: scope.value === 'volume' ? props.activeVolumeId : undefined,
  caseSensitive: caseSensitive.value
}))
const selectedCount = computed(() => selected.value.size)
const selectedChapterCount = computed(() => new Set(
  preview.value?.matches.filter((item) => selected.value.has(item.id)).map((item) => item.chapterId) ?? []
).size)
const canPreview = computed(() => {
  const left = caseSensitive.value ? query.value : query.value.toLocaleLowerCase()
  const right = caseSensitive.value ? replacement.value : replacement.value.toLocaleLowerCase()
  return query.value.length > 0 && left !== right && !loading.value && !props.disabledReason
})
const canExecute = computed(() => Boolean(
  preview.value && selectedCount.value > 0 && !executing.value && !props.disabledReason && (!preview.value.warnings.length || acknowledged.value)
))

watch([query, replacement, scope, caseSensitive], () => {
  preview.value = null
  selected.value = new Set()
  acknowledged.value = false
  completedRun.value = null
  error.value = ''
})

function messageFor(errorValue: unknown) {
  const code = textReplacementErrorCode(errorValue)
  if (code === 'PREVIEW_STALE' || code === 'MATCH_SET_STALE') return '正文已在别处更新，请重新预览后再替换。'
  if (code === 'UNDO_CONFLICT') return '替换后的章节已有新修改，不能整批撤销。可从章节版本历史逐章恢复。'
  if (code === 'BODY_REPRESENTATION_MISMATCH') return '正文结构需要先重新保存，当前没有执行任何替换。'
  if (code === 'TOO_MANY_MATCHES') return '命中超过 5000 处，请缩小范围或输入更长的查找内容。'
  return errorValue instanceof Error ? errorValue.message : '操作失败，请重试。'
}

async function loadPreview() {
  if (!canPreview.value) return
  loading.value = true
  error.value = ''
  completedRun.value = null
  try {
    const result = await textReplacementApi.preview(props.projectId, spec.value)
    preview.value = result
    selected.value = new Set(result.matches.map((item) => item.id))
  } catch (errorValue) {
    error.value = messageFor(errorValue)
  } finally {
    loading.value = false
  }
}

function toggleMatch(id: string) {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}

function toggleChapter(chapterId: string) {
  const next = new Set(selected.value)
  const ids = preview.value?.matches.filter((item) => item.chapterId === chapterId).map((item) => item.id) ?? []
  const shouldSelect = ids.some((id) => !next.has(id))
  ids.forEach((id) => shouldSelect ? next.add(id) : next.delete(id))
  selected.value = next
}

function chapterSelected(chapterId: string) {
  const ids = preview.value?.matches.filter((item) => item.chapterId === chapterId).map((item) => item.id) ?? []
  return ids.length > 0 && ids.every((id) => selected.value.has(id))
}

async function execute() {
  if (!preview.value || !canExecute.value) return
  executing.value = true
  error.value = ''
  try {
    const run = await textReplacementApi.execute(
      props.projectId,
      spec.value,
      preview.value.previewToken,
      [...selected.value],
      acknowledged.value
    )
    completedRun.value = run
    emit('changed', run)
  } catch (errorValue) {
    error.value = messageFor(errorValue)
  } finally {
    executing.value = false
  }
}

async function undo() {
  if (!completedRun.value || undoing.value) return
  undoing.value = true
  error.value = ''
  try {
    const run = await textReplacementApi.undo(props.projectId, completedRun.value.id)
    completedRun.value = run
    preview.value = null
    selected.value = new Set()
    emit('changed', run)
  } catch (errorValue) {
    error.value = messageFor(errorValue)
  } finally {
    undoing.value = false
  }
}
</script>

<template>
  <div class="replace-backdrop" @click.self="emit('close')">
    <section class="replace-drawer" role="dialog" aria-modal="true" aria-labelledby="replace-title">
      <header class="replace-head">
        <div><span>全书校订</span><h2 id="replace-title">查找与替换</h2></div>
        <button type="button" title="关闭全书校订" aria-label="关闭全书校订" @click="emit('close')"><AppIcon name="close" /></button>
      </header>

      <div class="replace-controls">
        <label><span>查找</span><input v-model="query" autofocus type="search" placeholder="输入正文中的文字"></label>
        <label><span>替换为</span><input v-model="replacement" placeholder="留空表示删除"></label>
        <div class="replace-control-row">
          <div class="replace-segments" aria-label="查找范围">
            <button type="button" :aria-pressed="scope === 'chapter'" :disabled="!activeChapterId" @click="scope = 'chapter'">本章</button>
            <button type="button" :aria-pressed="scope === 'volume'" :disabled="!activeVolumeId" @click="scope = 'volume'">本卷</button>
            <button type="button" :aria-pressed="scope === 'project'" @click="scope = 'project'">全书</button>
          </div>
          <label class="replace-check"><input v-model="caseSensitive" type="checkbox">区分大小写</label>
          <button class="wk-btn" data-primary="true" type="button" :disabled="!canPreview" @click="loadPreview">
            {{ loading ? '正在查找…' : '预览' }}
          </button>
        </div>
      </div>

      <div class="replace-body">
        <nav class="replace-chapters" aria-label="命中章节">
          <div class="replace-list-label"><span>命中章节</span><strong>{{ preview?.chapters.length ?? 0 }}</strong></div>
          <button
            v-for="chapter in preview?.chapters ?? []"
            :key="chapter.id"
            type="button"
            :data-selected="chapterSelected(chapter.id)"
            @click="toggleChapter(chapter.id)"
          >
            <span>{{ String(chapter.index).padStart(3, '0') }}</span>
            <strong>{{ chapter.title || '未命名' }}</strong>
            <small>{{ chapter.matchCount }} 处</small>
          </button>
          <p v-if="preview && !preview.totalMatches">没有找到匹配的正文。</p>
          <p v-else-if="!preview">输入文字并预览，结果不会直接改动正文。</p>
        </nav>

        <main class="replace-results">
          <div v-if="disabledReason" class="replace-error">{{ disabledReason }}</div>
          <div v-if="error" class="replace-error">{{ error }}</div>
          <div v-if="completedRun" class="replace-complete">
            <strong>{{ completedRun.status === 'undone' ? '整批替换已撤销' : `已替换 ${completedRun.totalMatches} 处` }}</strong>
            <span>{{ completedRun.affectedChapters.length }} 个章节已写入版本历史</span>
          </div>
          <div v-if="preview?.warnings.length" class="replace-warning">
            <strong>查找内容涉及设定名</strong>
            <p>{{ preview.warnings.map((item) => `「${item.name}」`).join('、') }}。替换正文不会同步修改设定库。</p>
            <label><input v-model="acknowledged" type="checkbox">我已核对设定库，仍要替换正文</label>
          </div>

          <div v-if="preview?.matches.length" class="replace-proof-list">
            <article v-for="match in preview.matches" :key="match.id" class="replace-proof" :data-selected="selected.has(match.id)">
              <input type="checkbox" :checked="selected.has(match.id)" @change="toggleMatch(match.id)">
              <span class="replace-proof-copy">
                <small>第 {{ match.chapterIndex }} 章 · {{ match.chapterTitle || '未命名' }}</small>
                <span class="replace-before">{{ match.before }}<del>{{ match.matched }}</del>{{ match.after }}</span>
                <span class="replace-after">{{ match.before }}<ins>{{ match.replacement || '删除' }}</ins>{{ match.after }}</span>
              </span>
              <button class="replace-open" type="button" @click="emit('locate', match.chapterId, match.paragraphId)">打开原文</button>
            </article>
          </div>
          <div v-else-if="!preview && !error" class="replace-empty">
            <AppIcon name="search" :size="24" />
            <strong>先查看全部命中，再决定改哪些</strong>
          </div>
        </main>
      </div>

      <footer class="replace-actions">
        <span v-if="preview">已选 {{ selectedCount }} 处 · {{ selectedChapterCount }} 章</span>
        <span v-else>正文按章节版本原子写入</span>
        <button v-if="completedRun?.status === 'applied'" class="wk-btn" type="button" :disabled="undoing" @click="undo">
          <AppIcon name="restore" :size="14" />{{ undoing ? '撤销中…' : '撤销整批替换' }}
        </button>
        <button class="wk-btn" type="button" @click="emit('close')">关闭</button>
        <button class="wk-btn" data-primary="true" type="button" :disabled="!canExecute || completedRun?.status === 'applied'" @click="execute">
          {{ executing ? '替换中…' : `替换所选 ${selectedCount || ''}` }}
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.replace-backdrop { position: fixed; inset: 0; z-index: 86; background: rgb(20 24 25 / 38%); }
.replace-drawer { position: absolute; inset: 0 0 0 auto; display: grid; grid-template-rows: 58px auto minmax(0, 1fr) 58px; width: min(960px, 94vw); color: var(--ink); background: var(--paper); border-left: var(--rule) solid var(--line-strong); box-shadow: -18px 0 42px rgb(20 24 25 / 18%); }
.replace-head { display: flex; align-items: center; justify-content: space-between; padding: 0 var(--u4); background: var(--panel); border-bottom: var(--hair) solid var(--line-strong); }
.replace-head span { display: block; color: var(--ink-4); font-size: 10px; font-weight: 700; text-transform: uppercase; }
.replace-head h2 { margin: 2px 0 0; font-family: var(--font-serif); font-size: 17px; line-height: 1.2; }
.replace-head > button { display: grid; place-items: center; width: 32px; height: 32px; color: var(--ink-2); background: transparent; border: var(--hair) solid transparent; cursor: pointer; }
.replace-controls { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(180px, 1fr); gap: var(--u3); padding: var(--u3) var(--u4); background: var(--panel); border-bottom: var(--hair) solid var(--line-strong); }
.replace-controls > label > span { display: block; margin-bottom: 4px; color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.replace-controls input[type='search'], .replace-controls input:not([type]) { width: 100%; height: 34px; padding: 0 var(--u2); color: var(--ink); background: var(--paper); border: var(--hair) solid var(--line-strong); border-radius: 3px; }
.replace-control-row { grid-column: 1 / -1; display: flex; align-items: center; gap: var(--u3); }
.replace-segments { display: flex; height: 30px; border: var(--hair) solid var(--line-strong); }
.replace-segments button { min-width: 54px; padding: 0 var(--u2); color: var(--ink-3); background: transparent; border: 0; border-right: var(--hair) solid var(--line); cursor: pointer; }
.replace-segments button:last-child { border-right: 0; }
.replace-segments button[aria-pressed='true'] { color: var(--ink); font-weight: 700; background: var(--paper); box-shadow: inset 0 -2px 0 var(--primary); }
.replace-check { display: flex; align-items: center; gap: 6px; color: var(--ink-3); font-size: var(--fs-xs); }
.replace-control-row > .wk-btn { margin-left: auto; }
.replace-body { display: grid; grid-template-columns: 226px minmax(0, 1fr); min-height: 0; }
.replace-chapters { min-width: 0; overflow: auto; background: var(--panel); border-right: var(--hair) solid var(--line-strong); }
.replace-list-label { position: sticky; top: 0; z-index: 1; display: flex; align-items: center; justify-content: space-between; height: 30px; padding: 0 var(--u3); color: var(--ink-3); font-size: var(--fs-xs); background: var(--panel-sunken); border-bottom: var(--hair) solid var(--line); }
.replace-chapters > button { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; align-items: center; width: 100%; min-height: 42px; padding: 5px var(--u2); color: var(--ink-3); text-align: left; background: transparent; border: 0; border-bottom: var(--hair) solid var(--line); cursor: pointer; }
.replace-chapters > button[data-selected='true'] { color: var(--ink); background: var(--paper); box-shadow: inset 3px 0 0 var(--primary); }
.replace-chapters > button span, .replace-chapters > button small { font-family: var(--font-mono); font-size: 10px; }
.replace-chapters > button strong { overflow: hidden; font-size: var(--fs-xs); text-overflow: ellipsis; white-space: nowrap; }
.replace-chapters > p { padding: var(--u4) var(--u3); color: var(--ink-4); font-size: var(--fs-xs); line-height: 1.6; }
.replace-results { min-width: 0; overflow: auto; padding: var(--u4); }
.replace-error, .replace-warning, .replace-complete { margin-bottom: var(--u3); padding: var(--u3); border-left: 3px solid var(--alert); background: var(--alert-soft); color: var(--alert-ink); }
.replace-complete { border-left-color: var(--success); background: var(--success-soft); color: var(--ink); }
.replace-complete span { display: block; margin-top: 3px; color: var(--ink-3); font-size: var(--fs-xs); }
.replace-warning p { margin: 4px 0 8px; color: var(--ink-2); font-size: var(--fs-sm); }
.replace-warning label { display: flex; gap: 6px; align-items: center; font-size: var(--fs-xs); font-weight: 700; }
.replace-proof-list { border-top: var(--hair) solid var(--line); }
.replace-proof { position: relative; display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: var(--u2); padding: 11px 74px 11px var(--u2); opacity: .58; border-bottom: var(--hair) solid var(--line); }
.replace-proof[data-selected='true'] { opacity: 1; background: var(--panel); }
.replace-proof > input { margin-top: 19px; }
.replace-proof-copy, .replace-before, .replace-after { display: block; min-width: 0; }
.replace-proof-copy small { display: block; margin-bottom: 5px; color: var(--ink-4); font-family: var(--font-mono); font-size: 10px; }
.replace-before, .replace-after { overflow-wrap: anywhere; font-family: var(--font-serif); font-size: var(--fs-sm); line-height: 1.75; }
.replace-before { color: var(--ink-3); }
.replace-after { color: var(--ink); }
.replace-before del { padding: 1px 2px; color: var(--alert-ink); background: var(--alert-soft); text-decoration-thickness: 1px; }
.replace-after ins { padding: 1px 2px; color: var(--success); background: var(--success-soft); font-weight: 700; text-decoration: none; }
.replace-open { position: absolute; top: 8px; right: var(--u2); padding: 3px 5px; color: var(--primary); font-size: 10px; background: transparent; border: var(--hair) solid transparent; cursor: pointer; }
.replace-open:hover { background: var(--panel-sunken); border-color: var(--line); }
.replace-empty { display: grid; place-items: center; align-content: center; min-height: 240px; gap: var(--u2); color: var(--ink-4); }
.replace-empty strong { color: var(--ink-3); font-family: var(--font-serif); font-size: var(--fs-md); }
.replace-actions { display: flex; align-items: center; justify-content: flex-end; gap: var(--u2); padding: var(--u3) var(--u4); background: var(--panel); border-top: var(--hair) solid var(--line-strong); }
.replace-actions > span { margin-right: auto; color: var(--ink-3); font-size: var(--fs-xs); }
button:focus-visible, input:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
@media (max-width: 680px) {
  .replace-drawer { width: 100%; grid-template-rows: 58px auto minmax(0, 1fr) auto; border-left: 0; }
  .replace-controls { grid-template-columns: 1fr; }
  .replace-control-row { grid-column: 1; flex-wrap: wrap; }
  .replace-body { grid-template-columns: 102px minmax(0, 1fr); }
  .replace-chapters > button { grid-template-columns: 28px minmax(0, 1fr); }
  .replace-chapters > button small { grid-column: 2; }
  .replace-results { padding: var(--u3); }
  .replace-actions { flex-wrap: wrap; padding: var(--u3); }
  .replace-actions > span { flex-basis: 100%; }
}
</style>
