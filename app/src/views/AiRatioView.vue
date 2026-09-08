<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  provenanceApi,
  type ProvenanceReport,
  type ProvenanceSource,
  type SuspectedSentence
} from '@/api/provenance'
import {
  naturalizationApi,
  type NaturalizationFinding,
  type NaturalizationRun
} from '@/api/naturalization'
import AppIcon from '@/components/ui/AppIcon.vue'
import { projectPath } from '@/router/project-route'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const router = useRouter()
const store = useProjectStore()
const shell = useShellStore()
const report = ref<ProvenanceReport | null>(null)
const scope = ref<'chapter' | 'book'>('chapter')
const loading = ref(false)
const error = ref('')
const naturalizationRun = ref<NaturalizationRun | null>(null)
const naturalizationBusy = ref(false)
const naturalizationError = ref('')
const naturalizationNotice = ref('')
let requestSerial = 0

const projectId = computed(() => String(route.params.projectId ?? store.loadedProjectId ?? ''))
const totalAiWords = computed(() => (report.value?.aiRawWords ?? 0) + (report.value?.aiEditedWords ?? 0))
const percent = (words: number) => report.value?.totalWords ? Math.round(words / report.value.totalWords * 100) : 0
const sourceLabel: Record<ProvenanceSource, string> = {
  'ai-raw': 'AI 原文',
  'ai-edited': 'AI 起草已改',
  human: '手写'
}

async function loadReport(nextProjectId = projectId.value, chapterId = store.activeId, nextScope = scope.value) {
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
}

watch(
  [projectId, () => store.activeId, scope],
  ([nextProjectId, chapterId, nextScope]) => {
    naturalizationRun.value = null
    naturalizationError.value = ''
    naturalizationNotice.value = ''
    void loadReport(nextProjectId, chapterId, nextScope)
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

function openSentence(item: SuspectedSentence, rewrite = false) {
  if (!projectId.value || !store.activeId) return
  void router.push({
    path: projectPath(projectId.value, 'write'),
    query: {
      chapter: store.activeId,
      paragraph: item.paragraphId,
      start: String(item.start),
      end: String(item.end),
      ...(rewrite ? { rewrite: '1' } : {})
    }
  })
}

async function scanNaturalization() {
  if (!projectId.value || !store.activeId || naturalizationBusy.value) return
  naturalizationBusy.value = true
  naturalizationError.value = ''
  naturalizationNotice.value = ''
  try {
    await store.openChapter(store.activeId)
    naturalizationRun.value = await naturalizationApi.scan(projectId.value, {
      chapterId: store.activeId,
      sourceBodyRev: store.active?.rev ?? 0,
      scope: 'chapter',
      mode: 'rules'
    })
    naturalizationNotice.value = naturalizationRun.value.findingCount
      ? `找到 ${naturalizationRun.value.findingCount} 处可复核表达`
      : '本章没有命中当前自然化规则'
  } catch (caught) {
    naturalizationError.value = caught instanceof Error ? caught.message : '自然化扫描失败'
  } finally {
    naturalizationBusy.value = false
  }
}

async function acceptNaturalization(finding: NaturalizationFinding) {
  if (!naturalizationRun.value || naturalizationBusy.value) return
  naturalizationBusy.value = true
  naturalizationError.value = ''
  try {
    naturalizationRun.value = await naturalizationApi.accept(naturalizationRun.value.id, finding.id)
    if (store.activeId) await store.reloadReplacedChapters([store.activeId])
    await loadReport()
    naturalizationNotice.value = '候选已采纳，正文版本和来源记录已更新'
  } catch (caught) {
    naturalizationError.value = caught instanceof Error ? caught.message : '候选采纳失败，正文可能已变化'
  } finally {
    naturalizationBusy.value = false
  }
}

async function rejectNaturalization(finding: NaturalizationFinding) {
  if (!naturalizationRun.value || naturalizationBusy.value) return
  naturalizationBusy.value = true
  naturalizationError.value = ''
  try {
    const rejected = await naturalizationApi.reject(naturalizationRun.value.id, finding.id)
    const current = naturalizationRun.value.findings.find((item) => item.id === rejected.id)
    if (current) current.status = rejected.status
    naturalizationNotice.value = '候选已拒绝，正文没有变化'
  } catch (caught) {
    naturalizationError.value = caught instanceof Error ? caught.message : '候选拒绝失败'
  } finally {
    naturalizationBusy.value = false
  }
}
</script>

<template>
  <div class="provenance-page">
    <Teleport defer to="#topbar-actions">
      <div class="scope-switch" aria-label="统计范围">
        <button type="button" :aria-pressed="scope === 'chapter'" @click="scope = 'chapter'">本章</button>
        <button type="button" :aria-pressed="scope === 'book'" @click="scope = 'book'">全书</button>
      </div>
      <button class="topbar-btn" type="button" :disabled="!report" @click="exportReport"><AppIcon name="export" :size="14" />导出</button>
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

      <section v-if="scope === 'chapter'" class="risk-proof" aria-labelledby="risk-proof-title">
        <div class="proof-heading">
          <div><span class="wk-label">STYLE PROOF</span><h2 id="risk-proof-title">疑似模板句式</h2></div>
          <strong>{{ report.suspectedSentences.length }}</strong>
        </div>
        <p class="proof-disclaimer">{{ report.sentenceRiskDisclaimer }}</p>
        <div v-if="report.suspectedSentences.length" class="proof-list">
          <article v-for="item in report.suspectedSentences" :key="item.id">
            <div class="proof-index"><strong>{{ item.score }}</strong><span>风险分</span></div>
            <div class="proof-copy">
              <div class="proof-meta"><span>{{ sourceLabel[item.source] }}</span><span>{{ item.reasons.join(' · ') }}</span></div>
              <p>{{ item.text }}</p>
            </div>
            <div class="proof-actions">
              <button class="wk-btn wk-btn-xs" type="button" @click="openSentence(item)"><AppIcon name="write" :size="13" />定位</button>
              <button class="wk-btn wk-btn-xs" data-primary="true" type="button" @click="openSentence(item, true)"><AppIcon name="edit" :size="13" />重写</button>
            </div>
          </article>
        </div>
        <div v-else class="proof-empty">本章没有命中当前规则。仍建议按平台要求和作者判断人工复核。</div>
      </section>

      <section v-if="scope === 'chapter'" class="naturalization-panel" aria-labelledby="naturalization-title">
        <div class="naturalization-heading">
          <div>
            <span class="wk-label">NATURALIZATION REVIEW</span>
            <h2 id="naturalization-title">自然化审查</h2>
            <p>只针对可解释的表达风险生成候选。人物、时间、数字、关系和事件结果必须由作者确认。</p>
          </div>
          <button class="wk-btn" data-primary="true" type="button" :disabled="naturalizationBusy || !store.activeId" @click="scanNaturalization">
            <AppIcon name="search" :size="13" />{{ naturalizationBusy ? '处理中…' : '扫描本章' }}
          </button>
        </div>
        <div v-if="naturalizationError" class="naturalization-message is-error">{{ naturalizationError }}</div>
        <div v-else-if="naturalizationNotice" class="naturalization-message">{{ naturalizationNotice }}</div>
        <div v-if="naturalizationRun?.findings.length" class="naturalization-list">
          <article v-for="finding in naturalizationRun.findings" :key="finding.id" :data-status="finding.status">
            <div class="naturalization-meta">
              <span>{{ finding.status === 'pending' ? '待复核' : finding.status === 'accepted' ? '已采纳' : finding.status === 'rejected' ? '已拒绝' : '已失效' }}</span>
              <small>{{ finding.reasons.join(' · ') }}</small>
            </div>
            <div class="naturalization-diff">
              <div><label>原文</label><p>{{ finding.originalText }}</p></div>
              <div><label>候选</label><p>{{ finding.candidateText }}</p></div>
            </div>
            <div v-if="finding.status === 'pending'" class="naturalization-actions">
              <button class="wk-btn wk-btn-xs" type="button" :disabled="naturalizationBusy" @click="rejectNaturalization(finding)">保留原文</button>
              <button class="wk-btn wk-btn-xs" data-primary="true" type="button" :disabled="naturalizationBusy" @click="acceptNaturalization(finding)">采纳候选</button>
            </div>
          </article>
        </div>
        <div v-else-if="naturalizationRun" class="naturalization-empty">{{ naturalizationRun.status === 'stale' ? '正文已变化，这次扫描已失效，请重新扫描。' : '没有待审候选。' }}</div>
      </section>

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
.provenance-page { height: 100%; overflow: auto; background: var(--color-bg); color: var(--ink); }
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
.source-ai, .bar-ai { background: var(--accent); }
.source-edited, .bar-edited { background: var(--ink-3); }
.source-human, .bar-human { background: var(--line-strong); }
.source-bar { display: flex; height: 5px; background: var(--color-neutral-200); }
.source-bar span { min-width: 0; }
.risk-proof { padding: 32px 34px 8px; max-width: 1120px; }
.proof-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; padding-bottom: 13px; border-bottom: 2px solid var(--ink); }
.proof-heading h2 { margin: 6px 0 0; font: 700 20px/1.2 var(--font-prose); letter-spacing: 0; }
.proof-heading > strong { font: 500 30px/1 var(--font-mono); }
.proof-disclaimer { max-width: 760px; margin: 12px 0 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.65; }
.proof-list { margin-top: 18px; }
.proof-list article { display: grid; grid-template-columns: 62px minmax(0, 1fr) auto; gap: 18px; align-items: start; padding: 17px 0; border-bottom: 1px solid var(--line); }
.proof-index { display: grid; gap: 3px; }
.proof-index strong { color: var(--accent); font: 700 20px/1 var(--font-mono); }
.proof-index span, .proof-meta { color: var(--ink-3); font-size: 11px; }
.proof-meta { display: flex; flex-wrap: wrap; gap: 7px 14px; margin-bottom: 7px; }
.proof-meta span:first-child { color: var(--ink-2); font-weight: 700; }
.proof-copy p { margin: 0; font: 15px/1.8 var(--font-prose); }
.proof-actions { display: flex; gap: 6px; }
.proof-empty { padding: 26px 0; border-bottom: 1px solid var(--line); color: var(--ink-3); }
.naturalization-panel { margin: 24px 34px 0; max-width: 1120px; border-top: 2px solid var(--ink); }
.naturalization-heading { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 24px 0 16px; border-bottom: 1px solid var(--line); }
.naturalization-heading h2 { margin: 6px 0 6px; font: 700 20px/1.2 var(--font-prose); letter-spacing: 0; }
.naturalization-heading p { margin: 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.65; }
.naturalization-message { padding: 12px 0; color: var(--ink-2); font-size: 12px; }
.naturalization-message.is-error { color: var(--alert-ink); }
.naturalization-list article { display: grid; grid-template-columns: 116px minmax(0, 1fr) auto; gap: 18px; align-items: start; padding: 17px 0; border-bottom: 1px solid var(--line); }
.naturalization-meta { display: flex; flex-direction: column; gap: 4px; font-size: 12px; font-weight: 700; }
.naturalization-meta small { color: var(--ink-3); font-size: 11px; line-height: 1.5; font-weight: 400; }
.naturalization-diff { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.naturalization-diff > div { padding: 11px 13px; background: var(--paper); border: 1px solid var(--line); }
.naturalization-diff label { display: block; margin-bottom: 6px; color: var(--ink-3); font-size: 11px; }
.naturalization-diff p { margin: 0; font: 14px/1.75 var(--font-prose); white-space: pre-wrap; }
.naturalization-actions { display: flex; gap: 6px; }
.naturalization-list article[data-status='accepted'], .naturalization-list article[data-status='rejected'], .naturalization-list article[data-status='stale'] { opacity: .62; }
.naturalization-empty { padding: 22px 0; color: var(--ink-3); font-size: 13px; }
.paragraph-ledger { padding: 38px 34px 56px; max-width: 1040px; }
.ledger-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--line-strong); }
.ledger-heading select { min-width: 260px; max-width: 60%; height: 32px; border: 1px solid var(--line-strong); background: var(--paper); color: var(--ink); padding: 0 30px 0 10px; font-family: var(--font-prose); font-size: 14px; }
.paragraph-list article { display: grid; grid-template-columns: 116px minmax(0, 1fr); gap: 18px; padding: 18px 0 18px 15px; border-bottom: 1px solid var(--line); border-left: 3px solid var(--line-strong); }
.paragraph-list article[data-source='ai-raw'] { border-left-color: var(--accent); }
.paragraph-list article[data-source='ai-edited'] { border-left-color: var(--ink-3); }
.paragraph-meta { display: flex; flex-direction: column; gap: 5px; font-size: 12px; font-weight: 700; }
.paragraph-meta small { color: var(--ink-3); font-weight: 400; }
.paragraph-list p { margin: 0; font-family: var(--font-prose); font-size: 15px; line-height: 1.9; white-space: pre-wrap; }
.book-summary { padding: 48px 34px; display: flex; align-items: baseline; gap: 14px; }
.book-summary strong { font-family: var(--font-mono); font-size: 42px; font-weight: 500; }
.book-summary span { color: var(--ink-3); line-height: 1.7; }
.state-line, .empty-state { padding: 40px 34px; color: var(--ink-3); }
.state-error { color: var(--alert-ink); }
.scope-switch { display: flex; border: 1px solid var(--line-strong); }
.scope-switch button { min-width: 52px; height: 30px; border: 0; border-right: 1px solid var(--line-strong); background: var(--paper); color: var(--ink-3); font-size: 12px; cursor: pointer; }
.scope-switch button:last-child { border-right: 0; }
.scope-switch button[aria-pressed='true'] { background: var(--ink); color: var(--paper); }
button:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 720px) {
  .report-head { min-height: 104px; padding: 20px; }
  .report-head h1 { font-size: 23px; }
  .source-summary { grid-template-columns: 1fr; }
  .source-summary article { padding: 18px 20px; border-right: 0; border-bottom: 1px solid var(--line); }
  .source-summary article:last-child { border-bottom: 0; }
  .source-summary strong { font-size: 27px; }
  .risk-proof, .paragraph-ledger { padding-inline: 20px; }
  .naturalization-panel { margin-inline: 20px; }
  .naturalization-heading { align-items: flex-start; flex-direction: column; }
  .naturalization-list article { grid-template-columns: 1fr; gap: 10px; }
  .naturalization-diff { grid-template-columns: 1fr; }
  .proof-list article { grid-template-columns: 48px minmax(0, 1fr); }
  .proof-actions { grid-column: 2; }
  .ledger-heading { align-items: flex-start; flex-direction: column; gap: 5px; }
  .ledger-heading select { min-width: 0; max-width: none; width: 100%; }
  .paragraph-list article { grid-template-columns: 1fr; gap: 9px; }
  .paragraph-meta { flex-direction: row; justify-content: space-between; }
  .book-summary { padding: 36px 20px; align-items: flex-start; flex-direction: column; }
}
</style>
