<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError } from '@/api/http'
import type { StyleConfidence, StyleProfile, StyleStatus } from '@/api/styles'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { useStylesStore } from '@/stores/styles'

const DIMENSION_ORDER = [
  'sentence_rhythm', 'dialogue', 'description_density',
  'imagery', 'chapter_hooks', 'recurring_language'
]

const shell = useShellStore()
const route = useRoute()
const project = useProjectStore()
const styles = useStylesStore()
const activeId = ref<string | null>(null)
const busyId = ref<string | null>(null)
const pageError = ref('')
const dialogOpen = ref(false)
const editing = ref<StyleProfile | null>(null)
const formName = ref('')
const formText = ref('')
const formDefault = ref(false)
const dialogError = ref('')
const fileName = ref('')

const projectId = computed(() => String(route.params.projectId ?? ''))
const profiles = computed(() => styles.profiles)
const active = computed(() => profiles.value.find((profile) => profile.id === activeId.value) ?? null)
const sampleCount = computed(() => formText.value.replace(/\s/g, '').length)
const dimensions = computed(() => {
  const source = active.value?.dimensions ?? {}
  return DIMENSION_ORDER.flatMap((key) => source[key] ? [[key, source[key]] as const] : [])
})
const isBound = computed(() => active.value?.id === project.project?.styleProfile)

const STATUS_LABEL: Record<StyleStatus, string> = {
  pending: '待抽取', processing: '抽取中', ready: '可用于生成', failed: '抽取失败'
}
const CONFIDENCE_LABEL: Record<StyleConfidence, string> = {
  insufficient: '样本不足', low: '低置信度', standard: '标准置信度'
}

onMounted(async () => {
  shell.setCrumb('风格档')
  try {
    await Promise.all([
      styles.load(true),
      project.loadedProjectId === projectId.value ? Promise.resolve() : project.load(projectId.value)
    ])
    const boundId = project.project?.styleProfile
    activeId.value = profiles.value.some((profile) => profile.id === boundId) ? boundId
      ?? profiles.value.find((profile) => profile.isDefault)?.id
      ?? profiles.value[0]?.id
      ?? null
      : profiles.value.find((profile) => profile.isDefault)?.id ?? profiles.value[0]?.id ?? null
  } catch (error) {
    pageError.value = messageFor(error)
  }
})

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const payload = JSON.parse(error.message) as { detail?: { code?: string; message?: string } }
      const code = payload.detail?.code
      if (code === 'INSUFFICIENT_CREDITS') return '当前积分不足，补充额度后再抽取。'
      if (code === 'STYLE_SAMPLE_TOO_SHORT') return '样文至少需要 5,000 字才能抽取风格指纹。'
      if (code === 'STYLE_PROFILE_NOT_READY') return '风格档抽取完成后才能绑定作品。'
      return payload.detail?.message ?? code ?? '请求没有完成，请稍后重试。'
    } catch { return `请求失败（${error.status}）` }
  }
  return error instanceof Error ? error.message : '请求没有完成，请稍后重试。'
}

function formatWords(value: number) {
  return value >= 10_000 ? `${(value / 10_000).toFixed(value >= 100_000 ? 0 : 1)} 万` : value.toLocaleString()
}

function formatDate(value?: string) {
  if (!value) return '尚未抽取'
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function openCreate() {
  editing.value = null
  formName.value = ''
  formText.value = ''
  formDefault.value = profiles.value.length === 0
  fileName.value = ''
  dialogError.value = ''
  dialogOpen.value = true
}

function openEdit() {
  if (!active.value) return
  editing.value = active.value
  formName.value = active.value.name
  formText.value = ''
  formDefault.value = active.value.isDefault
  fileName.value = ''
  dialogError.value = ''
  dialogOpen.value = true
}

async function readFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['txt', 'md'].includes(suffix ?? '')) {
    dialogError.value = '只支持 .txt 和 .md 样文。'
    input.value = ''
    return
  }
  const text = await file.text()
  if (text.length > 500_000) {
    dialogError.value = '样文不能超过 50 万字符。'
    input.value = ''
    return
  }
  formText.value = text
  fileName.value = file.name
  dialogError.value = ''
}

async function submitProfile() {
  const name = formName.value.trim()
  if (!name) { dialogError.value = '请填写风格档名称。'; return }
  if (!editing.value && !formText.value.trim()) { dialogError.value = '请粘贴或选择一份样文。'; return }
  if (formText.value.length > 500_000) { dialogError.value = '样文不能超过 50 万字符。'; return }
  busyId.value = editing.value?.id ?? 'new'
  dialogError.value = ''
  try {
    const profile = editing.value
      ? await styles.update(editing.value.id, {
          name,
          ...(formText.value.trim() ? { sampleText: formText.value } : {}),
          ...(formDefault.value && !editing.value.isDefault ? { isDefault: true } : {})
        })
      : await styles.create({ name, sampleText: formText.value, isDefault: formDefault.value })
    activeId.value = profile.id
    dialogOpen.value = false
    if (formText.value.trim() && profile.sampleWords >= 5_000) await extract(profile.id)
  } catch (error) {
    dialogError.value = messageFor(error)
  } finally {
    busyId.value = null
  }
}

async function extract(id: string) {
  busyId.value = id
  pageError.value = ''
  try {
    await styles.extract(id)
  } catch (error) {
    pageError.value = messageFor(error)
    await styles.load(true)
  } finally {
    busyId.value = null
  }
}

async function makeDefault() {
  if (!active.value) return
  busyId.value = active.value.id
  pageError.value = ''
  try { await styles.update(active.value.id, { isDefault: true }) }
  catch (error) { pageError.value = messageFor(error) }
  finally { busyId.value = null }
}

async function toggleBinding() {
  if (!active.value || !project.project) return
  busyId.value = active.value.id
  pageError.value = ''
  try {
    const id = await styles.bind(project.project.id, isBound.value ? null : active.value.id)
    project.setStyleProfile(id)
  } catch (error) { pageError.value = messageFor(error) }
  finally { busyId.value = null }
}

async function removeActive() {
  if (!active.value || !window.confirm(`删除风格档“${active.value.name}”？已绑定作品会自动解绑。`)) return
  const id = active.value.id
  busyId.value = id
  pageError.value = ''
  try {
    await styles.remove(id)
    if (project.project?.styleProfile === id) project.setStyleProfile(null)
    activeId.value = profiles.value.find((profile) => profile.isDefault)?.id ?? profiles.value[0]?.id ?? null
  } catch (error) { pageError.value = messageFor(error) }
  finally { busyId.value = null }
}
</script>

<template>
  <div class="style-page">
    <Teleport defer to="#topbar-actions">
      <button class="topbar-btn" type="button" @click="openCreate">新建风格档</button>
    </Teleport>

    <div v-if="styles.loading && !profiles.length" class="style-state">正在读取风格档…</div>
    <div v-else class="style-workspace">
      <aside class="profile-index">
        <header><span>样文实验室</span><strong>{{ profiles.length }}</strong></header>
        <button
          v-for="profile in profiles"
          :key="profile.id"
          class="profile-row"
          type="button"
          :aria-current="profile.id === activeId"
          @click="activeId = profile.id"
        >
          <span class="profile-row-main">
            <strong>{{ profile.name }}</strong>
            <small>{{ formatWords(profile.sampleWords) }} 字 · {{ STATUS_LABEL[profile.status] }}</small>
          </span>
          <span class="status-mark" :data-status="profile.status" />
          <span v-if="profile.isDefault" class="default-mark">默认</span>
        </button>
        <button v-if="!profiles.length" class="profile-empty" type="button" @click="openCreate">
          <strong>还没有风格档</strong><span>新建并加入第一份样文</span>
        </button>
        <footer>样文原文不会进入生成提示，删除风格档会同时删除样文。</footer>
      </aside>

      <main v-if="active" class="fingerprint-sheet">
        <div v-if="pageError" class="page-alert" role="alert">
          <span>{{ pageError }}</span>
          <button type="button" aria-label="关闭提示" title="关闭" @click="pageError = ''">×</button>
        </div>

        <header class="profile-head">
          <div>
            <div class="profile-kicker">
              <span>{{ STATUS_LABEL[active.status] }}</span>
              <span v-if="active.isDefault">默认风格</span>
              <span v-if="isBound">当前作品已启用</span>
            </div>
            <h1>{{ active.name }}</h1>
            <p>{{ formatWords(active.sampleWords) }} 字样本 · {{ CONFIDENCE_LABEL[active.confidence] }} · {{ formatDate(active.extractedAt) }}</p>
          </div>
          <div class="profile-actions">
            <button class="wk-btn" type="button" :disabled="busyId !== null" @click="openEdit">编辑</button>
            <button v-if="!active.isDefault" class="wk-btn" type="button" :disabled="busyId !== null" @click="makeDefault">设为默认</button>
            <button class="wk-btn danger-button" type="button" :disabled="busyId !== null" @click="removeActive">删除</button>
          </div>
        </header>

        <section class="profile-facts" aria-label="风格档状态">
          <div><span>样本规模</span><strong>{{ formatWords(active.sampleWords) }} 字</strong></div>
          <div><span>指纹状态</span><strong>{{ STATUS_LABEL[active.status] }}</strong></div>
          <div><span>可信程度</span><strong>{{ CONFIDENCE_LABEL[active.confidence] }}</strong></div>
          <div><span>绑定作品</span><strong>{{ active.boundProjectIds.length }} 部</strong></div>
        </section>

        <section v-if="active.status === 'ready'" class="binding-band">
          <div>
            <span>当前作品</span><strong>{{ project.project?.title ?? '读取中' }}</strong>
            <p>{{ isBound ? '整章生成会读取这份风格指纹。' : '当前作品尚未使用这份风格指纹。' }}</p>
          </div>
          <button class="wk-btn" :data-primary="!isBound" type="button" :disabled="busyId !== null" @click="toggleBinding">
            {{ isBound ? '从当前作品解绑' : '应用到当前作品' }}
          </button>
        </section>

        <section v-if="active.status === 'ready' && dimensions.length" class="dimension-section">
          <div class="section-heading"><span>STYLE FINGERPRINT</span><h2>六维风格指纹</h2></div>
          <article v-for="([key, dimension], index) in dimensions" :key="key" class="dimension-row">
            <div class="dimension-index">0{{ index + 1 }}</div>
            <div class="dimension-copy">
              <h3>{{ dimension.title }}</h3><p>{{ dimension.summary }}</p>
              <div v-if="dimension.traits.length" class="trait-line">
                <span>保留</span><b v-for="trait in dimension.traits" :key="trait">{{ trait }}</b>
              </div>
              <div v-if="dimension.avoid.length" class="trait-line is-avoid">
                <span>避免</span><b v-for="item in dimension.avoid" :key="item">{{ item }}</b>
              </div>
            </div>
            <div class="dimension-score"><strong>{{ dimension.score }}</strong><div><span :style="{ width: dimension.score + '%' }" /></div></div>
          </article>
        </section>

        <section v-else class="extraction-state" :data-status="active.status">
          <span class="extraction-code">{{ active.status === 'processing' ? 'ANALYZING' : active.status === 'failed' ? 'FAILED' : 'PENDING' }}</span>
          <h2>{{ active.status === 'processing' ? '正在抽取六维风格指纹' : active.status === 'failed' ? '这次抽取没有完成' : active.confidence === 'insufficient' ? '样本还不够长' : '样文已经保存' }}</h2>
          <p v-if="active.status === 'failed'">{{ active.errorDetail ?? '模型网关没有返回有效指纹。' }}</p>
          <p v-else-if="active.confidence === 'insufficient'">当前 {{ active.sampleWords.toLocaleString() }} 字，达到 5,000 字后可开始抽取。</p>
          <p v-else-if="active.status === 'processing'">页面可以离开，完成后重新进入即可查看结果。</p>
          <p v-else>抽取会均匀读取样文开头、中段和结尾，并计入本月 AI 用量。</p>
          <button v-if="active.status !== 'processing' && active.confidence !== 'insufficient'" class="wk-btn" data-primary="true" type="button" :disabled="busyId !== null" @click="extract(active.id)">
            {{ busyId === active.id ? '正在抽取…' : active.status === 'failed' ? '重新抽取' : '开始抽取' }}
          </button>
          <button v-else-if="active.confidence === 'insufficient'" class="wk-btn" type="button" @click="openEdit">补充样文</button>
        </section>
      </main>

      <main v-else class="style-state"><strong>建立你的第一份风格指纹</strong><button class="wk-btn" data-primary="true" type="button" @click="openCreate">新建风格档</button></main>
    </div>

    <div v-if="dialogOpen" class="dialog-backdrop" @click.self="dialogOpen = false">
      <section class="style-dialog" role="dialog" aria-modal="true" :aria-labelledby="editing ? 'edit-style-title' : 'new-style-title'">
        <header>
          <div><span>{{ editing ? 'UPDATE SAMPLE' : 'NEW SAMPLE' }}</span><h2 :id="editing ? 'edit-style-title' : 'new-style-title'">{{ editing ? '编辑风格档' : '新建风格档' }}</h2></div>
          <button type="button" aria-label="关闭" title="关闭" @click="dialogOpen = false">×</button>
        </header>
        <div class="dialog-body">
          <label class="form-field"><span>名称</span><input v-model="formName" maxlength="200" placeholder="例如：田园日常短句" autofocus></label>
          <label class="form-field"><span>{{ editing ? '替换样文（不替换可留空）' : '样文' }}</span><textarea v-model="formText" maxlength="500000" rows="12" placeholder="粘贴你自己的小说正文，不要加入写作指令。" /></label>
          <div class="sample-toolbar">
            <label class="file-button"><input type="file" accept=".txt,.md,text/plain,text/markdown" @change="readFile"><span>选择 TXT / Markdown</span></label>
            <span v-if="fileName">{{ fileName }}</span><strong>{{ sampleCount.toLocaleString() }} / 500,000 字符</strong>
          </div>
          <label class="default-check"><input v-model="formDefault" type="checkbox" :disabled="editing?.isDefault"><span>设为默认风格档</span></label>
          <p v-if="dialogError" class="dialog-error" role="alert">{{ dialogError }}</p>
        </div>
        <footer>
          <span>{{ sampleCount > 0 && sampleCount < 5_000 ? '不足 5,000 字将只保存，不自动抽取。' : '保存后自动抽取风格指纹。' }}</span>
          <div><button class="wk-btn" type="button" :disabled="busyId !== null" @click="dialogOpen = false">取消</button><button class="wk-btn" data-primary="true" type="button" :disabled="busyId !== null" @click="submitProfile">{{ busyId !== null ? '正在保存…' : '保存' }}</button></div>
        </footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.style-page { height: 100%; min-width: 0; overflow: hidden; color: var(--ink); background: var(--panel); }
.style-workspace { height: 100%; display: grid; grid-template-columns: 276px minmax(0, 1fr); }
.profile-index { min-width: 0; display: flex; flex-direction: column; overflow: auto; border-right: var(--hair) solid var(--line-strong); background: var(--panel-sunken); }
.profile-index > header { min-height: 52px; display: flex; align-items: center; justify-content: space-between; padding: 0 var(--u4); border-bottom: var(--hair) solid var(--line-strong); color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.profile-index > header strong { font-family: var(--font-mono); color: var(--ink-2); }
.profile-row { position: relative; width: 100%; min-height: 74px; display: grid; grid-template-columns: minmax(0, 1fr) 8px; align-items: center; gap: 8px; padding: 12px 16px; border: 0; border-bottom: var(--hair) solid var(--line); background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
.profile-row:hover { background: var(--panel); }
.profile-row[aria-current='true'] { background: var(--paper); box-shadow: inset 3px 0 0 var(--primary); }
.profile-row-main { min-width: 0; display: grid; gap: 5px; }
.profile-row-main strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--fs); }
.profile-row-main small { color: var(--ink-3); font-size: var(--fs-xs); }
.status-mark { width: 7px; height: 7px; background: var(--ink-4); }
.status-mark[data-status='ready'] { background: var(--primary); }
.status-mark[data-status='failed'] { background: var(--alert); }
.status-mark[data-status='processing'] { border: 1px solid var(--primary); background: transparent; animation: pulse 1.2s ease-in-out infinite; }
.default-mark { position: absolute; top: 8px; right: 31px; color: var(--ink-4); font-size: 9px; }
.profile-empty { display: grid; gap: 6px; margin: var(--u4); padding: 18px; border: var(--hair) dashed var(--line-strong); background: var(--panel); color: var(--ink-3); text-align: left; cursor: pointer; }
.profile-empty strong { color: var(--ink); }
.profile-index > footer { margin-top: auto; padding: var(--u4); border-top: var(--hair) solid var(--line); color: var(--ink-3); font-size: var(--fs-xs); line-height: 1.7; }
.fingerprint-sheet { min-width: 0; overflow: auto; background: var(--paper); }
.page-alert { min-height: 38px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px clamp(20px, 4vw, 54px); border-bottom: var(--hair) solid var(--alert-line); background: var(--alert-soft); color: var(--alert-ink); font-size: var(--fs-sm); }
.page-alert button, .style-dialog > header > button { width: 28px; height: 28px; border: 0; background: transparent; color: inherit; font-size: 20px; cursor: pointer; }
.profile-head { min-height: 178px; display: flex; align-items: flex-end; justify-content: space-between; gap: var(--u5); padding: 34px clamp(20px, 4vw, 54px) 26px; border-bottom: 2px solid var(--ink); }
.profile-kicker { display: flex; flex-wrap: wrap; gap: 7px; color: var(--ink-3); font-size: 10px; font-weight: 700; }
.profile-kicker span + span::before { content: '/'; margin-right: 7px; color: var(--line-strong); }
.profile-head h1 { margin: 12px 0 6px; font-family: var(--font-prose); font-size: 30px; line-height: 1.2; letter-spacing: 0; }
.profile-head p { margin: 0; color: var(--ink-3); font-size: var(--fs-sm); }
.profile-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.danger-button { color: var(--alert-ink); }
.profile-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: var(--hair) solid var(--line-strong); }
.profile-facts > div { min-width: 0; display: grid; gap: 7px; padding: 15px clamp(12px, 2vw, 24px); border-right: var(--hair) solid var(--line); }
.profile-facts > div:last-child { border-right: 0; }
.profile-facts span { color: var(--ink-3); font-size: 10px; }
.profile-facts strong { overflow-wrap: anywhere; font-size: var(--fs-sm); }
.binding-band { min-height: 90px; display: flex; align-items: center; justify-content: space-between; gap: var(--u4); padding: 16px clamp(20px, 4vw, 54px); border-bottom: var(--hair) solid var(--line-strong); background: var(--panel-sunken); }
.binding-band > div { min-width: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 12px; }
.binding-band span { color: var(--ink-3); font-size: 10px; font-weight: 700; }
.binding-band strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.binding-band p { grid-column: 2; margin: 0; color: var(--ink-3); font-size: var(--fs-xs); }
.dimension-section { padding: 30px clamp(20px, 4vw, 54px) 54px; }
.section-heading { display: grid; grid-template-columns: 150px 1fr; align-items: baseline; padding-bottom: 14px; border-bottom: var(--hair) solid var(--line-strong); }
.section-heading span { color: var(--ink-4); font: 10px/1 var(--font-mono); }
.section-heading h2 { margin: 0; font-size: 17px; }
.dimension-row { min-height: 134px; display: grid; grid-template-columns: 64px minmax(0, 1fr) 124px; gap: 22px; align-items: center; padding: 20px 0; border-bottom: var(--hair) solid var(--line); }
.dimension-index { align-self: start; padding-top: 3px; color: var(--ink-4); font: 11px/1 var(--font-mono); }
.dimension-copy { min-width: 0; }
.dimension-copy h3 { margin: 0 0 7px; font-size: 14px; }
.dimension-copy p { max-width: 70ch; margin: 0; color: var(--ink-2); font-size: var(--fs-sm); line-height: 1.7; }
.trait-line { display: flex; align-items: center; flex-wrap: wrap; gap: 5px; margin-top: 9px; }
.trait-line span { margin-right: 3px; color: var(--ink-4); font-size: 9px; font-weight: 700; }
.trait-line b { padding: 2px 6px; border: var(--hair) solid var(--line-strong); color: var(--ink-2); font-size: 10px; font-weight: 400; }
.trait-line.is-avoid b { color: var(--alert-ink); border-color: var(--alert-line); }
.dimension-score strong { display: block; margin-bottom: 8px; font: 24px/1 var(--font-mono); text-align: right; }
.dimension-score > div { height: 5px; background: var(--line); }
.dimension-score span { display: block; height: 100%; background: var(--primary); }
.extraction-state, .style-state { min-height: 360px; display: grid; place-content: center; justify-items: start; gap: 12px; padding: 40px; }
.style-state { height: 100%; justify-items: center; color: var(--ink-3); }
.extraction-state h2, .style-state strong { margin: 0; color: var(--ink); font-size: 20px; }
.extraction-state p { max-width: 58ch; margin: 0 0 8px; color: var(--ink-3); line-height: 1.7; }
.extraction-state[data-status='failed'] h2, .extraction-state[data-status='failed'] .extraction-code { color: var(--alert-ink); }
.extraction-code { color: var(--ink-4); font: 10px/1 var(--font-mono); }
.dialog-backdrop { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; padding: 18px; background: rgb(20 24 25 / 55%); }
.style-dialog { width: min(720px, 100%); max-height: calc(100vh - 36px); display: grid; grid-template-rows: auto minmax(0, 1fr) auto; overflow: hidden; border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--paper); box-shadow: 0 18px 56px rgb(0 0 0 / 24%); }
.style-dialog > header { min-height: 78px; display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 2px solid var(--ink); }
.style-dialog > header span { color: var(--ink-4); font: 9px/1 var(--font-mono); }
.style-dialog h2 { margin: 6px 0 0; font-size: 19px; }
.dialog-body { overflow: auto; padding: 20px; }
.form-field { display: grid; gap: 7px; margin-bottom: 16px; color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.form-field input, .form-field textarea { width: 100%; padding: 9px 11px; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--panel); color: var(--ink); font: inherit; line-height: 1.75; resize: vertical; }
.form-field input { height: 38px; }
.sample-toolbar { min-height: 32px; display: flex; align-items: center; gap: 9px; margin-top: -8px; color: var(--ink-3); font-size: var(--fs-xs); }
.sample-toolbar > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sample-toolbar > strong { margin-left: auto; flex: none; font-family: var(--font-mono); font-weight: 400; }
.file-button input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.file-button span { min-height: 28px; display: grid; place-items: center; padding: 0 9px; border: var(--hair) solid var(--line-strong); background: var(--panel); color: var(--ink-2); cursor: pointer; }
.default-check { display: flex; align-items: center; gap: 8px; margin-top: 18px; color: var(--ink-2); font-size: var(--fs-sm); }
.dialog-error { margin: 14px 0 0; color: var(--alert-ink); font-size: var(--fs-sm); }
.style-dialog > footer { min-height: 60px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 20px; border-top: var(--hair) solid var(--line-strong); color: var(--ink-3); font-size: var(--fs-xs); }
.style-dialog > footer > div { display: flex; gap: 7px; }
@keyframes pulse { 50% { opacity: .25; } }
@media (prefers-reduced-motion: reduce) { .status-mark[data-status='processing'] { animation: none; } }
@media (max-width: 760px) {
  .style-page { overflow: auto; }
  .style-workspace { height: auto; min-height: 100%; grid-template-columns: minmax(0, 1fr); }
  .profile-index { max-height: 238px; border-right: 0; border-bottom: var(--hair) solid var(--line-strong); }
  .profile-index > footer { display: none; }
  .profile-head { min-height: 0; align-items: flex-start; flex-direction: column; padding-top: 24px; }
  .profile-actions { justify-content: flex-start; }
  .profile-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .profile-facts > div:nth-child(2) { border-right: 0; }
  .profile-facts > div:nth-child(-n + 2) { border-bottom: var(--hair) solid var(--line); }
  .binding-band { align-items: flex-start; flex-direction: column; }
  .section-heading { grid-template-columns: 1fr; gap: 7px; }
  .dimension-row { grid-template-columns: 30px minmax(0, 1fr); gap: 12px; }
  .dimension-score { grid-column: 2; display: grid; grid-template-columns: 42px 1fr; align-items: center; gap: 10px; }
  .dimension-score strong { margin: 0; text-align: left; font-size: 18px; }
  .style-dialog > footer { align-items: flex-start; flex-direction: column; }
  .style-dialog > footer > div { align-self: flex-end; }
}
</style>
