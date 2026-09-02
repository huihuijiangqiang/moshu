<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { provenanceApi, type ProvenanceReport, type ProvenanceSource } from '@/api/provenance'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const store = useProjectStore()
const shell = useShellStore()
const report = ref<ProvenanceReport | null>(null)
const scope = ref<'chapter' | 'book'>('chapter')
const loading = ref(false)
const error = ref('')
let requestSerial = 0

const projectId = computed(() => String(route.params.projectId ?? store.loadedProjectId ?? ''))
const totalAiWords = computed(() => (report.value?.aiRawWords ?? 0) + (report.value?.aiEditedWords ?? 0))
const percent = (words: number) => report.value?.totalWords ? Math.round(words / report.value.totalWords * 100) : 0
const sourceLabel: Record<ProvenanceSource, string> = {
  'ai-raw': 'AI 原文',
  'ai-edited': 'AI 起草已改',
  human: '手写'
}

watch(
  [projectId, () => store.activeId, scope],
  async ([nextProjectId, chapterId, nextScope]) => {
    shell.setCrumb('AI 来源')
    if (!nextProjectId || (nextScope === 'chapter' && !chapterId)) {
      report.value = null
      return
    }
    const serial = ++requestSerial
    loading.value = true
    error.value = ''
    try {
      const next = await provenanceApi.report(nextProjectId, chapterId, nextScope)
      if (serial === requestSerial) report.value = next
    } catch (caught) {
      if (serial === requestSerial) {
        report.value = null
        error.value = caught instanceof Error ? caught.message : '来源记录加载失败'
      }
    } finally {
      if (serial === requestSerial) loading.value = false
    }
  },
  { immediate: true }
)

function exportReport() {
  if (!report.value) return
  const blob = new Blob([JSON.stringify(report.value, null, 2)], { type: 'application/json;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `${store.project?.title ?? '作品'}-AI来源-${scope.value}.json`
  link.click()
  URL.revokeObjectURL(link.href)
}

function selectChapter(event: Event) {
  store.activeId = (event.target as HTMLSelectElement).value
}
</script>

<template>
  <div class="provenance-page">
    <Teleport defer to="#topbar-actions">
      <div class="scope-switch" aria-label="统计范围">
        <button type="button" :aria-pressed="scope === 'chapter'" @click="scope = 'chapter'">本章</button>
        <button type="button" :aria-pressed="scope === 'book'" @click="scope = 'book'">全书</button>
      </div>
      <button class="topbar-btn" type="button" :disabled="!report" @click="exportReport">导出</button>
    </Teleport>

    <header class="report-head">
      <div>
        <div class="kicker">PROVENANCE LEDGER</div>
        <h1>AI 来源账本</h1>
      </div>
      <div class="report-total">
        <strong>{{ report?.totalWords.toLocaleString() ?? '—' }}</strong>
        <span>统计字数</span>
      </div>
    </header>

    <div v-if="loading" class="state-line">正在核对生成记录…</div>
    <div v-else-if="error" class="state-line state-error">{{ error }}</div>
    <template v-else-if="report">
      <section class="source-summary" aria-label="来源统计">
        <article>
          <span class="source-dot source-ai" />
          <strong>{{ percent(report.aiRawWords) }}%</strong>
          <div>AI 原文</div>
          <small>{{ report.aiRawWords.toLocaleString() }} 字</small>
        </article>
        <article>
          <span class="source-dot source-edited" />
          <strong>{{ percent(report.aiEditedWords) }}%</strong>
          <div>AI 起草已改</div>
          <small>{{ report.aiEditedWords.toLocaleString() }} 字</small>
        </article>
        <article>
          <span class="source-dot source-human" />
          <strong>{{ percent(report.humanWords) }}%</strong>
          <div>手写</div>
          <small>{{ report.humanWords.toLocaleString() }} 字</small>
        </article>
      </section>

      <div class="source-bar" aria-label="来源占比">
        <span class="bar-ai" :style="{ width: `${percent(report.aiRawWords)}%` }" />
        <span class="bar-edited" :style="{ width: `${percent(report.aiEditedWords)}%` }" />
        <span class="bar-human" :style="{ width: `${percent(report.humanWords)}%` }" />
      </div>

      <section v-if="scope === 'chapter'" class="paragraph-ledger">
        <div class="ledger-heading">
          <select aria-label="选择章节" :value="store.activeId ?? ''" @change="selectChapter">
            <option v-for="chapter in store.chapters" :key="chapter.id" :value="chapter.id">
              第 {{ chapter.index }} 章 · {{ chapter.title }}
            </option>
          </select>
          <span>{{ report.paragraphs.length }} 段 · {{ totalAiWords.toLocaleString() }} 字来自 AI 草稿</span>
        </div>
        <div v-if="report.paragraphs.length" class="paragraph-list">
          <article v-for="paragraph in report.paragraphs" :key="paragraph.id" :data-source="paragraph.source">
            <div class="paragraph-meta">
              <span>{{ sourceLabel[paragraph.source] }}</span>
              <small>{{ paragraph.words }} 字</small>
            </div>
            <p>{{ paragraph.text || '空段落' }}</p>
          </article>
        </div>
        <div v-else class="empty-state">本章还没有可统计的正文。</div>
      </section>

      <section v-else class="book-summary">
        <strong>{{ totalAiWords.toLocaleString() }}</strong>
        <span>字由墨枢生成并被采纳，其中 {{ report.aiEditedWords.toLocaleString() }} 字后来经过修改。</span>
      </section>
    </template>
  </div>
</template>

<style scoped>
.provenance-page { height: 100%; overflow: auto; background: var(--color-bg); }
.report-head { min-height: 126px; padding: 28px 34px 24px; border-bottom: 1px solid var(--line); display: flex; align-items: end; justify-content: space-between; gap: 24px; }
.report-head h1 { margin: 6px 0 0; font-family: var(--font-prose); font-size: 28px; font-weight: 700; letter-spacing: 0; }
.report-total { text-align: right; display: grid; gap: 2px; }
.report-total strong { font-family: var(--font-mono); font-size: 30px; font-weight: 500; }
.report-total span, .source-summary small, .ledger-heading span { color: var(--ink-3); font-size: 12px; }
.source-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.source-summary article { position: relative; padding: 24px 34px; border-right: 1px solid var(--line); }
.source-summary article:last-child { border-right: 0; }
.source-summary strong { display: block; margin: 8px 0 5px; font-family: var(--font-mono); font-size: 32px; font-weight: 500; }
.source-summary div { font-size: 13px; font-weight: 700; margin-bottom: 4px; }
.source-dot { display: block; width: 18px; height: 3px; }
.source-ai, .bar-ai { background: #b34b34; }
.source-edited, .bar-edited { background: #ae8d4d; }
.source-human, .bar-human { background: #62756a; }
.source-bar { display: flex; height: 5px; background: var(--color-neutral-200); }
.source-bar span { min-width: 0; }
.paragraph-ledger { padding: 30px 34px 56px; max-width: 1040px; }
.ledger-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--line-strong); }
.ledger-heading select { min-width: 260px; max-width: 60%; height: 32px; border: 1px solid var(--line-strong); background: var(--paper); color: var(--ink); padding: 0 30px 0 10px; font-family: var(--font-prose); font-size: 14px; }
.paragraph-list article { display: grid; grid-template-columns: 116px minmax(0, 1fr); gap: 18px; padding: 18px 0; border-bottom: 1px solid var(--line); }
.paragraph-list article[data-source='ai-raw'] { border-left: 3px solid #b34b34; padding-left: 15px; }
.paragraph-list article[data-source='ai-edited'] { border-left: 3px solid #ae8d4d; padding-left: 15px; }
.paragraph-list article[data-source='human'] { border-left: 3px solid #62756a; padding-left: 15px; }
.paragraph-meta { display: flex; flex-direction: column; gap: 5px; font-size: 12px; font-weight: 700; }
.paragraph-meta small { color: var(--ink-3); font-weight: 400; }
.paragraph-list p { margin: 0; font-family: var(--font-prose); font-size: 15px; line-height: 1.9; white-space: pre-wrap; }
.book-summary { padding: 48px 34px; display: flex; align-items: baseline; gap: 14px; }
.book-summary strong { font-family: var(--font-mono); font-size: 42px; font-weight: 500; }
.book-summary span { color: var(--ink-3); line-height: 1.7; }
.state-line, .empty-state { padding: 40px 34px; color: var(--ink-3); }
.state-error { color: var(--color-danger, #9c2f2f); }
.scope-switch { display: flex; border: 1px solid var(--line-strong); }
.scope-switch button { min-width: 52px; height: 30px; border: 0; border-right: 1px solid var(--line-strong); background: var(--paper); color: var(--ink-3); font-size: 12px; cursor: pointer; }
.scope-switch button:last-child { border-right: 0; }
.scope-switch button[aria-pressed='true'] { background: var(--ink); color: var(--paper); }
@media (max-width: 720px) {
  .report-head { min-height: 104px; padding: 20px; }
  .report-head h1 { font-size: 23px; }
  .source-summary { grid-template-columns: 1fr; }
  .source-summary article { padding: 18px 20px; border-right: 0; border-bottom: 1px solid var(--line); }
  .source-summary article:last-child { border-bottom: 0; }
  .source-summary strong { font-size: 27px; }
  .paragraph-ledger { padding: 24px 20px 48px; }
  .ledger-heading { align-items: flex-start; flex-direction: column; gap: 5px; }
  .ledger-heading select { min-width: 0; max-width: none; width: 100%; }
  .paragraph-list article { grid-template-columns: 1fr; gap: 9px; }
  .paragraph-meta { flex-direction: row; justify-content: space-between; }
  .book-summary { padding: 36px 20px; align-items: flex-start; flex-direction: column; }
}
</style>
