<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useGuardStore } from '@/stores/guard'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import type { GuardIssue, GuardKind } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import AppIcon from '@/components/ui/AppIcon.vue'

/**
 * 一致性守卫：主从布局。左列是按严重度排序的告警行，右侧是证据与处置。
 * 关键是「一眼看清冲突的两处原文」——所以证据用左边线对照排版，
 * 本次命中的那一条用红边标出，另一条是被它违反的旧文或设定。
 */
const guard = useGuardStore()
const project = useProjectStore()
const shell = useShellStore()
const route = useRoute()
const router = useRouter()
const { toProject } = useProjectNavigation()

const selectedId = ref<string | null>(null)
const selectedTemporalClaimId = ref<number | null>(null)
const selectedOffsetDays = ref(0)

onMounted(() => {
  shell.setCrumb('一致性守卫')
})

const tabs: { key: GuardKind | 'resolved'; label: string; note: string }[] = [
  { key: 'conflict', label: '设定冲突', note: '需要你判断' },
  { key: 'foreshadow', label: '伏笔待回收', note: '超期会持续提醒' },
  { key: 'pending-entry', label: '新设定待确认', note: '自动抽取，确认后入库' },
  { key: 'resolved', label: '已处置', note: '本卷累计' }
]

/** 高严重度排前面，这是唯一的优先级信号——不引入黄绿蓝色阶 */
const rows = computed<GuardIssue[]>(() =>
  [...guard.visible].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === 'high' ? -1 : 1))
)

const selected = computed(() => rows.value.find((i) => i.id === selectedId.value) ?? rows.value[0] ?? null)

const runtimeLabel = computed(() => ({
  idle: '尚未扫描',
  queued: '等待任务',
  running: '正在扫描',
  completed: '扫描完成',
  failed: '扫描失败'
}[guard.overview.status]))

// A pending run and its not-yet-dispatched outbox event describe the same work.
const queuedCount = computed(() => Math.max(guard.overview.queued, guard.overview.outboxPending))

const activityText = computed(() => {
  if (!guard.overview.latestActivityAt) return '暂无运行记录'
  const value = new Date(guard.overview.latestActivityAt)
  return Number.isNaN(value.getTime()) ? '已有运行记录' : value.toLocaleString('zh-CN', { hour12: false })
})

const timelineStatus = computed(() => {
  if (guard.timelineReflowError) return guard.timelineReflowError
  const result = guard.timelineReflowResult
  if (!result) return null
  if (result.claimsChanged === 0) return `时间线已是最新 · 检查 ${result.claimsExamined} 条事实`
  const blocked = result.ambiguous + result.cyclic
  return [
    `已更新 ${result.claimsChanged} 条事实，涉及 ${result.affectedChapterIds.length} 章`,
    `${result.resolved} 条依赖已确定`,
    result.unresolved ? `${result.unresolved} 条模糊时间待确认` : '',
    blocked ? `${blocked} 条歧义或循环待处理` : '',
    result.rescansQueued ? `${result.rescansQueued} 章正在复检` : ''
  ].filter(Boolean).join(' · ')
})

const activeTemporalReview = computed(() =>
  guard.temporalReviews.find((item) => item.claimId === selectedTemporalClaimId.value)
  ?? guard.temporalReviews[0]
  ?? null
)

const temporalReviewCounts = computed(() => ({
  pending: guard.temporalReviews.filter((item) => item.overrideSeconds == null).length,
  confirmed: guard.temporalReviews.filter((item) => item.overrideSeconds != null).length
}))

const reviewRangeDays = computed(() => {
  const item = activeTemporalReview.value
  return item
    ? { min: item.offsetMinSeconds / 86400, max: item.offsetMaxSeconds / 86400 }
    : { min: 0, max: 0 }
})

const temporalValueValid = computed(() =>
  Number.isFinite(selectedOffsetDays.value)
  && selectedOffsetDays.value >= reviewRangeDays.value.min
  && selectedOffsetDays.value <= reviewRangeDays.value.max
)

const guardGridRows = computed(() => [
  'auto',
  timelineStatus.value ? 'auto' : '',
  guard.temporalReviews.length ? 'auto' : '',
  'minmax(0, 1fr)'
].filter(Boolean).join(' '))

function formatOffset(seconds: number) {
  const sign = seconds < 0 ? '前' : '后'
  const absolute = Math.abs(seconds)
  const days = Math.floor(absolute / 86400)
  const hours = Math.round((absolute % 86400) / 3600)
  const units = [days ? `${days} 日` : '', hours ? `${hours} 时` : ''].filter(Boolean).join(' ')
  return absolute === 0 ? '同一时刻' : `${units || '不足一小时'}${sign}`
}

async function saveTemporalDecision(action: 'confirm' | 'clear') {
  const item = activeTemporalReview.value
  if (!item) return
  if (action === 'confirm' && !temporalValueValid.value) return
  await guard.decideTemporalReview(
    item,
    action,
    action === 'confirm' ? Math.round(selectedOffsetDays.value * 86400) : undefined
  )
}

const arbitrationCopy = computed(() => {
  if (!selected.value) return null
  const confidence = selected.value.arbitrationConfidence == null
    ? ''
    : ` · ${Math.round(selected.value.arbitrationConfidence * 100)}%`
  const copy = {
    not_requested: null,
    pending: { label: '模型复核中', detail: '规则告警已经生效，模型正在核对上下文。' },
    supported: { label: `模型支持该告警${confidence}`, detail: selected.value.arbitrationRationale || '现有证据支持这处冲突。' },
    unsupported: { label: `模型认为可能是误报${confidence}`, detail: selected.value.arbitrationRationale || '规则告警仍然保留，需要你最终确认。' },
    uncertain: { label: `模型无法确定${confidence}`, detail: selected.value.arbitrationRationale || '现有证据不足，请按正文和设定自行判断。' },
    failed: { label: '模型复核失败', detail: '规则告警仍然保留，不影响你继续处置。' }
  } as const
  return copy[selected.value.arbitrationStatus]
})

watch(rows, (list) => {
  if (!list.some((i) => i.id === selectedId.value)) selectedId.value = list[0]?.id ?? null
}, { immediate: true })

watch(activeTemporalReview, (item) => {
  if (!item) return
  selectedTemporalClaimId.value = item.claimId
  selectedOffsetDays.value = (item.overrideSeconds
    ?? (item.offsetMinSeconds + item.offsetMaxSeconds) / 2) / 86400
}, { immediate: true })

watch(
  [() => route.query.temporalClaim, () => guard.temporalReviews],
  ([value, reviews]) => {
    if (typeof value !== 'string') return
    const claimId = Number(value)
    if (reviews.some((item) => item.claimId === claimId)) selectedTemporalClaimId.value = claimId
  },
  { immediate: true }
)

function act(issue: GuardIssue, action: string, index: number) {
  if (action.includes('查看时间线')) {
    router.push(toProject('timeline'))
    return
  }
  if (action.includes('改写') || action.includes('回到正文') || action.includes('补一段')) {
    openIssueChapter(issue)
    return
  }

  // 更新设定、确认忽略等动作由 mock API 记录为已处置；正文修改必须由作者完成后再消警。
  void guard.resolve(issue.id, issue.actionCodes?.[index] ?? 'defer')
}

function openIssueChapter(issue: GuardIssue) {
  const indexes = [...issue.chapterRef.matchAll(/\d+/g)].map((match) => Number(match[0]))
  const chapter = [...indexes]
    .reverse()
    .map((index) => project.chapters.find((item) => item.index === index))
    .find(Boolean)

  router.push({
    path: toProject('write'),
    query: chapter ? { chapter: chapter.id } : undefined
  })
}
</script>

<template>
  <div class="guard-view" :style="{ display: 'grid', gridTemplateRows: guardGridRows, height: '100%', background: 'var(--canvas)' }">
    <!-- 顶部四个计数。数字大、标签小，扫一眼就知道要不要进来处理 -->
    <div
      class="guard-summary"
      :style="{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr) auto',
        gap: 'var(--rule)', background: 'var(--line-strong)',
        borderBottom: 'var(--rule) solid var(--line-strong)'
      }"
    >
      <button
        v-for="t in tabs"
        :key="t.key"
        type="button"
        class="guard-summary-item"
        :aria-selected="guard.tab === t.key"
        :style="{
          textAlign: 'left', border: 0, cursor: 'pointer', padding: 'var(--u3) var(--u4)',
          background: guard.tab === t.key ? 'var(--panel)' : 'var(--panel-sunken)',
          boxShadow: guard.tab === t.key ? 'inset 0 -2px 0 var(--alert)' : 'none'
        }"
        @click="guard.tab = t.key"
      >
        <span
          :style="{
            display: 'block', fontFamily: 'var(--font-mono)', fontSize: '30px', fontWeight: 700, lineHeight: 1,
            color: t.key === 'conflict' && guard.counts[t.key] ? 'var(--alert)' : 'var(--ink)'
          }"
        >{{ guard.counts[t.key] }}</span>
        <span :style="{ display: 'block', marginTop: '6px', fontSize: 'var(--fs)', fontWeight: 700 }">{{ t.label }}</span>
        <span :style="{ display: 'block', marginTop: '2px', fontSize: 'var(--fs-sm)', color: 'var(--ink-3)' }">{{ t.note }}</span>
      </button>

      <div class="guard-scan" :style="{ background: 'var(--panel-sunken)', padding: 'var(--u3) var(--u4)', display: 'grid', alignContent: 'center', gap: '6px' }">
        <span :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', whiteSpace: 'nowrap' }">
          {{ runtimeLabel }} · {{ activityText }}<br>
          {{ guard.overview.running }} 运行 / {{ queuedCount }} 排队 / {{ guard.overview.failed + guard.overview.outboxDeadLetter }} 失败
        </span>
        <div class="row" :style="{ gap: 'var(--u2)' }">
          <button class="wk-btn" type="button" :disabled="guard.scanRequestPending" @click="guard.rescan()">
            {{ guard.scanRequestPending ? '正在下发…' : guard.scanning ? '重新检查' : '扫描当前版本' }}
          </button>
          <button
            class="wk-btn"
            type="button"
            :disabled="guard.timelineReflowPending"
            title="重新计算跨章节相对时间"
            @click="guard.reflowTimeline()"
          >
            <AppIcon name="history" :size="14" />
            {{ guard.timelineReflowPending ? '重算中…' : '重算时间线' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="timelineStatus"
      role="status"
      aria-live="polite"
      :style="{
        minHeight: '36px', display: 'flex', alignItems: 'center', gap: 'var(--u2)',
        padding: '7px var(--u4)', borderBottom: 'var(--hair) solid var(--line)',
        background: guard.timelineReflowError ? 'var(--alert-soft)' : 'var(--panel)',
        color: guard.timelineReflowError ? 'var(--alert-ink)' : 'var(--ink-2)',
        fontSize: 'var(--fs-sm)'
      }"
    >
      <AppIcon name="history" :size="15" />
      <span>{{ timelineStatus }}</span>
    </div>

    <section v-if="guard.temporalReviews.length" class="temporal-review" aria-label="模糊时间确认">
      <div class="temporal-review-head">
        <span class="wk-label">时间校对</span>
        <span>未确认 {{ temporalReviewCounts.pending }} 条 · 已定点 {{ temporalReviewCounts.confirmed }} 条</span>
        <span v-if="guard.temporalDecisionError" class="temporal-review-error" role="alert">
          {{ guard.temporalDecisionError }}
        </span>
      </div>
      <div class="temporal-review-body">
        <div class="temporal-review-list" role="listbox" aria-label="待确认时间表达">
          <button
            v-for="item in guard.temporalReviews"
            :key="item.claimId"
            type="button"
            :aria-selected="item.claimId === activeTemporalReview?.claimId"
            @click="selectedTemporalClaimId = item.claimId"
          >
            <span>{{ item.chapterIndex ? `第 ${item.chapterIndex} 章` : '全书设定' }}</span>
            <strong>{{ item.original }}</strong>
            <small>{{ item.overrideSeconds == null ? '待确认' : '已定点' }}</small>
          </button>
        </div>

        <div v-if="activeTemporalReview" class="temporal-review-editor">
          <div class="temporal-review-copy">
            <strong>{{ activeTemporalReview.eventRef || activeTemporalReview.original }}</strong>
            <span>
              相对“{{ activeTemporalReview.relationRef || '参照事件' }}” ·
              {{ formatOffset(activeTemporalReview.offsetMinSeconds) }} 至
              {{ formatOffset(activeTemporalReview.offsetMaxSeconds) }}
            </span>
          </div>
          <div class="temporal-scale">
            <span>{{ formatOffset(activeTemporalReview.offsetMinSeconds) }}</span>
            <input
              v-model.number="selectedOffsetDays"
              type="range"
              :min="reviewRangeDays.min"
              :max="reviewRangeDays.max"
              :step="1 / 24"
              aria-label="在解析范围内选择准确时间"
            >
            <span>{{ formatOffset(activeTemporalReview.offsetMaxSeconds) }}</span>
          </div>
          <div class="temporal-review-actions">
            <label>
              <span>确认偏移</span>
              <input
                v-model.number="selectedOffsetDays"
                type="number"
                :min="reviewRangeDays.min"
                :max="reviewRangeDays.max"
                :step="1 / 24"
              >
              <span>天</span>
            </label>
            <span class="temporal-picked">{{ formatOffset(selectedOffsetDays * 86400) }}</span>
            <button
              class="wk-btn"
              type="button"
              :disabled="!temporalValueValid || guard.temporalDecisionPendingId !== null"
              data-primary="true"
              @click="saveTemporalDecision('confirm')"
            >
              <AppIcon name="check" :size="14" />
              {{ guard.temporalDecisionPendingId === activeTemporalReview.claimId ? '保存中…' : '确认时间' }}
            </button>
            <button
              v-if="activeTemporalReview.overrideSeconds != null"
              class="wk-btn"
              type="button"
              :disabled="guard.temporalDecisionPendingId !== null"
              @click="saveTemporalDecision('clear')"
            >撤销定点</button>
          </div>
        </div>
      </div>
    </section>

    <div class="wk-cols guard-workspace" :style="{ gridTemplateColumns: '340px minmax(0, 1fr)' }">
      <!-- 告警列表 -->
      <div class="wk-pane" aria-label="告警列表">
        <div class="wk-head">
          <span>{{ tabs.find((t) => t.key === guard.tab)?.label }}</span>
          <span class="wk-head-push">{{ rows.length }} 条</span>
        </div>

        <button
          v-for="i in rows"
          :key="i.id"
          class="wk-row"
          type="button"
          role="option"
          :aria-selected="i.id === selected?.id"
          :style="{ minHeight: 'auto', padding: '7px var(--u3)', display: 'grid', gap: '3px', alignItems: 'start' }"
          @click="selectedId = i.id"
        >
          <span class="row" :style="{ gap: '6px', width: '100%' }">
            <span class="pill" :class="i.severity === 'high' ? 'pill-alert' : ''">
              {{ i.severity === 'high' ? '高' : '中' }}
            </span>
            <span :style="{ fontWeight: 700, color: 'var(--ink)' }">{{ i.category }}</span>
            <span class="wk-row-meta" :style="{ marginLeft: 'auto' }">{{ i.chapterRef }}</span>
          </span>
          <span :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-2)', lineHeight: 1.55, whiteSpace: 'normal' }">
            {{ i.title }}
          </span>
        </button>

        <p v-if="!rows.length" :style="{ padding: 'var(--u5) var(--u3)', color: 'var(--ink-3)', lineHeight: 1.7 }">
          这一类没有待处理项。<br>
          {{ guard.tab === 'conflict' ? '全书前后一致，继续写。' : '' }}
        </p>
      </div>

      <!-- 证据与处置 -->
      <main class="wk-pane wk-pane-paper guard-detail" aria-label="告警详情">
        <template v-if="selected">
          <div class="paper-bar">
            <span class="pill" :class="selected.severity === 'high' ? 'pill-alert' : ''">
              {{ selected.severity === 'high' ? '高' : '中' }}
            </span>
            <span :style="{ fontWeight: 700, color: 'var(--ink)', fontSize: 'var(--fs)' }">{{ selected.category }}</span>
            <span :style="{ color: 'var(--ink-3)' }">{{ selected.chapterRef }}</span>
            <span :style="{ marginLeft: 'auto' }" />
            <button class="wk-btn wk-btn-xs" type="button" @click="openIssueChapter(selected)">打开对应章节</button>
          </div>

          <div :style="{ maxWidth: '820px', padding: 'var(--u6)' }">
            <h2 :style="{ fontSize: 'var(--fs-xl)', fontWeight: 700, margin: '0 0 var(--u3)', lineHeight: 1.45 }">
              {{ selected.title }}
            </h2>
            <p
              v-if="selected.detail"
              :style="{ margin: '0 0 var(--u5)', fontSize: 'var(--fs-md)', lineHeight: 1.9, color: 'var(--ink-2)' }"
            >{{ selected.detail }}</p>

            <!-- 两处原文对照。本次命中用红边，被违反的旧文/设定用灰边 -->
            <template v-if="selected.evidence.length">
              <div class="wk-label" :style="{ marginBottom: 'var(--u3)' }">证据对照</div>
              <div
                v-for="(ev, n) in selected.evidence"
                :key="n"
                :style="{
                  borderLeft: '3px solid ' + (ev.accent ? 'var(--alert)' : 'var(--line-strong)'),
                  background: ev.accent ? 'var(--alert-soft)' : 'var(--panel-sunken)',
                  padding: 'var(--u3) var(--u4)', marginBottom: 'var(--u3)'
                }"
              >
                <div
                  :style="{
                    fontSize: 'var(--fs-xs)', fontWeight: 700, letterSpacing: '0.06em', marginBottom: '6px',
                    color: ev.accent ? 'var(--alert-ink)' : 'var(--ink-3)'
                  }"
                >{{ ev.label }}</div>
                <p :style="{ margin: 0, fontFamily: 'var(--font-prose)', fontSize: 'var(--fs-lg)', lineHeight: 1.95, color: 'var(--ink)' }">
                  {{ ev.text }}
                </p>
              </div>
            </template>

            <div
              v-if="arbitrationCopy"
              :style="{
                marginTop: 'var(--u4)', padding: 'var(--u3) var(--u4)',
                borderLeft: '3px solid var(--line-strong)', background: 'var(--panel-sunken)'
              }"
              aria-live="polite"
            >
              <div :style="{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--ink)' }">
                {{ arbitrationCopy.label }}
              </div>
              <p :style="{ margin: '4px 0 0', fontSize: 'var(--fs-sm)', lineHeight: 1.7, color: 'var(--ink-2)' }">
                {{ arbitrationCopy.detail }}
              </p>
            </div>

            <!-- 处置。第一项是推荐动作，误报单独一条，用于持续调准 -->
            <div :style="{ marginTop: 'var(--u6)', paddingTop: 'var(--u4)', borderTop: 'var(--hair) solid var(--line)' }">
              <div class="wk-label" :style="{ marginBottom: 'var(--u3)' }">处置</div>
              <div class="row" :style="{ flexWrap: 'wrap', gap: 'var(--u2)' }">
                <button
                  v-for="(a, n) in selected.actions"
                  :key="a"
                  class="wk-btn"
                  type="button"
                  :data-primary="n === 0"
                  @click="act(selected, a, n)"
                >{{ a }}</button>
                <button
                  class="wk-btn"
                  type="button"
                  :style="{ marginLeft: 'auto', borderColor: 'transparent', color: 'var(--ink-3)' }"
                  @click="guard.resolve(selected.id, 'false_positive')"
                >这是误报</button>
              </div>
              <p :style="{ margin: 'var(--u3) 0 0', fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', lineHeight: 1.7 }">
                处置结果会回写设定库。标记误报不会改动正文，只用于持续调准扫描规则。
              </p>
            </div>
          </div>
        </template>

        <p v-else :style="{ padding: 'var(--u6)', color: 'var(--ink-3)', lineHeight: 1.8 }">
          没有需要处理的告警。<br>
          守卫会在你每写完一章后增量扫描，发现问题再回到这里。
        </p>
      </main>
    </div>
  </div>
</template>

<style scoped>
.temporal-review {
  border-bottom: var(--rule) solid var(--line-strong);
  background: var(--panel);
}

.temporal-review-head {
  min-height: 34px;
  display: flex;
  align-items: center;
  gap: var(--u3);
  padding: 6px var(--u4);
  border-bottom: var(--hair) solid var(--line);
  color: var(--ink-3);
  font-size: var(--fs-sm);
}

.temporal-review-error { margin-left: auto; color: var(--alert-ink); }
.temporal-review-body { display: grid; grid-template-columns: 230px minmax(0, 1fr); min-height: 126px; }
.temporal-review-list { border-right: var(--hair) solid var(--line-strong); overflow: auto; max-height: 164px; }
.temporal-review-list button {
  width: 100%; min-height: 42px; display: grid; grid-template-columns: auto 1fr auto; align-items: center;
  gap: var(--u2); padding: 7px var(--u3); border: 0; border-bottom: var(--hair) solid var(--line);
  background: transparent; color: var(--ink-2); text-align: left; cursor: pointer;
}
.temporal-review-list button[aria-selected="true"] { background: var(--panel-sunken); box-shadow: inset 2px 0 var(--alert); }
.temporal-review-list strong { color: var(--ink); font-size: var(--fs-sm); overflow: hidden; text-overflow: ellipsis; }
.temporal-review-list small { color: var(--ink-3); }
.temporal-review-editor { padding: var(--u3) var(--u4); display: grid; align-content: center; gap: var(--u3); }
.temporal-review-copy { display: flex; align-items: baseline; gap: var(--u3); }
.temporal-review-copy strong { color: var(--ink); }
.temporal-review-copy span { color: var(--ink-3); font-size: var(--fs-sm); }
.temporal-scale { display: grid; grid-template-columns: 78px minmax(160px, 1fr) 78px; align-items: center; gap: var(--u2); }
.temporal-scale span { color: var(--ink-3); font-family: var(--font-mono); font-size: var(--fs-xs); white-space: nowrap; }
.temporal-scale span:last-child { text-align: right; }
.temporal-scale input { width: 100%; accent-color: var(--alert); }
.temporal-review-actions { display: flex; align-items: center; gap: var(--u2); }
.temporal-review-actions label { display: flex; align-items: center; gap: 6px; color: var(--ink-3); font-size: var(--fs-sm); }
.temporal-review-actions input[type="number"] { width: 88px; min-height: 30px; border: var(--hair) solid var(--line-strong); background: var(--canvas); color: var(--ink); padding: 4px 7px; }
.temporal-picked { margin-right: auto; color: var(--ink-2); font-family: var(--font-mono); font-size: var(--fs-sm); }

@media (max-width: 760px) {
  .temporal-review-head { flex-wrap: wrap; }
  .temporal-review-error { width: 100%; margin-left: 0; }
  .temporal-review-body { grid-template-columns: 1fr; }
  .temporal-review-list { display: flex; max-height: none; overflow-x: auto; border-right: 0; border-bottom: var(--hair) solid var(--line-strong); }
  .temporal-review-list button { min-width: 210px; border-right: var(--hair) solid var(--line); border-bottom: 0; }
  .temporal-review-copy { align-items: flex-start; flex-direction: column; gap: 3px; }
  .temporal-scale { grid-template-columns: 64px minmax(120px, 1fr) 64px; }
  .temporal-review-actions { flex-wrap: wrap; }
  .temporal-picked { width: 100%; order: 3; }
}
</style>
