<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BackupFileError, exportApi, readBackupPayload, type ExportFormat, type ExportSplit } from '@/api/exports'
import { ApiError } from '@/api/http'
import { routeProjectId } from '@/router/project-route'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const router = useRouter()
const store = useProjectStore()
const codex = useCodexStore()
const shell = useShellStore()
const projectId = computed(() => routeProjectId(route))

onMounted(() => shell.setCrumb('导出与备份'))

const includeOutline = ref(true)
const includeCodex = ref(true)
const format = ref<ExportFormat>('txt')
const split = ref<ExportSplit>('single')
const busy = ref<'export' | 'backup' | 'restore' | null>(null)
const message = ref('')
const error = ref('')
const restoreInput = ref<HTMLInputElement | null>(null)

const formatOptions: Array<{ value: ExportFormat; label: string; note: string }> = [
  { value: 'txt', label: 'TXT', note: '投稿与跨平台最稳妥' },
  { value: 'markdown', label: 'Markdown', note: '保留标题层级' },
  { value: 'docx', label: 'DOCX', note: 'Word 与编辑器继续排版' },
  { value: 'epub', label: 'EPUB', note: '电子书阅读器预览' }
]

const selectedFormat = computed(() => formatOptions.find((item) => item.value === format.value)!)
const manifest = computed(() => [
  { label: '正文', value: `${store.totalChapters} 章`, detail: `${store.totalWords.toLocaleString()} 字` },
  { label: '章纲', value: includeOutline.value ? '包含' : '不包含', detail: '按章节顺序' },
  { label: '设定库', value: includeCodex.value ? `${codex.entries.length} 条` : '不包含', detail: '属性与别名' },
  { label: '交付', value: selectedFormat.value.label, detail: split.value === 'zip' ? '一章一文件' : '单文件' }
])

watch(format, (value) => {
  if (!['txt', 'markdown'].includes(value)) split.value = 'single'
})

function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

async function exportNow() {
  if (!projectId.value) return
  busy.value = 'export'
  error.value = ''
  message.value = ''
  try {
    const file = await exportApi.manuscript(projectId.value, {
      format: format.value,
      split: split.value,
      includeOutline: includeOutline.value,
      includeCodex: includeCodex.value
    })
    saveFile(file.blob, file.filename)
    message.value = `已生成 ${file.filename}`
  } catch {
    error.value = '导出失败。稿件没有变化，请稍后重试。'
  } finally {
    busy.value = null
  }
}

async function backupNow() {
  if (!projectId.value) return
  busy.value = 'backup'
  error.value = ''
  message.value = ''
  try {
    const file = await exportApi.backup(projectId.value)
    saveFile(file.blob, file.filename)
    message.value = `完整备份已生成：${file.filename}`
  } catch {
    error.value = '备份生成失败，请稍后重试。'
  } finally {
    busy.value = null
  }
}

async function restoreBackup(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  busy.value = 'restore'
  error.value = ''
  message.value = ''
  try {
    const payload = await readBackupPayload(file)
    const restored = await exportApi.restore(payload)
    await router.push(`/projects/${encodeURIComponent(restored.id)}/write`)
  } catch (caught) {
    if (caught instanceof BackupFileError && caught.kind === 'too_large') {
      error.value = '备份文件超过 100 MB，无法在浏览器中恢复。'
    } else if (caught instanceof BackupFileError) {
      error.value = '文件不是有效的 JSON 备份。'
    } else if (caught instanceof ApiError && caught.status === 422) {
      error.value = '备份格式不兼容或文件已损坏。'
    } else if (caught instanceof ApiError && [401, 403].includes(caught.status)) {
      error.value = '当前账号没有恢复权限，请重新登录后再试。'
    } else {
      error.value = '恢复失败，请稍后重试。'
    }
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="export-page">
    <header class="export-head">
      <div>
        <div class="kicker">MANUSCRIPT DELIVERY</div>
        <h1>带走《{{ store.project?.title }}》</h1>
        <p>从数据库读取全部章节。没打开过的正文也会完整导出。</p>
      </div>
      <div class="export-total"><strong>{{ store.totalWords.toLocaleString() }}</strong><span>字 · {{ store.totalChapters }} 章</span></div>
    </header>

    <main class="export-grid">
      <section class="export-config" aria-labelledby="export-options-title">
        <h2 id="export-options-title">交付设置</h2>
        <fieldset>
          <legend>附带内容</legend>
          <label class="check-line">
            <input v-model="includeOutline" type="checkbox">
            <span>大纲与章纲</span><small>放在正文之前，方便编辑校对</small>
          </label>
          <label class="check-line">
            <input v-model="includeCodex" type="checkbox">
            <span>设定库</span><small>人物、地点、势力、物品与结构化属性</small>
          </label>
        </fieldset>

        <fieldset>
          <legend>文件格式</legend>
          <div class="format-list">
            <button v-for="item in formatOptions" :key="item.value" type="button" :data-active="format === item.value" @click="format = item.value">
              <strong>{{ item.label }}</strong><small>{{ item.note }}</small>
            </button>
          </div>
        </fieldset>

        <fieldset v-if="format === 'txt' || format === 'markdown'">
          <legend>章节组织</legend>
          <div class="split-control" aria-label="章节组织">
            <button type="button" :data-active="split === 'single'" @click="split = 'single'">合并为一个文件</button>
            <button type="button" :data-active="split === 'zip'" @click="split = 'zip'">分章打包 ZIP</button>
          </div>
        </fieldset>
      </section>

      <aside class="export-manifest" aria-label="导出清单">
        <div class="manifest-title"><span>导出清单</span><span>数据库实时生成</span></div>
        <ol>
          <li v-for="(item, index) in manifest" :key="item.label">
            <span>{{ String(index + 1).padStart(2, '0') }}</span>
            <div><strong>{{ item.label }}</strong><small>{{ item.detail }}</small></div>
            <b>{{ item.value }}</b>
          </li>
        </ol>
        <button class="primary-action" type="button" :disabled="busy !== null" @click="exportNow">
          {{ busy === 'export' ? '正在生成…' : `导出 ${selectedFormat.label}` }}
        </button>
        <p class="ownership-note">导出永久免费。文件由当前数据库快照生成，不修改原稿。</p>
      </aside>
    </main>

    <section class="backup-band" aria-labelledby="backup-title">
      <div>
        <div class="kicker">LOSSLESS BACKUP</div>
        <h2 id="backup-title">完整备份与恢复</h2>
        <p>JSON 备份包含正文版本、章纲历史和设定关系。恢复时始终创建新作品，不覆盖现有稿件。</p>
      </div>
      <div class="backup-actions">
        <button type="button" :disabled="busy !== null" @click="backupNow">{{ busy === 'backup' ? '正在备份…' : '下载完整备份' }}</button>
        <button type="button" :disabled="busy !== null" @click="restoreInput?.click()">{{ busy === 'restore' ? '正在恢复…' : '从备份恢复' }}</button>
        <input ref="restoreInput" type="file" accept="application/json,.json" hidden @change="restoreBackup">
      </div>
    </section>

    <div v-if="message || error" class="export-feedback" :data-error="!!error" role="status">{{ error || message }}</div>
  </div>
</template>

<style scoped>
.export-page { height: 100%; overflow: auto; background: var(--panel); color: var(--ink); }
.export-head { min-height: 190px; padding: 36px clamp(24px, 5vw, 72px) 30px; display: flex; align-items: flex-end; justify-content: space-between; gap: 32px; border-bottom: var(--hair) solid var(--line); background: var(--paper); }
.export-head h1 { margin: 8px 0 10px; font-size: clamp(25px, 3vw, 36px); font-family: var(--font-serif); letter-spacing: 0; }
.export-head p, .backup-band p { margin: 0; color: var(--ink-2); line-height: 1.65; }
.export-total { text-align: right; white-space: nowrap; }
.export-total strong { display: block; font: 700 32px/1 var(--font-mono); }
.export-total span { display: block; margin-top: 8px; color: var(--ink-3); }
.export-grid { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(320px, .7fr); max-width: 1180px; margin: 0 auto; }
.export-config, .export-manifest { padding: 34px clamp(24px, 4vw, 52px) 44px; }
.export-config { border-right: var(--hair) solid var(--line); }
.export-config h2, .backup-band h2 { margin: 0 0 28px; font-size: 18px; letter-spacing: 0; }
fieldset { margin: 0 0 30px; padding: 0; border: 0; }
legend { width: 100%; margin-bottom: 12px; padding-bottom: 8px; border-bottom: var(--hair) solid var(--line); color: var(--ink-3); font: 700 11px/1 var(--font-mono); text-transform: uppercase; }
.check-line { display: grid; grid-template-columns: 20px 1fr; gap: 3px 10px; padding: 11px 0; cursor: pointer; }
.check-line input { grid-row: 1 / 3; align-self: center; width: 16px; height: 16px; accent-color: var(--accent); }
.check-line span { font-weight: 700; }
.check-line small, .format-list small, .manifest-title, .export-manifest li small { color: var(--ink-3); }
.format-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: var(--hair) solid var(--line); }
.format-list button { min-height: 74px; padding: 13px 15px; display: flex; flex-direction: column; align-items: flex-start; gap: 5px; border: 0; border-right: var(--hair) solid var(--line); border-bottom: var(--hair) solid var(--line); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.format-list button:nth-child(even) { border-right: 0; }
.format-list button:nth-last-child(-n + 2) { border-bottom: 0; }
.format-list button[data-active='true'] { background: var(--ink); color: var(--paper); }
.format-list button[data-active='true'] small { color: color-mix(in srgb, var(--paper) 72%, transparent); }
.split-control { display: grid; grid-template-columns: 1fr 1fr; border: var(--hair) solid var(--line); }
.split-control button { min-height: 40px; border: 0; background: transparent; color: var(--ink-2); cursor: pointer; }
.split-control button + button { border-left: var(--hair) solid var(--line); }
.split-control button[data-active='true'] { background: var(--paper-2); color: var(--ink); font-weight: 700; }
.export-manifest { background: var(--paper-2); }
.manifest-title { display: flex; justify-content: space-between; padding-bottom: 13px; border-bottom: 2px solid var(--ink); font: 700 10px/1 var(--font-mono); text-transform: uppercase; }
.export-manifest ol { margin: 0; padding: 0; list-style: none; }
.export-manifest li { min-height: 68px; display: grid; grid-template-columns: 30px 1fr auto; align-items: center; gap: 12px; border-bottom: var(--hair) solid var(--line); }
.export-manifest li > span { color: var(--ink-3); font: 600 10px/1 var(--font-mono); }
.export-manifest li div { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.export-manifest li b { font-size: 12px; }
.primary-action { width: 100%; height: 44px; margin-top: 26px; border: 1px solid var(--ink); background: var(--ink); color: var(--paper); font-weight: 700; cursor: pointer; }
.primary-action:disabled, .backup-actions button:disabled { opacity: .5; cursor: wait; }
.ownership-note { margin: 13px 0 0; color: var(--ink-3); font-size: 11px; line-height: 1.6; }
.backup-band { padding: 30px clamp(24px, 5vw, 72px); display: flex; align-items: center; justify-content: space-between; gap: 28px; border-block: var(--hair) solid var(--line); background: var(--paper); }
.backup-band h2 { margin: 7px 0 8px; }
.backup-band p { max-width: 680px; }
.backup-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.backup-actions button { min-height: 38px; padding: 0 15px; border: 1px solid var(--line-strong); background: transparent; color: var(--ink); cursor: pointer; }
.export-feedback { position: sticky; bottom: 0; padding: 10px 24px; border-top: var(--hair) solid var(--ok-line); background: var(--ok-soft); color: var(--ok-ink); text-align: center; font-weight: 700; }
.export-feedback[data-error='true'] { border-color: var(--alert-line); background: var(--alert-soft); color: var(--alert-ink); }
button:focus-visible, input:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
@media (max-width: 780px) {
  .export-head, .backup-band { align-items: flex-start; flex-direction: column; }
  .export-total { text-align: left; }
  .export-grid { grid-template-columns: 1fr; }
  .export-config { border-right: 0; border-bottom: var(--hair) solid var(--line); }
  .format-list { grid-template-columns: 1fr; }
  .format-list button, .format-list button:nth-child(even), .format-list button:nth-last-child(-n + 2) { border-right: 0; border-bottom: var(--hair) solid var(--line); }
  .format-list button:last-child { border-bottom: 0; }
  .split-control { grid-template-columns: 1fr; }
  .split-control button + button { border-left: 0; border-top: var(--hair) solid var(--line); }
  .backup-actions { width: 100%; justify-content: stretch; }
  .backup-actions button { flex: 1 1 180px; }
}
</style>
