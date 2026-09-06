<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { contentApi } from '@/api/content'
import { useProjectStore } from '@/stores/project'
import { codexEntrySearchText, useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { useStylesStore } from '@/stores/styles'
import { CODEX_KIND_LABEL, type CodexEntry, type CodexStateHistoryItem, type ContextLayer, type GenerationControls } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import { generationDraftApi, previewGeneration, type GenerationPreview } from '@/api/generation'
import AppIcon from '@/components/ui/AppIcon.vue'
import ReviewPanel from '@/components/editor/ReviewPanel.vue'
import type { GenerationDraftDetail, GenerationDraftSummary, ReviewAnchor, ReviewComment, ReviewRound, ReviewWorkspace } from '@/types'

/**
 * AI 面板。三件事按重要性排：能不能生成（章纲 + 参数）、
 * 生成会带什么进去（四层预算）、生成前有什么没解决（守卫提醒）。
 * 四层预算是产品的硬约束，必须逐层可见 —— 作者要知道 25k 被谁吃掉了。
 */
const props = defineProps<{
  generating?: boolean
  generationError?: string
  drafts?: GenerationDraftSummary[]
  draftsLoading?: boolean
  reviewWorkspace?: ReviewWorkspace
  reviewAnchor?: ReviewAnchor
  reviewLoading?: boolean
  reviewBusy?: boolean
  reviewError?: string
  reviewSubmitDisabledReason?: string
}>()
const emit = defineEmits<{
  generate: [options: GenerationControls]
  stop: []
  insertDraft: [draft: GenerationDraftDetail]
  rejectDraft: [id: string]
  refreshDrafts: []
  refreshReviews: []
  submitReview: [note: string]
  addReviewComment: [roundId: string, content: string]
  updateReviewComment: [roundId: string, comment: ReviewComment, content: string]
  resolveReviewComment: [roundId: string, comment: ReviewComment]
  decideReview: [round: ReviewRound, decision: 'approved' | 'changes_requested', note: string]
  locateReviewComment: [comment: ReviewComment]
}>()

const router = useRouter()
const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const styles = useStylesStore()
const { toProject } = useProjectNavigation()

const layers = ref<ContextLayer[]>([])
const tab = ref<'ai' | 'drafts' | 'refs' | 'review' | 'notes'>('ai')
const targetWords = ref(3000)
const model = ref<'basic' | 'advanced'>('basic')
const loadingContext = ref(false)
const contextError = ref('')
const selectedDraft = ref<GenerationDraftDetail | null>(null)
const draftDetailLoading = ref(false)
const draftDetailError = ref('')
const referenceScope = ref<'chapter' | 'all'>('chapter')
const referenceQuery = ref('')
const selectedReferenceId = ref<string | null>(null)
const referenceStates = ref<CodexStateHistoryItem[]>([])
const referenceStatesLoading = ref(false)
const referenceStatesError = ref('')
const previewOpen = ref(false)
const previewLoading = ref(false)
const previewError = ref('')
const preview = ref<GenerationPreview | null>(null)
let referenceStateRequest = 0
let previewRequest = 0

const BUDGET = 25000

watch(
  () => [project.project?.id, project.activeId] as const,
  async ([projectId, chapterId]) => {
    if (!projectId || !chapterId) return
    loadingContext.value = true
    contextError.value = ''
    try {
      const [context] = await Promise.all([
        contentApi.getContextLayers(projectId, chapterId),
        styles.load()
      ])
      layers.value = context
    } catch (error) {
      layers.value = []
      contextError.value = error instanceof Error ? error.message : '上下文加载失败'
    } finally {
      loadingContext.value = false
    }
  },
  { immediate: true }
)

function requestGeneration() {
  emit('generate', {
    targetWords: Math.max(200, Math.min(20000, targetWords.value || 3000)),
    model: model.value,
    useStyleProfile: true,
    dialogueDensity: 'high'
  })
}

async function openPromptPreview() {
  if (!project.activeId || previewLoading.value) return
  const requestId = ++previewRequest
  const chapterId = project.activeId
  previewOpen.value = true
  previewLoading.value = true
  previewError.value = ''
  try {
    const result = await previewGeneration({
      chapterId,
      targetWords: Math.max(200, Math.min(20000, targetWords.value || 3000)),
      model: model.value,
      useStyleProfile: true,
      dialogueDensity: 'high'
    })
    if (requestId === previewRequest) preview.value = result
  } catch (error) {
    if (requestId === previewRequest) {
      preview.value = null
      previewError.value = error instanceof Error ? error.message : '提示词预览加载失败'
    }
  } finally {
    if (requestId === previewRequest) previewLoading.value = false
  }
}

function closePromptPreview() {
  ++previewRequest
  previewOpen.value = false
  previewLoading.value = false
}

const total = computed(() => layers.value.reduce((s, l) => s + l.tokens, 0))
const over = computed(() => total.value > BUDGET)
const k = (n: number) => (n / 1000).toFixed(1) + 'k'

/** 每层的上限，和后端 assembler 的四层预算一致 */
const CAP: Record<ContextLayer['key'], number> = {
  resident: 6000,
  retrieved: 5000,
  summary: 4000,
  adjacent: 10000
}
const LAYER_LABEL: Record<string, string> = {
  resident: '常驻设定',
  retrieved: '本章相关设定',
  summary: '前情摘要',
  adjacent: '相邻章节原文'
}

const cost = computed(() => (model.value === 'advanced' ? 35 : 18))
const styleName = computed(() => {
  const id = project.project?.styleProfile
  return id ? styles.byId.get(id)?.name ?? '已绑定风格档' : '未设置'
})

/** 本章引用的设定：mock 下按章号反查 refChapters */
const refs = computed(() => {
  const idx = project.active?.index
  if (!idx) return []
  return codex.entries.filter((e) => e.refChapters.includes(idx))
})

const referenceRows = computed(() => {
  const query = referenceQuery.value.trim().toLowerCase()
  const pool = referenceScope.value === 'chapter' && !query ? refs.value : codex.entries
  return [...pool]
    .filter((entry) => entry.status === 'confirmed')
    .filter((entry) => !query || codexEntrySearchText(entry).includes(query))
    .sort((left, right) => Number(right.resident) - Number(left.resident) || right.refChapters.length - left.refChapters.length)
})

const selectedReference = computed(() => selectedReferenceId.value ? codex.byId.get(selectedReferenceId.value) ?? null : null)

const referenceFacts = computed(() => {
  const entry = selectedReference.value
  if (!entry) return []
  if (entry.kind !== 'character') return entry.facts ?? []
  return [
    { label: '当前状态', value: entry.character?.currentState },
    { label: '行动动机', value: entry.character?.motivation },
    { label: '能力', value: entry.character?.ability },
    { label: '能力边界', value: entry.character?.limitation },
    { label: '语言习惯', value: entry.character?.speech }
  ].filter((item): item is { label: string; value: string } => Boolean(item.value))
})

const effectiveReferenceStates = computed(() => {
  const targetIndex = project.active?.index ?? Number.MAX_SAFE_INTEGER
  const latest = new Map<string, CodexStateHistoryItem>()
  referenceStates.value
    .filter((item) => item.source === 'author' && item.chapterIndex <= targetIndex)
    .forEach((item) => {
      const previous = latest.get(item.stateKey)
      if (!previous || previous.chapterIndex <= item.chapterIndex) latest.set(item.stateKey, item)
    })
  return [...latest.values()].sort((left, right) => left.stateKey.localeCompare(right.stateKey))
})

watch(() => project.activeId, () => {
  ++referenceStateRequest
  ++previewRequest
  selectedDraft.value = null
  draftDetailError.value = ''
  selectedReferenceId.value = null
  referenceStates.value = []
  referenceStatesLoading.value = false
  previewOpen.value = false
  preview.value = null
  previewError.value = ''
  previewLoading.value = false
})

watch(
  [selectedReferenceId, () => project.project?.id],
  async ([entryId, projectId]) => {
    const requestId = ++referenceStateRequest
    referenceStates.value = []
    referenceStatesError.value = ''
    if (!entryId || !projectId) {
      referenceStatesLoading.value = false
      return
    }
    referenceStatesLoading.value = true
    try {
      const result = await contentApi.listCodexStateHistory(projectId, entryId)
      if (requestId === referenceStateRequest) referenceStates.value = result
    } catch {
      if (requestId === referenceStateRequest) referenceStatesError.value = '当前状态加载失败。'
    } finally {
      if (requestId === referenceStateRequest) referenceStatesLoading.value = false
    }
  }
)

function openReference(entry: CodexEntry) {
  selectedReferenceId.value = entry.id
}

function openFullReference(entry: CodexEntry) {
  codex.kind = entry.kind
  codex.query = entry.name
  router.push(toProject('codex'))
}

async function openDraft(draft: GenerationDraftSummary) {
  draftDetailLoading.value = true
  draftDetailError.value = ''
  try {
    selectedDraft.value = await generationDraftApi.get(draft.id)
  } catch (error) {
    draftDetailError.value = error instanceof Error ? error.message : '候选内容加载失败'
  } finally {
    draftDetailLoading.value = false
  }
}

function draftLabel(draft: GenerationDraftSummary) {
  if (draft.requestSummary.action) return draft.requestSummary.action
  return draft.kind === 'chapter' ? '整章生成' : '行内生成'
}

function draftTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function rejectSelected() {
  if (!selectedDraft.value) return
  emit('rejectDraft', selectedDraft.value.id)
  selectedDraft.value = null
}

function forwardReviewComment(roundId: string, content: string) {
  emit('addReviewComment', roundId, content)
}

function forwardReviewCommentUpdate(roundId: string, comment: ReviewComment, content: string) {
  emit('updateReviewComment', roundId, comment, content)
}

function forwardReviewCommentResolution(roundId: string, comment: ReviewComment) {
  emit('resolveReviewComment', roundId, comment)
}

function forwardReviewDecision(round: ReviewRound, decision: 'approved' | 'changes_requested', note: string) {
  emit('decideReview', round, decision, note)
}
</script>

<template>
  <div>
    <div class="wk-tabs">
      <button
        v-for="t in (['ai', 'drafts', 'refs', 'review', 'notes'] as const)"
        :key="t"
        class="wk-tab"
        type="button"
        role="tab"
        :aria-selected="tab === t"
        @click="tab = t"
      >
        {{ t === 'ai' ? 'AI' : t === 'drafts' ? `候选 ${props.drafts?.length ?? 0}` : t === 'refs' ? '资料' : t === 'review' ? `审稿 ${props.reviewWorkspace?.rounds[0]?.comments.filter((item) => item.status === 'open').length ?? 0}` : '笔记' }}
      </button>
    </div>

    <div v-if="previewOpen" class="prompt-preview" role="region" aria-label="生成提示词预览">
      <div class="prompt-preview-head">
        <div>
          <div class="wk-label">生成前检查</div>
          <h2>本次提示词</h2>
        </div>
        <button class="prompt-preview-close" type="button" aria-label="关闭提示词预览" @click="closePromptPreview"><AppIcon name="close" :size="15" /></button>
      </div>

      <p v-if="previewLoading" class="prompt-preview-state">正在装配本章设定，不会调用模型或扣除积分…</p>
      <p v-else-if="previewError" class="prompt-preview-state prompt-preview-error">{{ previewError }}</p>
      <template v-else-if="preview">
        <div class="prompt-preview-summary">
          <span>{{ preview.chapterTitle }}</span>
          <span>{{ preview.model.id }} · {{ preview.targetWords }} 字</span>
          <span>{{ preview.tokenBudget.prompt.toLocaleString() }} tokens</span>
        </div>

        <section class="prompt-preview-section">
          <div class="wk-label">已加载技能 · {{ preview.skills.length }}</div>
          <div class="prompt-skill-list">
            <span v-for="skill in preview.skills" :key="skill.id" class="prompt-skill">
              {{ skill.id }} <small>{{ skill.version }}</small>
            </span>
          </div>
          <p class="prompt-preview-note">场景判断：{{ preview.scene }}。技能只影响写法，不会覆盖已确认设定。</p>
        </section>

        <section class="prompt-preview-section">
          <div class="row-between">
            <div class="wk-label">四层上下文</div>
            <span class="prompt-preview-metric">{{ preview.tokenBudget.context.toLocaleString() }} / {{ preview.tokenBudget.total.toLocaleString() }}</span>
          </div>
          <details v-for="layer in preview.layers" :key="layer.key" class="prompt-layer">
            <summary><span>{{ LAYER_LABEL[layer.key] ?? layer.key }}</span><span>{{ layer.tokens.toLocaleString() }} tokens · {{ layer.items.length }} 项</span></summary>
            <pre>{{ layer.content || '（这一层为空）' }}</pre>
          </details>
          <p v-if="preview.tokenBudget.trimmedLayers.length" class="prompt-preview-note prompt-preview-warning">
            已按预算裁剪：{{ preview.tokenBudget.trimmedLayers.join('、') }}
          </p>
        </section>

        <section class="prompt-preview-section">
          <div class="wk-label">最终发送消息</div>
          <details v-for="message in preview.messages" :key="message.role" class="prompt-message" :open="message.role === 'user'">
            <summary>{{ message.role === 'system' ? 'System · 写作规则' : 'User · 本章任务' }}</summary>
            <pre>{{ message.content }}</pre>
          </details>
        </section>
      </template>
    </div>

    <template v-if="tab === 'ai'">
      <!-- 章纲：生成的依据，放最上面 -->
      <section class="wk-sec">
        <div class="wk-label" :style="{ marginBottom: 'var(--u2)' }">本章章纲</div>
        <ol
          v-if="project.active?.outline.length"
          :style="{ margin: '0 0 var(--u3)', padding: '0 0 0 18px', fontSize: 'var(--fs)', lineHeight: 1.85, color: 'var(--ink-2)' }"
        >
          <li v-for="(o, i) in project.active.outline" :key="i">{{ o }}</li>
        </ol>
        <p v-else :style="{ margin: '0 0 var(--u3)', color: 'var(--ink-3)', lineHeight: 1.7 }">
          本章还没有章纲，先写一句你想发生什么。
        </p>

        <button
          v-if="props.generating"
          class="wk-btn wk-btn-block"
          type="button"
          @click="emit('stop')"
        >停止生成</button>
        <button
          v-else
          class="wk-btn wk-btn-block"
          type="button"
          data-primary="true"
          @click="requestGeneration"
        >按章纲生成整章</button>
        <button
          v-if="!props.generating"
          class="wk-btn wk-btn-block"
          type="button"
          @click="openPromptPreview"
        >预览提示词</button>
        <p v-if="props.generationError" :style="{ margin: 'var(--u2) 0 0', color: 'var(--alert-ink)', fontSize: 'var(--fs-sm)', lineHeight: 1.6 }">
          {{ props.generationError }}
        </p>
      </section>

      <!-- 生成参数 -->
      <section class="wk-sec">
        <div class="wk-label" :style="{ marginBottom: 'var(--u2)' }">生成参数</div>
        <div :style="{ display: 'grid', gap: '6px', fontSize: 'var(--fs)' }">
          <label class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">模型档位</span>
            <select v-model="model" class="wk-input" :style="{ width: '120px', height: '22px' }">
              <option value="basic">基础档</option>
              <option value="advanced">高级档</option>
            </select>
          </label>
          <label class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">目标字数</span>
            <input v-model.number="targetWords" class="wk-input" type="number" step="500"
                   :style="{ width: '120px', height: '22px', fontFamily: 'var(--font-mono)' }">
          </label>
          <div class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">风格档</span>
            <span>{{ styleName }}</span>
          </div>
          <div class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">预估消耗</span>
            <span :style="{ fontFamily: 'var(--font-mono)' }">{{ cost }} 积分</span>
          </div>
        </div>
      </section>

      <!-- 四层上下文预算 -->
      <section class="wk-sec">
        <div class="row-between" :style="{ marginBottom: 'var(--u2)' }">
          <span class="wk-label">上下文预算</span>
          <span
            :style="{
              fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)',
              color: over ? 'var(--alert-ink)' : 'var(--ink-3)',
              fontWeight: over ? 700 : 400
            }"
          >{{ k(total) }} / 25k</span>
        </div>

        <div :style="{ display: 'grid', gap: 'var(--u2)' }">
          <div v-for="l in layers" :key="l.key">
            <div class="row-between" :style="{ fontSize: 'var(--fs-sm)', marginBottom: '3px' }">
              <span :title="l.detail" :style="{ color: 'var(--ink-2)' }">{{ l.label }}</span>
              <span :style="{ fontFamily: 'var(--font-mono)', color: 'var(--ink-3)' }">
                {{ k(l.tokens) }} / {{ k(CAP[l.key]) }}
              </span>
            </div>
            <div class="bar" :data-layer="l.key" :data-over="l.tokens > CAP[l.key]">
              <span :style="{ width: Math.min(100, (l.tokens / CAP[l.key]) * 100) + '%' }" />
            </div>
          </div>
        </div>
        <p v-if="loadingContext" :style="{ margin: 'var(--u2) 0 0', color: 'var(--ink-3)', fontSize: 'var(--fs-sm)' }">正在装配本章上下文…</p>
        <p v-else-if="contextError" :style="{ margin: 'var(--u2) 0 0', color: 'var(--alert-ink)', fontSize: 'var(--fs-sm)' }">{{ contextError }}</p>
      </section>

      <!-- 守卫提醒：只取最紧要的三条 -->
      <section class="wk-sec">
        <div class="row-between" :style="{ marginBottom: 'var(--u2)' }">
          <span class="wk-label" :style="{ color: guard.open.length ? 'var(--alert-ink)' : 'var(--ink-3)' }">
            守卫提醒
          </span>
          <button class="wk-btn wk-btn-xs" type="button" @click="router.push(toProject('guard'))">
            全部 {{ guard.open.length }}
          </button>
        </div>

        <div :style="{ display: 'grid', gap: '6px' }">
          <button
            v-for="i in guard.topThree"
            :key="i.id"
            type="button"
            :style="{
              display: 'grid', gap: '3px', width: '100%', textAlign: 'left', cursor: 'pointer',
              padding: '6px var(--u2)', fontSize: 'var(--fs-sm)', lineHeight: 1.55,
              background: 'var(--panel-sunken)', border: 0,
              borderLeft: '2px solid ' + (i.severity === 'high' ? 'var(--alert)' : 'var(--ink-4)')
            }"
            @click="router.push(toProject('guard'))"
          >
            <span class="row" :style="{ gap: '6px' }">
              <span class="pill" :class="i.severity === 'high' ? 'pill-alert' : ''">
                {{ i.severity === 'high' ? '高' : '中' }}
              </span>
              <span :style="{ fontWeight: 700, color: 'var(--ink)' }">{{ i.category }}</span>
              <span :style="{ marginLeft: 'auto', color: 'var(--ink-4)' }">{{ i.chapterRef }}</span>
            </span>
            <span :style="{ color: 'var(--ink-2)' }">{{ i.title }}</span>
          </button>
          <p v-if="!guard.open.length" :style="{ margin: 0, color: 'var(--ink-3)' }">暂无提醒。</p>
        </div>
      </section>
    </template>

    <template v-else-if="tab === 'drafts'">
      <div class="wk-head">
        <span>生成候选</span>
        <span class="wk-head-push">{{ props.drafts?.length ?? 0 }} 条</span>
        <button class="draft-refresh" type="button" title="刷新候选" aria-label="刷新候选" @click="emit('refreshDrafts')">
          <AppIcon name="restore" :size="14" />
        </button>
      </div>

      <div v-if="selectedDraft" class="draft-detail">
        <button class="draft-back" type="button" @click="selectedDraft = null">返回候选列表</button>
        <div class="row-between">
          <strong>{{ draftLabel(selectedDraft) }}</strong>
          <span class="pill" :class="selectedDraft.status === 'failed' ? 'pill-alert' : 'pill-soft'">
            {{ selectedDraft.status === 'streaming' ? '生成中' : selectedDraft.status === 'failed' ? '生成中断' : '待采纳' }}
          </span>
        </div>
        <div class="draft-meta">{{ selectedDraft.generatedWords }} 字 · {{ draftTime(selectedDraft.createdAt) }}</div>
        <div class="draft-prose">{{ selectedDraft.content || '没有可恢复的内容。' }}</div>
        <p v-if="selectedDraft.status === 'failed'" class="draft-warning">这次生成提前中断，已保留完成的部分。</p>
        <p v-else-if="selectedDraft.status === 'streaming'" class="draft-warning">候选仍在写入，完成或停止后才能处理。</p>
        <div class="draft-actions">
          <button class="wk-btn wk-btn-xs" type="button" :disabled="selectedDraft.status === 'streaming'" @click="rejectSelected">舍弃</button>
          <button
            class="wk-btn wk-btn-xs"
            data-primary="true"
            type="button"
            :disabled="!selectedDraft.content || selectedDraft.status === 'streaming'"
            @click="emit('insertDraft', selectedDraft)"
          >放入正文检查</button>
        </div>
      </div>

      <div v-else>
        <p v-if="props.draftsLoading || draftDetailLoading" class="draft-state">正在读取候选…</p>
        <p v-else-if="draftDetailError" class="draft-state draft-state-error">{{ draftDetailError }}</p>
        <template v-else>
          <button
            v-for="draft in props.drafts"
            :key="draft.id"
            class="draft-row"
            type="button"
            @click="openDraft(draft)"
          >
            <span class="row-between">
              <strong>{{ draftLabel(draft) }}</strong>
              <span :class="draft.status === 'failed' ? 'draft-status-failed' : 'muted'">
                {{ draft.status === 'streaming' ? '生成中' : draft.status === 'failed' ? '已中断' : `${draft.generatedWords} 字` }}
              </span>
            </span>
            <span class="draft-excerpt">{{ draft.excerpt || '等待生成内容…' }}</span>
            <span class="draft-time">{{ draftTime(draft.createdAt) }}</span>
          </button>
        </template>
        <p v-if="!props.draftsLoading && !props.drafts?.length" class="draft-state">
          生成结果会保存在这里，刷新页面后仍可找回。
        </p>
      </div>
    </template>

    <template v-else-if="tab === 'refs'">
      <div v-if="selectedReference" class="reference-detail">
        <div class="reference-detail-bar">
          <button type="button" @click="selectedReferenceId = null">返回资料</button>
          <button type="button" @click="openFullReference(selectedReference)">完整档案 →</button>
        </div>
        <header class="reference-identity">
          <span class="wk-label">{{ CODEX_KIND_LABEL[selectedReference.kind] }}</span>
          <h2>{{ selectedReference.name }}</h2>
          <p v-if="selectedReference.aliases.length">又名 {{ selectedReference.aliases.join('、') }}</p>
        </header>
        <p class="reference-summary">{{ selectedReference.summary }}</p>

        <section v-if="referenceFacts.length" class="reference-section">
          <div class="wk-label">写作核对</div>
          <dl>
            <div v-for="fact in referenceFacts" :key="fact.label"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
          </dl>
        </section>

        <section class="reference-section">
          <div class="row-between"><span class="wk-label">本章有效状态</span><small>截至第 {{ project.active?.index ?? '—' }} 章</small></div>
          <p v-if="referenceStatesLoading" class="reference-state-message">正在读取状态…</p>
          <p v-else-if="referenceStatesError" class="reference-state-message is-error">{{ referenceStatesError }}</p>
          <dl v-else-if="effectiveReferenceStates.length" class="reference-states">
            <div v-for="item in effectiveReferenceStates" :key="item.stateKey">
              <dt>{{ item.stateKey }} <small>第 {{ item.chapterIndex }} 章</small></dt><dd>{{ item.value }}</dd>
            </div>
          </dl>
          <p v-else class="reference-state-message">当前章节之前没有作者状态记录。</p>
        </section>

        <section v-if="selectedReference.relations?.length" class="reference-section">
          <div class="wk-label">关联</div>
          <button v-for="relation in selectedReference.relations" :key="`${relation.name}-${relation.relation}`" class="reference-relation" type="button" :disabled="!relation.targetId" @click="relation.targetId && (selectedReferenceId = relation.targetId)">
            <span><strong>{{ relation.name }}</strong><small>{{ relation.relation }}</small></span><span>→</span>
          </button>
        </section>
      </div>

      <div v-else class="reference-browser">
        <div class="wk-head"><span>写作资料</span><span class="wk-head-push">{{ referenceRows.length }} 条</span></div>
        <div class="reference-tools">
          <input v-model="referenceQuery" class="wk-input" placeholder="搜索人物、地点或约束" aria-label="搜索写作资料">
          <div class="reference-scope" aria-label="资料范围">
            <button type="button" :aria-pressed="referenceScope === 'chapter'" @click="referenceScope = 'chapter'">本章 {{ refs.length }}</button>
            <button type="button" :aria-pressed="referenceScope === 'all'" @click="referenceScope = 'all'">全部 {{ codex.entries.length }}</button>
          </div>
        </div>
        <button v-for="entry in referenceRows" :key="entry.id" class="reference-row" type="button" :data-reference-id="entry.id" @click="openReference(entry)">
          <span class="reference-row-title"><strong>{{ entry.name }}</strong><span>{{ CODEX_KIND_LABEL[entry.kind] }}</span><small v-if="entry.resident">常驻</small></span>
          <span>{{ entry.summary }}</span>
        </button>
        <p v-if="!referenceRows.length" class="reference-empty">
          {{ referenceQuery ? '没有匹配资料。可按人物动机、能力或地点名搜索。' : '本章还没有显式引用。切换“全部”可查阅整本设定库。' }}
        </p>
      </div>
    </template>

    <ReviewPanel
      v-else-if="tab === 'review'"
      :workspace="reviewWorkspace"
      :anchor="reviewAnchor"
      :loading="reviewLoading"
      :busy="reviewBusy"
      :error="reviewError"
      :submit-disabled-reason="reviewSubmitDisabledReason"
      @refresh="emit('refreshReviews')"
      @submit="emit('submitReview', $event)"
      @add-comment="forwardReviewComment"
      @update-comment="forwardReviewCommentUpdate"
      @resolve-comment="forwardReviewCommentResolution"
      @decide="forwardReviewDecision"
      @locate="emit('locateReviewComment', $event)"
    />

    <template v-else>
      <div class="wk-head"><span>笔记</span></div>
      <div :style="{ padding: 'var(--u3)' }">
        <textarea
          class="wk-input"
          :style="{ height: '160px', padding: 'var(--u2)', lineHeight: 1.7, resize: 'vertical' }"
          placeholder="只属于你的备忘，不进入 AI 上下文。"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.prompt-preview {
  position: relative;
  z-index: 2;
  max-height: min(78vh, 760px);
  overflow: auto;
  padding: var(--u3);
  color: var(--ink-2);
  background: var(--panel-sunken);
  border-bottom: var(--hair) solid var(--line-strong);
}
.prompt-preview-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--u3); }
.prompt-preview-head h2 { margin: 3px 0 0; color: var(--ink); font-family: var(--font-prose); font-size: 20px; font-weight: 600; }
.prompt-preview-close { width: 26px; height: 26px; padding: 0; color: var(--ink-3); background: transparent; border: 0; font-size: 20px; line-height: 1; cursor: pointer; }
.prompt-preview-close:hover { color: var(--ink); }
.prompt-preview-summary { display: flex; flex-wrap: wrap; gap: 6px 12px; margin: var(--u3) 0; color: var(--ink-3); font: var(--fs-xs)/1.5 var(--font-mono); }
.prompt-preview-summary span:first-child { color: var(--ink); font-family: var(--font-prose); font-size: var(--fs); }
.prompt-preview-section { padding: var(--u3) 0; border-top: var(--hair) solid var(--line); }
.prompt-skill-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: var(--u2); }
.prompt-skill { padding: 3px 6px; color: var(--ink-2); background: var(--panel); border: var(--hair) solid var(--line-strong); font: 10px/1.3 var(--font-mono); }
.prompt-skill small { color: var(--ink-4); }
.prompt-preview-note { margin: var(--u2) 0 0; color: var(--ink-3); font-size: var(--fs-xs); line-height: 1.6; }
.prompt-preview-warning { color: var(--alert-ink); }
.prompt-preview-metric { color: var(--ink-3); font: 10px var(--font-mono); }
.prompt-layer, .prompt-message { margin-top: 5px; background: var(--panel); border: var(--hair) solid var(--line); }
.prompt-layer summary, .prompt-message summary { display: flex; justify-content: space-between; gap: var(--u2); padding: 7px 8px; color: var(--ink-2); font-size: var(--fs-xs); cursor: pointer; list-style: none; }
.prompt-layer summary::-webkit-details-marker, .prompt-message summary::-webkit-details-marker { display: none; }
.prompt-layer summary span:last-child { color: var(--ink-4); font: 10px var(--font-mono); }
.prompt-layer pre, .prompt-message pre { max-height: 220px; overflow: auto; margin: 0; padding: 8px; color: var(--ink-2); border-top: var(--hair) solid var(--line); font: 11px/1.7 var(--font-mono); white-space: pre-wrap; overflow-wrap: anywhere; }
.prompt-preview-state { margin: var(--u4) 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.7; }
.prompt-preview-error { color: var(--alert-ink); }
.prompt-preview:focus-within { outline: 2px solid var(--primary-line); outline-offset: -2px; }
.draft-refresh {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}
.draft-refresh:hover { color: var(--ink); background: var(--panel-sunken); }
.draft-refresh:focus-visible,
.draft-row:focus-visible,
.draft-back:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.draft-row {
  display: grid;
  gap: 5px;
  width: 100%;
  padding: var(--u3);
  text-align: left;
  color: var(--ink);
  background: transparent;
  border: 0;
  border-bottom: var(--hair) solid var(--line);
  cursor: pointer;
}
.draft-row:hover { background: var(--panel-sunken); }
.draft-row strong { font-size: var(--fs); }
.draft-excerpt {
  display: -webkit-box;
  overflow: hidden;
  color: var(--ink-2);
  font-size: var(--fs-sm);
  line-height: 1.6;
  white-space: normal;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.draft-time,
.draft-meta { color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-xs); }
.draft-status-failed { color: var(--alert-ink); font-size: var(--fs-sm); }
.draft-state { margin: 0; padding: var(--u5) var(--u3); color: var(--ink-3); line-height: 1.7; }
.draft-state-error { color: var(--alert-ink); }
.draft-detail { display: grid; gap: var(--u3); padding: var(--u3); }
.draft-back {
  justify-self: start;
  padding: 0;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  cursor: pointer;
}
.draft-back:hover { color: var(--ink); }
.draft-prose {
  max-height: min(52vh, 520px);
  overflow: auto;
  padding: var(--u3) 0;
  color: var(--ink-2);
  font-family: var(--font-serif);
  line-height: 1.85;
  white-space: pre-wrap;
  border-top: var(--hair) solid var(--line);
  border-bottom: var(--hair) solid var(--line);
}
.draft-warning { margin: 0; color: var(--alert-ink); font-size: var(--fs-sm); line-height: 1.6; }
.draft-actions { display: flex; justify-content: flex-end; gap: var(--u2); }
.reference-tools { display: grid; gap: var(--u2); padding: var(--u3); border-bottom: var(--hair) solid var(--line); }
.reference-tools .wk-input { width: 100%; }
.reference-scope { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); height: 27px; border: var(--hair) solid var(--line-strong); border-radius: 3px; overflow: hidden; }
.reference-scope button { min-width: 0; padding: 0 var(--u2); border: 0; border-left: var(--hair) solid var(--line); color: var(--ink-3); background: transparent; font-size: var(--fs-xs); cursor: pointer; }
.reference-scope button:first-child { border-left: 0; }
.reference-scope button[aria-pressed='true'] { color: var(--ink); background: var(--panel-sunken); font-weight: 700; }
.reference-row { width: 100%; min-width: 0; display: grid; gap: 5px; padding: 11px var(--u3); border: 0; border-bottom: var(--hair) solid var(--line); color: var(--ink-3); background: transparent; text-align: left; cursor: pointer; }
.reference-row:hover { background: var(--panel-sunken); }
.reference-row > span:last-child { display: -webkit-box; overflow: hidden; font-size: var(--fs-sm); line-height: 1.55; white-space: normal; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.reference-row-title { min-width: 0; display: flex; align-items: center; gap: 5px; }
.reference-row-title strong { overflow: hidden; color: var(--ink); font-size: var(--fs); text-overflow: ellipsis; white-space: nowrap; }
.reference-row-title span, .reference-row-title small { flex: none; padding: 1px 4px; border: var(--hair) solid var(--line); color: var(--ink-4); font-size: 9px; }
.reference-row-title small { border-color: var(--primary-line); color: var(--primary); }
.reference-empty { margin: 0; padding: var(--u5) var(--u3); color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.7; }
.reference-detail { min-width: 0; padding-bottom: var(--u6); }
.reference-detail-bar { min-height: 36px; display: flex; align-items: center; justify-content: space-between; gap: var(--u2); padding: 0 var(--u3); border-bottom: var(--hair) solid var(--line); }
.reference-detail-bar button { padding: 0; border: 0; color: var(--ink-3); background: transparent; font-size: var(--fs-xs); cursor: pointer; }
.reference-detail-bar button:hover { color: var(--primary); }
.reference-identity { padding: var(--u4) var(--u3) var(--u3); }
.reference-identity h2 { margin: 5px 0 0; font-family: var(--font-prose); font-size: 24px; font-weight: 600; }
.reference-identity p { margin: 4px 0 0; color: var(--ink-4); font-size: var(--fs-xs); }
.reference-summary { margin: 0; padding: 0 var(--u3) var(--u4); color: var(--ink-2); font-family: var(--font-prose); font-size: 14px; line-height: 1.75; overflow-wrap: anywhere; }
.reference-section { padding: var(--u3); border-top: var(--hair) solid var(--line-strong); }
.reference-section > .row-between small { color: var(--ink-4); font: 9px/1.4 var(--font-mono); }
.reference-section dl { margin: var(--u2) 0 0; }
.reference-section dl > div { padding: 8px 0; border-bottom: var(--hair) solid var(--line); }
.reference-section dt { color: var(--ink-4); font-size: 9px; font-weight: 700; }
.reference-section dd { margin: 3px 0 0; color: var(--ink-2); font-size: var(--fs-sm); line-height: 1.55; overflow-wrap: anywhere; }
.reference-states dt { color: var(--primary); }
.reference-states dt small { margin-left: 4px; color: var(--ink-4); font-family: var(--font-mono); font-weight: 400; }
.reference-state-message { margin: var(--u2) 0 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.6; }
.reference-state-message.is-error { color: var(--alert-ink); }
.reference-relation { width: 100%; min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: var(--u2); padding: 8px 0; border: 0; border-bottom: var(--hair) solid var(--line); color: var(--ink-3); background: transparent; text-align: left; cursor: pointer; }
.reference-relation > span:first-child { min-width: 0; display: grid; gap: 2px; }
.reference-relation strong { color: var(--ink); font-size: var(--fs-sm); }
.reference-relation small { color: var(--ink-4); font-size: 9px; }
.reference-relation:hover { color: var(--primary); }
.reference-relation:disabled { cursor: default; opacity: .6; }
.reference-row:focus-visible, .reference-detail-bar button:focus-visible, .reference-relation:focus-visible, .reference-scope button:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
</style>
