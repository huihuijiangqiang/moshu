<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { deconstructionApi, type DeconstructionResult } from '@/api/deconstruct'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useShellStore } from '@/stores/shell'

const shell = useShellStore()
const fileInput = ref<HTMLInputElement | null>(null)
const file = ref<File | null>(null)
const result = ref<DeconstructionResult | null>(null)
const analyzing = ref(false)
const error = ref('')

onMounted(() => shell.setCrumb('拆书分析'))

const peakIntensity = computed(() => Math.max(1, ...(result.value?.rhythm_nodes.map((node) => node.intensity) ?? [1])))
const visibleNodes = computed(() => (result.value?.rhythm_nodes ?? []).slice(0, 40))

function chooseFile() {
  fileInput.value?.click()
}

function onFileChange(event: Event) {
  const selected = (event.target as HTMLInputElement).files?.[0] ?? null
  result.value = null
  error.value = ''
  if (!selected) {
    file.value = null
    return
  }
  if (selected.size > 10 * 1024 * 1024) {
    file.value = null
    error.value = '文件不能超过 10 MB。'
    return
  }
  file.value = selected
}

async function analyze() {
  if (!file.value || analyzing.value) return
  analyzing.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await deconstructionApi.analyze(file.value)
  } catch {
    error.value = '文件没有分析成功，请检查格式后重试。'
  } finally {
    analyzing.value = false
  }
}

function clear() {
  file.value = null
  result.value = null
  error.value = ''
  if (fileInput.value) fileInput.value.value = ''
}

function formatRatio(value: number) {
  return `${Math.round(value * 100)}%`
}
</script>

<template>
  <div class="deconstruct-page">
    <header class="deconstruct-head">
      <div>
        <div class="kicker">REFERENCE STUDY</div>
        <h1>拆书分析</h1>
        <p>上传一份参考稿，快速看清章节结构、节奏节点与爽点分布。</p>
      </div>
      <button v-if="result" class="wk-btn" type="button" @click="clear"><AppIcon name="close" :size="14" />清空结果</button>
    </header>

    <section class="deconstruct-notice" aria-label="版权与隐私提示">
      <AppIcon name="guard" :size="17" />
      <div>
        <strong>仅供结构学习参考</strong>
        <p>请确认你拥有上传内容的合法使用权。原文只在本次请求内处理，不保存、不进入当前作品设定库，也不会用于生成正文。</p>
      </div>
    </section>

    <main class="deconstruct-body">
      <section v-if="!result" class="deconstruct-upload" aria-labelledby="deconstruct-upload-title">
        <div class="section-heading"><span>UPLOAD A REFERENCE</span><h2 id="deconstruct-upload-title">选择参考稿</h2></div>
        <p class="deconstruct-help">支持 TXT、Markdown、DOCX、EPUB，单文件不超过 10 MB。分析结果不会写入作品数据。</p>
        <input ref="fileInput" class="sr-only" type="file" accept=".txt,.md,.markdown,.docx,.epub" @change="onFileChange">
        <button class="deconstruct-file-button" type="button" @click="chooseFile">
          <AppIcon name="export" :size="19" />
          <span>{{ file ? file.name : '选择文件' }}</span>
          <small>{{ file ? `${(file.size / 1024).toFixed(0)} KB · 已就绪` : 'TXT / MD / DOCX / EPUB' }}</small>
        </button>
        <p v-if="error" class="deconstruct-error" role="alert">{{ error }}</p>
        <button class="wk-btn" data-primary="true" type="button" :disabled="!file || analyzing" @click="analyze">
          {{ analyzing ? '分析中…' : '开始拆书分析' }}
        </button>
      </section>

      <template v-else>
        <section class="deconstruct-summary" aria-label="分析概览">
          <div><span>总字数</span><strong>{{ result.stats.total_words.toLocaleString() }}</strong></div>
          <div><span>识别章节</span><strong>{{ result.stats.chapter_count }}</strong></div>
          <div><span>平均章长</span><strong>{{ result.stats.average_chapter_words.toLocaleString() }}</strong></div>
          <div><span>解析方式</span><strong>{{ result.parser }}</strong></div>
        </section>

        <section class="deconstruct-section" aria-labelledby="rhythm-title">
          <div class="section-heading"><span>RHYTHM MAP</span><h2 id="rhythm-title">节奏节点</h2></div>
          <div v-if="visibleNodes.length" class="rhythm-track" role="list" aria-label="节奏节点列表">
            <div v-for="(node, index) in visibleNodes" :key="`${node.chapter_index}-${node.position}-${index}`" class="rhythm-node" role="listitem" :style="{ '--node-height': `${Math.max(18, (node.intensity / peakIntensity) * 100)}%` }">
              <span class="rhythm-bar" :title="`第 ${node.chapter_index} 章 · ${node.label}`" />
              <small>第{{ node.chapter_index }}章</small>
              <em>{{ node.label }}</em>
            </div>
          </div>
          <p v-else class="deconstruct-empty">没有检测到明显的节奏信号。</p>
        </section>

        <section class="deconstruct-section" aria-labelledby="payoff-title">
          <div class="section-heading"><span>PAYOFF DISTRIBUTION</span><h2 id="payoff-title">爽点分布</h2></div>
          <div class="payoff-list">
            <div v-for="bucket in result.payoff_distribution" :key="bucket.range" class="payoff-row">
              <span>{{ bucket.range }}</span>
              <div class="payoff-bar"><i :style="{ width: `${Math.min(100, bucket.score / Math.max(1, ...result.payoff_distribution.map((item) => item.score)) * 100)}%` }" /></div>
              <small>{{ bucket.peak_chapters.length ? `第 ${bucket.peak_chapters.join('、')} 章` : '暂无峰值' }}</small>
            </div>
          </div>
        </section>

        <section class="deconstruct-section" aria-labelledby="outline-title">
          <div class="section-heading"><span>CHAPTER STRUCTURE</span><h2 id="outline-title">章纲结构</h2></div>
          <div class="chapter-table" role="table" aria-label="章节结构">
            <div class="chapter-table-head" role="row"><span>章节</span><span>字数</span><span>对白</span><span>摘要与节点</span></div>
            <article v-for="chapter in result.chapters" :key="chapter.index" class="chapter-row" role="row">
              <div><strong>{{ String(chapter.index).padStart(3, '0') }}</strong><span>{{ chapter.title }}</span><small v-if="chapter.confidence === 'low'">低置信度</small></div>
              <span>{{ chapter.word_count.toLocaleString() }}</span>
              <span>{{ formatRatio(chapter.dialogue_ratio) }}</span>
              <div><p>{{ chapter.summary || '暂无摘要' }}</p><small>{{ chapter.beats.map((beat) => beat.label).join(' · ') || '未检测到明显节点' }}</small></div>
            </article>
          </div>
        </section>

        <section class="deconstruct-warnings" aria-label="分析说明">
          <strong>分析说明</strong>
          <p v-for="warning in result.warnings" :key="warning">{{ warning }}</p>
          <p>{{ result.disclaimer }}</p>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.deconstruct-page { height: 100%; overflow: auto; background: var(--panel); color: var(--ink); }
.deconstruct-head { display: flex; align-items: end; justify-content: space-between; gap: var(--u6); padding: 34px 40px 26px; border-bottom: var(--hair) solid var(--line-strong); }
.deconstruct-head h1 { margin: 5px 0 8px; font: 700 30px/1.1 var(--font-heading); letter-spacing: 0; }
.deconstruct-head p, .deconstruct-help { margin: 0; color: var(--ink-3); line-height: 1.7; }
.deconstruct-notice { display: flex; gap: var(--u3); margin: 24px 40px 0; padding: 15px 17px; border-left: 3px solid var(--primary); background: var(--primary-soft); }
.deconstruct-notice strong { display: block; margin-bottom: 4px; }.deconstruct-notice p { margin: 0; color: var(--ink-2); font-size: var(--fs-sm); line-height: 1.65; }
.deconstruct-body { padding: 34px 40px 60px; }.deconstruct-upload { max-width: 680px; padding: 22px 0 30px; border-bottom: var(--hair) solid var(--line-strong); }
.section-heading span { color: var(--ink-4); font: 700 10px/1 var(--font-mono); letter-spacing: .12em; }.section-heading h2 { margin: 7px 0 16px; font: 700 19px/1.25 var(--font-heading); }
.deconstruct-file-button { width: 100%; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: var(--u3); margin: 22px 0 16px; padding: 18px 20px; border: 1px dashed var(--line-strong); background: var(--paper); color: var(--ink); text-align: left; font: inherit; cursor: pointer; }
.deconstruct-file-button:hover { border-color: var(--primary); }.deconstruct-file-button span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; }.deconstruct-file-button small { color: var(--ink-3); }
.deconstruct-error { margin: 0 0 15px; color: var(--alert-ink); font-size: var(--fs-sm); }.deconstruct-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: var(--hair) solid var(--line-strong); border-bottom: var(--hair) solid var(--line-strong); }
.deconstruct-summary > div { min-height: 100px; display: grid; align-content: center; gap: 8px; padding: 14px 18px; border-right: var(--hair) solid var(--line); }.deconstruct-summary > div:last-child { border-right: 0; }.deconstruct-summary span { color: var(--ink-3); font-size: var(--fs-sm); }.deconstruct-summary strong { font: 700 23px/1 var(--font-mono); }
.deconstruct-section { margin-top: 42px; }.rhythm-track { min-height: 170px; display: flex; align-items: end; gap: 6px; overflow-x: auto; padding: 20px 6px 0; border-bottom: var(--hair) solid var(--line-strong); }.rhythm-node { flex: 0 0 42px; height: 150px; display: flex; flex-direction: column; justify-content: end; align-items: center; gap: 5px; }.rhythm-bar { display: block; width: 18px; height: var(--node-height); min-height: 18px; background: var(--primary); }.rhythm-node small { color: var(--ink-3); font-size: 10px; white-space: nowrap; }.rhythm-node em { max-width: 42px; overflow: hidden; color: var(--ink-2); font-size: 10px; font-style: normal; text-align: center; text-overflow: ellipsis; white-space: nowrap; }.deconstruct-empty { color: var(--ink-3); }
.payoff-list { display: grid; gap: 9px; }.payoff-row { display: grid; grid-template-columns: 60px minmax(100px, 1fr) minmax(120px, 240px); align-items: center; gap: 12px; font-size: var(--fs-sm); }.payoff-row > span { color: var(--ink-3); font-family: var(--font-mono); font-size: 11px; }.payoff-bar { height: 10px; background: var(--panel-sunken); }.payoff-bar i { display: block; height: 100%; background: var(--primary); }.payoff-row small { overflow: hidden; color: var(--ink-3); text-overflow: ellipsis; white-space: nowrap; }
.chapter-table { border-top: var(--hair) solid var(--line-strong); }.chapter-table-head, .chapter-row { display: grid; grid-template-columns: minmax(180px, 1.1fr) 90px 70px minmax(260px, 2fr); gap: 16px; align-items: start; padding: 13px 10px; border-bottom: var(--hair) solid var(--line); }.chapter-table-head { color: var(--ink-3); font-size: var(--fs-xs); }.chapter-row { font-size: var(--fs-sm); }.chapter-row > div:first-child { display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 7px; }.chapter-row > div:first-child strong { font-family: var(--font-mono); }.chapter-row > div:first-child span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.chapter-row > div:first-child small { grid-column: 2; color: var(--alert-ink); font-size: var(--fs-xs); }.chapter-row > span { color: var(--ink-2); font-family: var(--font-mono); }.chapter-row p { margin: 0 0 6px; overflow: hidden; color: var(--ink-2); line-height: 1.55; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }.chapter-row small { color: var(--ink-3); }.deconstruct-warnings { margin-top: 38px; padding: 17px 20px; border-top: 2px solid var(--ink); background: var(--panel-sunken); }.deconstruct-warnings p { margin: 7px 0 0; color: var(--ink-2); font-size: var(--fs-sm); line-height: 1.65; }.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
@media (max-width: 760px) { .deconstruct-head, .deconstruct-body { padding-inline: 20px; }.deconstruct-notice { margin-inline: 20px; }.deconstruct-summary { grid-template-columns: repeat(2, 1fr); }.deconstruct-summary > div:nth-child(2) { border-right: 0; }.deconstruct-summary > div:nth-child(-n + 2) { border-bottom: var(--hair) solid var(--line); }.payoff-row { grid-template-columns: 55px minmax(90px, 1fr); }.payoff-row small { grid-column: 2; }.chapter-table { overflow-x: auto; }.chapter-table-head, .chapter-row { min-width: 720px; } }
</style>
