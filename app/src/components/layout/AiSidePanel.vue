<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { contentApi } from '@/api/content'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { useStylesStore } from '@/stores/styles'
import { CODEX_KIND_LABEL, type ContextLayer, type GenerationControls } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import { generationDraftApi } from '@/api/generation'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { GenerationDraftDetail, GenerationDraftSummary } from '@/types'

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
}>()
const emit = defineEmits<{
  generate: [options: GenerationControls]
  stop: []
  insertDraft: [draft: GenerationDraftDetail]
  rejectDraft: [id: string]
  refreshDrafts: []
}>()

const router = useRouter()
const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const styles = useStylesStore()
const { toProject } = useProjectNavigation()

const layers = ref<ContextLayer[]>([])
const tab = ref<'ai' | 'drafts' | 'refs' | 'notes'>('ai')
const targetWords = ref(3000)
const model = ref<'basic' | 'advanced'>('basic')
const loadingContext = ref(false)
const contextError = ref('')
const selectedDraft = ref<GenerationDraftDetail | null>(null)
const draftDetailLoading = ref(false)
const draftDetailError = ref('')

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

watch(() => project.activeId, () => {
  selectedDraft.value = null
  draftDetailError.value = ''
})

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
</script>

<template>
  <div>
    <div class="wk-tabs">
      <button
        v-for="t in (['ai', 'drafts', 'refs', 'notes'] as const)"
        :key="t"
        class="wk-tab"
        type="button"
        role="tab"
        :aria-selected="tab === t"
        @click="tab = t"
      >
        {{ t === 'ai' ? 'AI' : t === 'drafts' ? `候选 ${props.drafts?.length ?? 0}` : t === 'refs' ? `引用 ${refs.length}` : '笔记' }}
      </button>
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
      <div class="wk-head"><span>本章引用</span><span class="wk-head-push">{{ refs.length }} 条</span></div>
      <button
        v-for="e in refs"
        :key="e.id"
        class="wk-row"
        type="button"
        :style="{ minHeight: 'auto', padding: 'var(--u2) var(--u3)', display: 'grid', gap: '2px' }"
        @click="router.push(toProject('codex'))"
      >
        <span class="row" :style="{ gap: '6px' }">
          <span :style="{ fontWeight: 700, color: 'var(--ink)' }">{{ e.name }}</span>
          <span class="pill">{{ CODEX_KIND_LABEL[e.kind] }}</span>
          <span v-if="e.resident" class="pill pill-soft">常驻</span>
          <span v-if="e.conflicts" class="pill pill-alert">{{ e.conflicts }} 冲突</span>
        </span>
        <span :style="{ color: 'var(--ink-3)', fontSize: 'var(--fs-sm)', lineHeight: 1.6, whiteSpace: 'normal' }">
          {{ e.summary }}
        </span>
      </button>
      <p v-if="!refs.length" :style="{ padding: 'var(--u5) var(--u3)', color: 'var(--ink-3)', lineHeight: 1.7 }">
        正文里以 <code>@</code> 插入的条目会出现在这里，并自动进入生成上下文。
      </p>
    </template>

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
</style>
