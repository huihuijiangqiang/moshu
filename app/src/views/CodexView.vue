<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useCodexStore, codexEntrySearchText } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { CODEX_KIND_LABEL, type CharacterProfile, type CodexEntry, type CodexEntryDraft, type CodexKind } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import { embeddingApi, type EmbeddingJob } from '@/api/embedding'

const codex = useCodexStore()
const project = useProjectStore()
const shell = useShellStore()
const router = useRouter()
const { toProject } = useProjectNavigation()

const kinds = Object.keys(CODEX_KIND_LABEL) as CodexKind[]
type Scope = 'kind' | 'resident' | 'pending' | 'conflict' | 'all'
const scope = ref<Scope>('kind')
const selectedId = ref<string | null>(null)
const mobileDetailOpen = ref(false)
const embeddingJob = ref<EmbeddingJob | null>(null)
const embeddingError = ref('')
const queueingEmbedding = ref(false)
const formOpen = ref(false)
const editingId = ref<string | null>(null)
const formBusy = ref(false)
const formError = ref('')
const deleteOpen = ref(false)
const actionBusy = ref(false)
const actionError = ref('')
let embeddingTimer: ReturnType<typeof setTimeout> | null = null

interface CodexFormState {
  kind: CodexKind
  name: string
  aliases: string
  summary: string
  resident: boolean
  role: string
  age: string
  personality: string
  desire: string
  motivation: string
  flaw: string
  fear: string
  ability: string
  limitation: string
  speech: string
  appearance: string
  background: string
  currentState: string
  arcPast: string
  arcCurrent: string
  arcNext: string
  facts: string
  plantedChapterId: string
  expectedChapterId: string
  foreshadowResolved: boolean
  resolvedChapterId: string
}

function blankForm(kind: CodexKind = codex.kind): CodexFormState {
  return {
    kind, name: '', aliases: '', summary: '', resident: false,
    role: '', age: '', personality: '', desire: '', motivation: '', flaw: '', fear: '',
    ability: '', limitation: '', speech: '', appearance: '', background: '', currentState: '',
    arcPast: '', arcCurrent: '', arcNext: '', facts: '',
    plantedChapterId: '', expectedChapterId: '', foreshadowResolved: false,
    resolvedChapterId: ''
  }
}

const form = ref<CodexFormState>(blankForm())

const embeddingLabel = computed(() => {
  switch (embeddingJob.value?.status) {
    case 'ready': return '检索索引已就绪'
    case 'queued': return embeddingJob.value.dispatchAttempts
      ? `检索索引重新投递中 · ${embeddingJob.value.dispatchAttempts} 次`
      : '检索索引已排队'
    case 'running': return '正在更新检索索引'
    case 'retrying': return `检索索引重试中 · ${embeddingJob.value.attempts} 次`
    case 'dead_letter': return `检索索引失败 · ${embeddingJob.value.attempts + embeddingJob.value.dispatchAttempts} 次`
    default: return '检索索引待更新'
  }
})

async function refreshEmbeddingStatus() {
  const projectId = project.loadedProjectId
  if (!projectId) return
  if (embeddingTimer) clearTimeout(embeddingTimer)
  try {
    embeddingJob.value = await embeddingApi.status(projectId)
    embeddingError.value = ''
  } catch (error) {
    embeddingError.value = error instanceof Error ? error.message : '索引状态加载失败'
  }
  if (embeddingJob.value && ['queued', 'running', 'retrying'].includes(embeddingJob.value.status)) {
    embeddingTimer = setTimeout(refreshEmbeddingStatus, 2500)
  }
}

async function queueEmbeddingBackfill() {
  const projectId = project.loadedProjectId
  if (!projectId || queueingEmbedding.value) return
  queueingEmbedding.value = true
  embeddingError.value = ''
  try {
    embeddingJob.value = await embeddingApi.queue(projectId)
    embeddingTimer = setTimeout(refreshEmbeddingStatus, 1000)
  } catch (error) {
    embeddingError.value = error instanceof Error ? error.message : '索引任务入队失败'
  } finally {
    queueingEmbedding.value = false
  }
}

onMounted(() => {
  shell.setCrumb('设定库')
  void refreshEmbeddingStatus()
})
onBeforeUnmount(() => {
  if (embeddingTimer) clearTimeout(embeddingTimer)
})
watch(() => project.loadedProjectId, () => void refreshEmbeddingStatus())

const scopes: { key: Scope; label: string; count: () => number }[] = [
  { key: 'all', label: '全部', count: () => codex.entries.length },
  { key: 'resident', label: '常驻', count: () => codex.resident.length },
  { key: 'pending', label: '待确认', count: () => codex.pending.length },
  { key: 'conflict', label: '有冲突', count: () => codex.entries.filter((entry) => entry.conflicts > 0).length }
]

/** 搜索时跨类型，人物动机、能力边界和关系信息也参与检索。 */
const rows = computed<CodexEntry[]>(() => {
  const q = codex.query.trim().toLowerCase()
  let pool = codex.entries
  if (scope.value === 'resident') pool = pool.filter((entry) => entry.resident)
  else if (scope.value === 'pending') pool = pool.filter((entry) => entry.status === 'pending')
  else if (scope.value === 'conflict') pool = pool.filter((entry) => entry.conflicts > 0)
  else if (scope.value === 'kind' && !q) pool = pool.filter((entry) => entry.kind === codex.kind)

  if (q) pool = pool.filter((entry) => codexEntrySearchText(entry).includes(q))

  return [...pool].sort((a, b) => {
    if ((a.status === 'pending') !== (b.status === 'pending')) return a.status === 'pending' ? -1 : 1
    if (!!a.conflicts !== !!b.conflicts) return a.conflicts ? -1 : 1
    return b.refChapters.length - a.refChapters.length
  })
})

const selected = computed(() => rows.value.find((entry) => entry.id === selectedId.value) ?? rows.value[0] ?? null)
const currentListLabel = computed(() => scope.value === 'kind' ? CODEX_KIND_LABEL[codex.kind] : scopes.find((item) => item.key === scope.value)?.label)

watch(rows, (list) => {
  if (!list.some((entry) => entry.id === selectedId.value)) {
    const preferred = scope.value === 'pending' ? list[0] : list.find((entry) => entry.status === 'confirmed') ?? list[0]
    selectedId.value = preferred?.id ?? null
  }
}, { immediate: true })

function pickKind(kind: CodexKind) {
  scope.value = 'kind'
  codex.kind = kind
  codex.query = ''
  mobileDetailOpen.value = false
}

function pickScope(nextScope: Scope) {
  scope.value = nextScope
  codex.query = ''
  mobileDetailOpen.value = false
}

function pickEntry(id: string) {
  selectedId.value = id
  mobileDetailOpen.value = true
}

function selectRelation(targetId?: string) {
  if (!targetId || !codex.byId.has(targetId)) return
  const target = codex.byId.get(targetId)
  scope.value = 'all'
  codex.query = ''
  if (target) codex.kind = target.kind
  selectedId.value = targetId
  mobileDetailOpen.value = true
}

const residentTokens = computed(() => codex.resident.length * 1900)
const latestRef = (entry: CodexEntry) => entry.refChapters.length ? Math.max(...entry.refChapters) : null
const gapChapters = (entry: CodexEntry) => {
  const last = latestRef(entry)
  return last === null ? null : Math.max(0, project.totalChapters - last)
}

const characterCore = computed(() => [
  { label: '核心欲望', value: selected.value?.character?.desire },
  { label: '行动动机', value: selected.value?.character?.motivation },
  { label: '致命缺陷', value: selected.value?.character?.flaw },
  { label: '深层恐惧', value: selected.value?.character?.fear }
])

const characterConstraints = computed(() => [
  { label: '能力', value: selected.value?.character?.ability },
  { label: '能力边界', value: selected.value?.character?.limitation },
  { label: '语言习惯', value: selected.value?.character?.speech },
  { label: '外在识别', value: selected.value?.character?.appearance },
  { label: '前史', value: selected.value?.character?.background }
])

const characterArc = computed(() => [
  { key: 'past', label: '过去', value: selected.value?.character?.arc?.past },
  { key: 'current', label: '当前', value: selected.value?.character?.arc?.current },
  { key: 'next', label: '下一步', value: selected.value?.character?.arc?.next }
])

const writingAnchors = computed(() => {
  if (selected.value?.kind === 'character') {
    return [
      { label: '当前状态', value: selected.value.character?.currentState },
      { label: '推进方向', value: selected.value.character?.arc?.next },
      { label: '不可写错', value: selected.value.character?.limitation }
    ]
  }
  return (selected.value?.facts ?? []).slice(0, 3)
})

function openChapter(index: number) {
  const chapter = project.chapters.find((item) => item.index === index)
  router.push({
    path: toProject('write'),
    query: chapter ? { chapter: chapter.id } : undefined
  })
}

function openCreate() {
  editingId.value = null
  form.value = blankForm(codex.kind)
  formError.value = ''
  formOpen.value = true
}

function openEdit() {
  const entry = selected.value
  if (!entry) return
  const character = entry.character ?? {}
  form.value = {
    ...blankForm(entry.kind),
    kind: entry.kind,
    name: entry.name,
    aliases: entry.aliases.join('、'),
    summary: entry.summary,
    resident: entry.resident,
    role: character.role ?? '',
    age: character.age ?? '',
    personality: character.personality?.join('、') ?? '',
    desire: character.desire ?? '',
    motivation: character.motivation ?? '',
    flaw: character.flaw ?? '',
    fear: character.fear ?? '',
    ability: character.ability ?? '',
    limitation: character.limitation ?? '',
    speech: character.speech ?? '',
    appearance: character.appearance ?? '',
    background: character.background ?? '',
    currentState: character.currentState ?? '',
    arcPast: character.arc?.past ?? '',
    arcCurrent: character.arc?.current ?? '',
    arcNext: character.arc?.next ?? '',
    facts: entry.facts?.map((fact) => `${fact.label}：${fact.value}`).join('\n') ?? '',
    plantedChapterId: entry.plantedChapterId ?? '',
    expectedChapterId: entry.expectedChapterId ?? '',
    foreshadowResolved: entry.foreshadowResolved ?? false,
    resolvedChapterId: entry.resolvedChapterId ?? ''
  }
  editingId.value = entry.id
  formError.value = ''
  formOpen.value = true
}

function openDelete() {
  actionError.value = ''
  deleteOpen.value = true
}

function splitList(value: string): string[] {
  return [...new Set(value.split(/[、，,\n]/).map((item) => item.trim()).filter(Boolean))]
}

function parseFacts(value: string) {
  return value.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
    const separator = line.search(/[：:]/)
    if (separator < 1 || !line.slice(separator + 1).trim()) throw new Error('fact_format')
    return { label: line.slice(0, separator).trim(), value: line.slice(separator + 1).trim() }
  })
}

function formDraft(): CodexEntryDraft {
  const state = form.value
  let character: CharacterProfile | undefined
  if (state.kind === 'character') {
    character = {
      role: state.role, age: state.age, personality: splitList(state.personality),
      desire: state.desire, motivation: state.motivation, flaw: state.flaw, fear: state.fear,
      ability: state.ability, limitation: state.limitation, speech: state.speech,
      appearance: state.appearance, background: state.background, currentState: state.currentState,
      arc: { past: state.arcPast, current: state.arcCurrent, next: state.arcNext }
    }
  }
  return {
    kind: state.kind,
    name: state.name.trim(),
    aliases: splitList(state.aliases),
    summary: state.summary.trim(),
    resident: state.resident,
    status: editingId.value ? selected.value?.status ?? 'confirmed' : 'confirmed',
    character,
    facts: state.kind === 'character' ? undefined : parseFacts(state.facts),
    plantedChapterId: state.kind === 'foreshadow' ? state.plantedChapterId : undefined,
    expectedChapterId: state.kind === 'foreshadow' ? state.expectedChapterId || undefined : undefined,
    foreshadowResolved: state.kind === 'foreshadow' ? state.foreshadowResolved : undefined,
    resolvedChapterId: state.kind === 'foreshadow' && state.foreshadowResolved
      ? state.resolvedChapterId || undefined
      : undefined
  }
}

async function submitEntry() {
  if (formBusy.value) return
  if (!form.value.name.trim()) { formError.value = '请填写设定名称。'; return }
  if (form.value.kind === 'foreshadow' && !form.value.plantedChapterId) {
    formError.value = '请选择伏笔埋设章节。'; return
  }
  if (form.value.kind === 'foreshadow' && form.value.foreshadowResolved && !form.value.resolvedChapterId) {
    formError.value = '请选择实际回收章节。'; return
  }
  if (form.value.kind === 'foreshadow') {
    const planted = project.chapters.find((chapter) => chapter.id === form.value.plantedChapterId)
    const expected = project.chapters.find((chapter) => chapter.id === form.value.expectedChapterId)
    const resolved = project.chapters.find((chapter) => chapter.id === form.value.resolvedChapterId)
    if (planted && expected && expected.index < planted.index) {
      formError.value = '预计回收章节不能早于埋设章节。'; return
    }
    if (planted && resolved && resolved.index < planted.index) {
      formError.value = '实际回收章节不能早于埋设章节。'; return
    }
  }
  const projectId = project.loadedProjectId
  if (!projectId) { formError.value = '当前作品尚未加载完成。'; return }
  formBusy.value = true
  formError.value = ''
  try {
    const draft = formDraft()
    const entry = editingId.value
      ? await codex.update(editingId.value, draft)
      : await codex.create(projectId, draft)
    codex.kind = entry.kind
    scope.value = 'kind'
    selectedId.value = entry.id
    formOpen.value = false
    void refreshEmbeddingStatus()
  } catch (error) {
    formError.value = error instanceof Error && error.message === 'fact_format'
      ? '关键事实请按“标签：内容”逐行填写。'
      : '设定保存失败，请稍后重试。'
  } finally {
    formBusy.value = false
  }
}

async function confirmPending() {
  if (!selected.value || actionBusy.value) return
  actionBusy.value = true
  actionError.value = ''
  try {
    await codex.confirm(selected.value.id)
  } catch {
    actionError.value = '确认失败，请稍后重试。'
  } finally {
    actionBusy.value = false
  }
}

async function discardPending() {
  if (!selected.value || actionBusy.value) return
  actionBusy.value = true
  actionError.value = ''
  try {
    await codex.drop(selected.value.id)
  } catch {
    actionError.value = '忽略失败，请稍后重试。'
  } finally {
    actionBusy.value = false
  }
}

async function deleteSelected() {
  if (!selected.value || actionBusy.value) return
  actionBusy.value = true
  actionError.value = ''
  try {
    await codex.drop(selected.value.id)
    deleteOpen.value = false
  } catch (error) {
    actionError.value = error instanceof Error && error.message === 'codex_entry_in_use'
      ? '这条设定已被正文引用，不能直接删除。'
      : '删除失败，请稍后重试。'
  } finally {
    actionBusy.value = false
  }
}
</script>

<template>
  <div class="codex-layout" :data-mobile-detail="mobileDetailOpen">
    <aside class="codex-library" aria-label="设定库导航">
      <header class="codex-library-head">
        <div><span class="wk-label">世界资料</span><h1>设定库</h1></div>
        <div class="codex-library-actions"><strong>{{ codex.entries.length }}</strong><button type="button" aria-label="新建设定" title="新建设定" @click="openCreate">＋</button></div>
      </header>

      <div class="codex-library-controls">
        <input v-model="codex.query" class="wk-input" placeholder="搜索人物、动机或约束" aria-label="搜索设定">

        <div class="codex-kind-tabs" role="tablist" aria-label="设定类型">
          <button
            v-for="kind in kinds"
            :key="kind"
            type="button"
            role="tab"
            :aria-selected="scope === 'kind' && codex.kind === kind"
            @click="pickKind(kind)"
          >
            <span>{{ CODEX_KIND_LABEL[kind] }}</span><small>{{ codex.counts[kind] ?? 0 }}</small>
          </button>
        </div>

        <div class="codex-scope-tabs" aria-label="设定范围">
          <button
            v-for="item in scopes"
            :key="item.key"
            type="button"
            :aria-pressed="scope === item.key"
            @click="pickScope(item.key)"
          >{{ item.label }}<span v-if="item.key === 'pending' || item.key === 'conflict'">{{ item.count() }}</span></button>
        </div>
      </div>

      <section v-if="embeddingJob || embeddingError" class="codex-embedding-status" :data-status="embeddingJob?.status">
        <div>
          <strong>{{ embeddingLabel }}</strong>
          <small v-if="embeddingJob">{{ embeddingJob.freshCount }} / {{ embeddingJob.totalCount }} 条可检索</small>
          <small v-if="embeddingError">{{ embeddingError }}</small>
          <small v-else-if="embeddingJob?.lastError">{{ embeddingJob.errorCode }} · {{ embeddingJob.lastError }}</small>
        </div>
        <button
          v-if="embeddingJob?.canRetry"
          class="wk-btn"
          type="button"
          :disabled="queueingEmbedding"
          @click="queueEmbeddingBackfill"
        >{{ queueingEmbedding ? '入队中' : '重新索引' }}</button>
      </section>

      <div class="codex-list-head"><span>{{ currentListLabel }}</span><small>{{ rows.length }} 条</small></div>
      <div class="codex-list" role="listbox" :aria-label="`${currentListLabel}设定`">
        <button
          v-for="entry in rows"
          :key="entry.id"
          class="codex-list-row"
          type="button"
          role="option"
          :aria-selected="entry.id === selected?.id"
          @click="pickEntry(entry.id)"
        >
          <span class="codex-list-title">
            <strong>{{ entry.name }}</strong>
            <span v-if="entry.status === 'pending'" class="pill pill-alert">待确认</span>
            <span v-if="entry.resident" class="pill pill-soft">常驻</span>
            <span v-if="entry.conflicts" class="pill pill-alert">{{ entry.conflicts }} 冲突</span>
            <small>{{ entry.refChapters.length }} 章</small>
          </span>
          <span class="codex-list-summary">{{ entry.summary }}</span>
        </button>

        <p v-if="!rows.length" class="codex-empty">
          {{ codex.query ? '没有匹配的设定，试试人物动机或能力名称。' : '这一类还没有条目。' }}
        </p>
      </div>

      <footer class="codex-budget">
        <span><strong>常驻上下文</strong><small>{{ codex.resident.length }} 条 · {{ (residentTokens / 1000).toFixed(1) }}k / 6k</small></span>
        <span class="bar" data-layer="resident" :data-over="residentTokens > 6000"><i :style="{ width: Math.min(100, (residentTokens / 6000) * 100) + '%' }" /></span>
      </footer>
    </aside>

    <main class="codex-detail" aria-label="设定详情">
      <template v-if="selected">
        <div class="codex-detail-bar">
          <button class="codex-mobile-back" type="button" aria-label="返回设定列表" @click="mobileDetailOpen = false">←</button>
          <span>{{ CODEX_KIND_LABEL[selected.kind] }}档案</span>
          <span class="codex-detail-status">
            <span v-if="selected.resident" class="pill pill-soft">常驻上下文</span>
            <span v-if="selected.status === 'pending'" class="pill pill-alert">资料待确认</span>
          </span>
          <span class="codex-detail-actions">
            <button type="button" @click="openEdit">编辑</button>
            <button v-if="selected.status === 'confirmed'" type="button" :disabled="selected.refChapters.length > 0" :title="selected.refChapters.length ? '正文已引用，不能直接删除' : '删除设定'" @click="openDelete">删除</button>
          </span>
        </div>

        <article class="codex-sheet">
          <section v-if="selected.status === 'pending'" class="codex-pending">
            <div><strong>守卫从正文抽取到这个人物</strong><p>只保留原文能够确认的资料，空缺项不会被系统擅自推断。</p></div>
            <div class="row">
              <button class="wk-btn" type="button" data-primary="true" :disabled="actionBusy" @click="confirmPending">确认入库</button>
              <button class="wk-btn" type="button" :disabled="actionBusy" @click="discardPending">忽略</button>
            </div>
            <p v-if="actionError" class="codex-action-error" role="alert">{{ actionError }}</p>
          </section>

          <header class="codex-identity">
            <div class="codex-identity-main">
              <span class="wk-label">{{ selected.character?.role ?? CODEX_KIND_LABEL[selected.kind] }}</span>
              <h2>{{ selected.name }}</h2>
              <p v-if="selected.aliases.length" class="codex-aliases">又名 {{ selected.aliases.join('、') }}</p>
              <p class="codex-lead">{{ selected.summary }}</p>
              <div v-if="selected.kind === 'character'" class="codex-character-meta">
                <span>{{ selected.character?.age ?? '年龄待补充' }}</span>
                <span v-for="trait in selected.character?.personality ?? []" :key="trait">{{ trait }}</span>
              </div>
            </div>

            <aside v-if="writingAnchors.length" class="codex-writing-anchor">
              <div class="wk-label">{{ selected.kind === 'character' ? '本章写作锚点' : '写作核对' }}</div>
              <dl>
                <div v-for="item in writingAnchors" :key="item.label">
                  <dt>{{ item.label }}</dt><dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd>
                </div>
              </dl>
            </aside>
          </header>

          <template v-if="selected.kind === 'character'">
            <section class="codex-section" aria-labelledby="character-core-title">
              <div class="codex-section-heading"><h3 id="character-core-title">人物内核</h3><p>这个人为什么行动，又会被什么拖住</p></div>
              <dl class="codex-definition-grid">
                <div v-for="item in characterCore" :key="item.label"><dt>{{ item.label }}</dt><dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd></div>
              </dl>
            </section>

            <section class="codex-section" aria-labelledby="character-constraint-title">
              <div class="codex-section-heading"><h3 id="character-constraint-title">写作约束</h3><p>写对白、动作和能力时必须保持一致</p></div>
              <dl class="codex-constraint-list">
                <div v-for="item in characterConstraints" :key="item.label"><dt>{{ item.label }}</dt><dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd></div>
              </dl>
            </section>

            <section class="codex-section" aria-labelledby="character-arc-title">
              <div class="codex-section-heading"><h3 id="character-arc-title">人物弧</h3><p>变化不是履历，而是下一场戏的方向</p></div>
              <div class="character-arc">
                <div v-for="stage in characterArc" :key="stage.key" class="character-arc-step" :data-current="stage.key === 'current'">
                  <span class="character-arc-marker" /><strong>{{ stage.label }}</strong><p :data-empty="!stage.value">{{ stage.value ?? '待补充' }}</p>
                </div>
              </div>
            </section>
          </template>

          <section v-else-if="selected.facts?.length" class="codex-section" aria-labelledby="codex-facts-title">
            <div class="codex-section-heading"><h3 id="codex-facts-title">关键设定</h3><p>写作时不可违背的事实</p></div>
            <dl class="codex-constraint-list">
              <div v-for="fact in selected.facts" :key="fact.label"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
            </dl>
          </section>

          <section v-if="selected.relations?.length" class="codex-section" aria-labelledby="codex-relations-title">
            <div class="codex-section-heading"><h3 id="codex-relations-title">关联设定</h3><p>人物与世界如何彼此施力</p></div>
            <div class="codex-relations">
              <button
                v-for="relation in selected.relations"
                :key="`${relation.name}-${relation.relation}`"
                type="button"
                :disabled="!relation.targetId"
                @click="selectRelation(relation.targetId)"
              >
                <span><strong>{{ relation.name }}</strong><small>{{ relation.relation }}</small></span>
                <p>{{ relation.note }}</p><span v-if="relation.targetId" aria-hidden="true">→</span>
              </button>
            </div>
          </section>

          <section class="codex-section codex-evidence" aria-labelledby="codex-evidence-title">
            <div class="codex-section-heading"><h3 id="codex-evidence-title">正文依据</h3><p>档案来自哪些章节</p></div>
            <div class="codex-stats">
              <div><strong>{{ selected.refChapters.length }}</strong><span>引用章数</span></div>
              <div><strong>{{ latestRef(selected) ?? '—' }}</strong><span>最近出现</span></div>
              <div><strong :data-alert="(gapChapters(selected) ?? 0) > 30">{{ gapChapters(selected) ?? '—' }}</strong><span>断档章数</span></div>
              <div><strong :data-alert="selected.conflicts > 0">{{ selected.conflicts }}</strong><span>待处理冲突</span></div>
            </div>

            <button v-if="selected.conflicts" class="wk-btn codex-guard-link" type="button" data-primary="true" @click="router.push(toProject('guard'))">
              去一致性守卫处理 {{ selected.conflicts }} 处冲突
            </button>
            <p v-if="selected.kind === 'foreshadow'" class="codex-foreshadow">
              埋于第 {{ selected.plantedAt }} 章
              <template v-if="selected.expectedBy"> · 预计在{{ selected.expectedBy }}回收</template>
              <template v-if="selected.foreshadowResolved"> · 已回收</template>
              <template v-else> · 未回收</template>
            </p>
            <div class="codex-chapters">
              <button
                v-for="chapter in selected.refChapters.slice(-40)"
                :key="chapter"
                class="pill"
                type="button"
                :aria-label="`打开第 ${chapter} 章`"
                @click="openChapter(chapter)"
              >{{ chapter }}</button>
              <span v-if="selected.refChapters.length > 40">仅显示最近 40 章</span>
            </div>
          </section>
        </article>
      </template>

      <p v-else class="codex-empty">从左侧选择一条设定。</p>
    </main>

    <div v-if="formOpen" class="codex-dialog-backdrop" @click.self="formOpen = false">
      <section class="codex-dialog" role="dialog" aria-modal="true" aria-labelledby="codex-form-title">
        <header>
          <div><span>{{ editingId ? 'EDIT ENTRY' : 'NEW ENTRY' }}</span><h2 id="codex-form-title">{{ editingId ? '编辑设定' : '新建设定' }}</h2></div>
          <button type="button" aria-label="关闭" title="关闭" @click="formOpen = false">×</button>
        </header>
        <form @submit.prevent="submitEntry">
          <div class="codex-form-section codex-form-basics">
            <label><span>类型</span><select v-model="form.kind" class="wk-input"><option v-for="kind in kinds" :key="kind" :value="kind">{{ CODEX_KIND_LABEL[kind] }}</option></select></label>
            <label><span>名称</span><input v-model="form.name" class="wk-input" maxlength="200" autocomplete="off" placeholder="人物、地点或规则名称"></label>
            <label class="codex-form-wide"><span>别名</span><input v-model="form.aliases" class="wk-input" maxlength="500" autocomplete="off" placeholder="用顿号或逗号分隔"></label>
            <label class="codex-form-wide"><span>核心描述</span><textarea v-model="form.summary" class="wk-input" rows="4" placeholder="写清这项设定在故事中的作用，以及不可违背的边界。" /></label>
            <label class="codex-resident-toggle"><input v-model="form.resident" type="checkbox"><span><strong>常驻写作上下文</strong><small>每次生成正文都携带这条设定</small></span></label>
          </div>

          <div v-if="form.kind === 'character'" class="codex-form-section">
            <div class="codex-form-heading"><h3>人物内核</h3><p>这些字段会直接参与人物一致性与写作提示。</p></div>
            <div class="codex-form-grid">
              <label><span>角色定位</span><input v-model="form.role" class="wk-input" placeholder="女主、盟友、对手"></label>
              <label><span>年龄</span><input v-model="form.age" class="wk-input" placeholder="二十四岁"></label>
              <label><span>性格关键词</span><input v-model="form.personality" class="wk-input" placeholder="谨慎、务实"></label>
              <label><span>当前状态</span><input v-model="form.currentState" class="wk-input" placeholder="眼下处境与目标"></label>
              <label><span>核心欲望</span><textarea v-model="form.desire" class="wk-input" rows="2" /></label>
              <label><span>行动动机</span><textarea v-model="form.motivation" class="wk-input" rows="2" /></label>
              <label><span>致命缺陷</span><textarea v-model="form.flaw" class="wk-input" rows="2" /></label>
              <label><span>深层恐惧</span><textarea v-model="form.fear" class="wk-input" rows="2" /></label>
              <label><span>能力</span><textarea v-model="form.ability" class="wk-input" rows="2" /></label>
              <label><span>能力边界</span><textarea v-model="form.limitation" class="wk-input" rows="2" /></label>
              <label><span>语言习惯</span><textarea v-model="form.speech" class="wk-input" rows="2" /></label>
              <label><span>外在识别</span><textarea v-model="form.appearance" class="wk-input" rows="2" /></label>
              <label class="codex-form-wide"><span>前史</span><textarea v-model="form.background" class="wk-input" rows="3" /></label>
            </div>
            <div class="codex-form-heading codex-form-subheading"><h3>人物弧</h3><p>记录变化方向，不写流水履历。</p></div>
            <div class="codex-form-grid codex-form-arc">
              <label><span>过去</span><textarea v-model="form.arcPast" class="wk-input" rows="3" /></label>
              <label><span>当前</span><textarea v-model="form.arcCurrent" class="wk-input" rows="3" /></label>
              <label><span>下一步</span><textarea v-model="form.arcNext" class="wk-input" rows="3" /></label>
            </div>
          </div>

          <div v-else-if="form.kind === 'foreshadow'" class="codex-form-section">
            <div class="codex-form-heading"><h3>伏笔生命周期</h3><p>章节用于守卫判断是否超过预计回收点。</p></div>
            <div class="codex-form-grid">
              <label><span>埋设章节</span><select v-model="form.plantedChapterId" class="wk-input" required><option value="" disabled>选择章节</option><option v-for="chapter in project.chapters" :key="chapter.id" :value="chapter.id">第 {{ chapter.index }} 章 · {{ chapter.title }}</option></select></label>
              <label><span>预计回收章节</span><select v-model="form.expectedChapterId" class="wk-input"><option value="">暂不设期限</option><option v-for="chapter in project.chapters" :key="chapter.id" :value="chapter.id">第 {{ chapter.index }} 章 · {{ chapter.title }}</option></select></label>
              <label class="codex-resident-toggle"><input v-model="form.foreshadowResolved" type="checkbox"><span><strong>已经回收</strong><small>记录实际回收章节并关闭逾期提醒</small></span></label>
              <label v-if="form.foreshadowResolved"><span>实际回收章节</span><select v-model="form.resolvedChapterId" class="wk-input"><option value="" disabled>选择章节</option><option v-for="chapter in project.chapters" :key="chapter.id" :value="chapter.id">第 {{ chapter.index }} 章 · {{ chapter.title }}</option></select></label>
            </div>
            <div class="codex-form-heading codex-form-subheading"><h3>关键事实</h3><p>每行一项，格式为“标签：内容”。</p></div>
            <textarea v-model="form.facts" class="wk-input codex-facts-input" rows="6" placeholder="埋设方式：旧井石缝中的半枚铜钥匙&#10;回收目标：打开周家粮仓暗门" />
          </div>

          <div v-else class="codex-form-section">
            <div class="codex-form-heading"><h3>关键事实</h3><p>每行一项，格式为“标签：内容”。</p></div>
            <textarea v-model="form.facts" class="wk-input codex-facts-input" rows="9" placeholder="地理位置：青河以东二十里&#10;进入条件：仅在退潮后可通行" />
          </div>

          <p v-if="formError" class="codex-dialog-error" role="alert">{{ formError }}</p>
          <footer><span>留空字段不会被系统擅自补全。</span><div><button class="wk-btn" type="button" :disabled="formBusy" @click="formOpen = false">取消</button><button class="wk-btn" data-primary="true" type="submit" :disabled="formBusy">{{ formBusy ? '保存中…' : '保存设定' }}</button></div></footer>
        </form>
      </section>
    </div>

    <div v-if="deleteOpen" class="codex-dialog-backdrop" @click.self="deleteOpen = false">
      <section class="codex-delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby="codex-delete-title">
        <span class="wk-label">DELETE ENTRY</span>
        <h2 id="codex-delete-title">删除“{{ selected?.name }}”</h2>
        <p>这会同时删除别名和关系记录，操作无法撤销。</p>
        <p v-if="actionError" class="codex-dialog-error" role="alert">{{ actionError }}</p>
        <div><button class="wk-btn" type="button" :disabled="actionBusy" @click="deleteOpen = false">取消</button><button class="wk-btn codex-danger-button" type="button" :disabled="actionBusy" @click="deleteSelected">{{ actionBusy ? '删除中…' : '确认删除' }}</button></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.codex-library-actions { display: flex; align-items: center; gap: 10px; }
.codex-library-actions strong { color: var(--ink-4); font: 13px/1 var(--font-mono); }
.codex-library-actions button { width: 30px; height: 30px; display: grid; place-items: center; padding: 0; border: var(--hair) solid var(--line-strong); border-radius: 3px; color: var(--ink-2); background: var(--paper); font-size: 19px; cursor: pointer; }
.codex-library-actions button:hover { color: var(--primary); border-color: var(--primary); }
.codex-detail-actions { display: flex; gap: 3px; margin-left: 4px; }
.codex-detail-actions button { min-height: 26px; padding: 0 8px; border: 0; border-radius: 3px; color: var(--ink-3); background: transparent; font-size: 11px; cursor: pointer; }
.codex-detail-actions button:hover { color: var(--ink); background: var(--paper); }
.codex-detail-actions button:disabled { cursor: not-allowed; opacity: .42; }
.codex-action-error { flex-basis: 100%; color: var(--alert-ink) !important; }
.codex-dialog-backdrop { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; padding: 18px; background: rgb(20 24 25 / 55%); }
.codex-dialog { width: min(780px, 100%); max-height: calc(100vh - 36px); display: grid; grid-template-rows: auto minmax(0, 1fr); overflow: hidden; border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--paper); box-shadow: 0 18px 56px rgb(0 0 0 / 24%); }
.codex-dialog > header { min-height: 72px; display: flex; align-items: center; justify-content: space-between; padding: 14px 22px; border-bottom: 2px solid var(--ink); }
.codex-dialog > header span { color: var(--ink-4); font: 9px/1 var(--font-mono); }
.codex-dialog h2 { margin: 5px 0 0; font-size: 20px; }
.codex-dialog > header button { width: 30px; height: 30px; border: 0; color: var(--ink-2); background: transparent; font-size: 22px; cursor: pointer; }
.codex-dialog form { min-height: 0; overflow-y: auto; }
.codex-form-section { padding: 22px; border-bottom: var(--hair) solid var(--line); }
.codex-form-basics, .codex-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.codex-form-section label { min-width: 0; display: grid; align-content: start; gap: 7px; color: var(--ink-3); font-size: 11px; font-weight: 700; }
.codex-form-section textarea { min-height: 68px; padding-top: 10px; resize: vertical; line-height: 1.6; }
.codex-form-wide { grid-column: 1 / -1; }
.codex-resident-toggle { grid-column: 1 / -1; display: flex !important; align-items: center; gap: 10px !important; padding-top: 2px; cursor: pointer; }
.codex-resident-toggle input { width: 16px; height: 16px; accent-color: var(--primary); }
.codex-resident-toggle span { display: grid; gap: 2px; }
.codex-resident-toggle small { color: var(--ink-4); font-weight: 400; }
.codex-form-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 20px; margin-bottom: 16px; }
.codex-form-heading h3 { margin: 0; font-size: 16px; }
.codex-form-heading p { margin: 0; color: var(--ink-4); font-size: 11px; }
.codex-form-subheading { margin-top: 24px; padding-top: 18px; border-top: var(--hair) solid var(--line); }
.codex-form-arc { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.codex-facts-input { width: 100%; min-height: 210px !important; }
.codex-dialog-error { margin: 14px 22px 0; color: var(--alert-ink); font-size: 12px; }
.codex-dialog footer { min-height: 60px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 22px; color: var(--ink-4); font-size: 11px; }
.codex-dialog footer > div { display: flex; gap: 7px; }
.codex-delete-dialog { width: min(430px, 100%); padding: 24px; border-top: 3px solid var(--alert); border-radius: 4px; background: var(--paper); box-shadow: 0 18px 56px rgb(0 0 0 / 24%); }
.codex-delete-dialog h2 { margin: 8px 0 0; font-size: 20px; }
.codex-delete-dialog p { margin: 14px 0 0; color: var(--ink-3); font-size: 13px; line-height: 1.65; }
.codex-delete-dialog > div { display: flex; justify-content: flex-end; gap: 7px; margin-top: 24px; }
.codex-danger-button { color: white; border-color: var(--alert); background: var(--alert); }
@media (max-width: 700px) {
  .codex-detail-status { display: none; }
  .codex-detail-actions { margin-left: auto; }
  .codex-dialog-backdrop { padding: 0; place-items: stretch; }
  .codex-dialog { width: 100%; max-height: 100vh; border: 0; border-radius: 0; }
  .codex-form-basics, .codex-form-grid, .codex-form-arc { grid-template-columns: minmax(0, 1fr); }
  .codex-form-wide, .codex-resident-toggle { grid-column: auto; }
  .codex-form-heading { display: block; }
  .codex-form-heading p { margin-top: 4px; }
  .codex-dialog footer { align-items: flex-start; flex-direction: column; }
  .codex-dialog footer > div { align-self: stretch; }
  .codex-dialog footer .wk-btn { flex: 1; }
}
</style>
