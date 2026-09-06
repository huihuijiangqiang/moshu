<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { contentApi } from '@/api/content'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import { useShellStore } from '@/stores/shell'
import type { TimelineBoard, TimelineBoardEvent, TimelineBoardLane } from '@/types'

type BoardMode = 'story' | 'chapter'

const router = useRouter()
const shell = useShellStore()
const { projectId, toProject } = useProjectNavigation()
const board = ref<TimelineBoard | null>(null)
const loading = ref(false)
const error = ref('')
const mode = ref<BoardMode>('story')
const laneFilter = ref('all')
let loadGeneration = 0

onMounted(() => shell.setCrumb('故事时间线'))

async function loadBoard() {
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const result = await contentApi.getTimelineBoard(projectId.value)
    if (generation !== loadGeneration) return
    board.value = result
    if (laneFilter.value !== 'all' && !result.lanes.some((lane) => lane.timelineId === laneFilter.value)) {
      laneFilter.value = 'all'
    }
  } catch {
    if (generation === loadGeneration) error.value = '时间线暂时无法读取，请稍后重试。'
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

watch(projectId, loadBoard, { immediate: true })

const visibleLanes = computed(() => {
  const lanes = board.value?.lanes ?? []
  return laneFilter.value === 'all' ? lanes : lanes.filter((lane) => lane.timelineId === laneFilter.value)
})

const chapterRange = computed(() => {
  const indexes = (board.value?.lanes ?? []).flatMap((lane) => lane.events.map((event) => event.chapterIndex))
  return { min: Math.min(...indexes, 1), max: Math.max(...indexes, 1) }
})

const activeRange = computed(() => mode.value === 'story'
  ? {
      min: board.value?.storyOrderMin ?? 0,
      max: board.value?.storyOrderMax ?? board.value?.storyOrderMin ?? 1
    }
  : chapterRange.value
)

const ticks = computed(() => {
  const { min, max } = activeRange.value
  const steps = max === min ? 1 : 4
  return Array.from({ length: steps + 1 }, (_, index) => {
    const value = min + ((max - min) * index) / steps
    return { value, left: `${(index / steps) * 100}%`, label: formatAxis(value) }
  })
})

function trackEvents(lane: TimelineBoardLane) {
  return [...(mode.value === 'story' ? lane.events.filter((event) => event.storyOrder != null) : lane.events)]
    .sort((left, right) => {
      const leftPosition = mode.value === 'story' ? (left.storyOrder ?? 0) : left.chapterIndex
      const rightPosition = mode.value === 'story' ? (right.storyOrder ?? 0) : right.chapterIndex
      return leftPosition - rightPosition || left.chapterIndex - right.chapterIndex || left.claimId - right.claimId
    })
}

function pendingEvents(lane: TimelineBoardLane) {
  return mode.value === 'story' ? lane.events.filter((event) => event.storyOrder == null) : []
}

function eventPosition(event: TimelineBoardEvent) {
  const { min, max } = activeRange.value
  const value = mode.value === 'story' ? (event.storyOrder ?? min) : event.chapterIndex
  const percent = max === min ? 50 : ((value - min) / (max - min)) * 100
  return `clamp(96px, ${Math.max(0, Math.min(100, percent))}%, calc(100% - 96px))`
}

function formatAxis(value: number) {
  if (mode.value === 'chapter') return `第 ${Math.round(value)} 章`
  if (Math.abs(value) > 100_000_000) {
    const date = new Date(value * 1000)
    if (!Number.isNaN(date.getTime())) return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
  }
  return `序 ${Number.isInteger(value) ? value : value.toFixed(1)}`
}

function statusLabel(status: TimelineBoardEvent['placementStatus']) {
  return {
    placed: '已定位',
    review: '待确认',
    ambiguous: '有歧义',
    cyclic: '循环依赖',
    unplaced: '未定位'
  }[status]
}

function openChapter(event: TimelineBoardEvent) {
  void router.push({ path: toProject('write'), query: { chapter: event.chapterId } })
}

function openReview(event: TimelineBoardEvent) {
  void router.push({ path: toProject('guard'), query: { temporalClaim: String(event.claimId) } })
}
</script>

<template>
  <main class="timeline-view">
    <Teleport to="#topbar-actions">
      <button class="topbar-btn" type="button" :disabled="loading" title="刷新故事时间线" @click="loadBoard">
        <AppIcon name="restore" :size="14" />
        <span>{{ loading ? '读取中' : '刷新' }}</span>
      </button>
    </Teleport>

    <header class="timeline-summary">
      <div class="timeline-intro">
        <span class="wk-label">全书事件坐标</span>
        <h1>故事时间线</h1>
        <p>故事发生顺序独立于章节编排。倒叙与支线在这里按真实先后展开。</p>
      </div>
      <dl class="timeline-counts" aria-label="时间线统计">
        <div><dt>事件</dt><dd>{{ board?.eventCount ?? 0 }}</dd></div>
        <div><dt>已定位</dt><dd>{{ board?.placedCount ?? 0 }}</dd></div>
        <div data-alert="true"><dt>待校对</dt><dd>{{ board?.unplacedCount ?? 0 }}</dd></div>
        <div><dt>剧情线</dt><dd>{{ board?.lanes.length ?? 0 }}</dd></div>
      </dl>
    </header>

    <div class="timeline-tools">
      <div class="timeline-segment" role="group" aria-label="排序方式">
        <button type="button" :aria-pressed="mode === 'story'" @click="mode = 'story'">故事顺序</button>
        <button type="button" :aria-pressed="mode === 'chapter'" @click="mode = 'chapter'">章节顺序</button>
      </div>
      <label class="timeline-filter">
        <span>显示线路</span>
        <select v-model="laneFilter">
          <option value="all">全部剧情线</option>
          <option v-for="lane in board?.lanes ?? []" :key="lane.timelineId" :value="lane.timelineId">
            {{ lane.label }} · {{ lane.eventCount }}
          </option>
        </select>
      </label>
      <span class="timeline-legend"><i />已定位 <i data-review="true" />待确认</span>
    </div>

    <div v-if="loading && !board" class="timeline-state" role="status">正在整理全书事件顺序…</div>
    <div v-else-if="error" class="timeline-state timeline-state-error" role="alert">
      <strong>时间线读取失败</strong>
      <span>{{ error }}</span>
      <button class="wk-btn" type="button" @click="loadBoard">重新读取</button>
    </div>
    <div v-else-if="!board?.eventCount" class="timeline-state">
      <strong>还没有可定位的故事事件</strong>
      <span>写完章节并运行一致性扫描后，时间事件会出现在这里。</span>
      <button class="wk-btn" type="button" @click="router.push(toProject('write'))">回到正文</button>
    </div>

    <section v-else class="timeline-board" aria-label="多剧情线时间板">
      <div class="timeline-scroll">
        <div class="timeline-ruler">
          <div class="lane-label ruler-label">{{ mode === 'story' ? '故事坐标' : '章节坐标' }}</div>
          <div class="ruler-track">
            <span v-for="tick in ticks" :key="tick.value" :style="{ left: tick.left }">{{ tick.label }}</span>
          </div>
        </div>

        <article v-for="lane in visibleLanes" :key="lane.timelineId" class="timeline-lane">
          <header class="lane-label">
            <strong>{{ lane.label }}</strong>
            <span>{{ lane.placedCount }} / {{ lane.eventCount }} 已定位</span>
          </header>
          <div class="lane-track">
            <span v-for="tick in ticks" :key="tick.value" class="track-tick" :style="{ left: tick.left }" />
            <span class="track-line" />
            <button
              v-for="(event, index) in trackEvents(lane)"
              :key="event.claimId"
              class="timeline-event"
              :class="{ 'timeline-event-review': event.placementStatus !== 'placed' }"
              :style="{ left: eventPosition(event), top: `${46 + (index % 3) * 60}px` }"
              type="button"
              :title="`打开第 ${event.chapterIndex} 章《${event.chapterTitle}》`"
              @click="openChapter(event)"
            >
              <span>{{ event.eventRef }}</span>
              <small>第 {{ event.chapterIndex }} 章 · {{ event.timeText || statusLabel(event.placementStatus) }}</small>
            </button>
          </div>
          <div v-if="pendingEvents(lane).length" class="lane-pending">
            <span class="lane-pending-title">待校对</span>
            <button
              v-for="event in pendingEvents(lane)"
              :key="event.claimId"
              type="button"
              :data-blocked="event.placementStatus === 'cyclic'"
              @click="openReview(event)"
            >
              <strong>{{ event.eventRef }}</strong>
              <span>{{ event.timeText || statusLabel(event.placementStatus) }} · 第 {{ event.chapterIndex }} 章</span>
              <AppIcon name="chevron" :size="13" />
            </button>
          </div>
        </article>
      </div>
    </section>
  </main>
</template>

<style scoped>
.timeline-view {
  width: 100%;
  height: 100%;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  color: var(--ink);
  background: var(--paper);
}

.timeline-summary {
  min-height: 142px;
  display: grid;
  grid-template-columns: minmax(300px, 1fr) auto;
  align-items: end;
  gap: 40px;
  padding: 24px clamp(24px, 4vw, 54px);
  border-bottom: var(--hair) solid var(--line-strong);
}

.timeline-intro h1 { margin: 7px 0 4px; font-family: var(--font-prose); font-size: 27px; font-weight: 600; }
.timeline-intro p { margin: 0; color: var(--ink-3); font-size: var(--fs-sm); }
.timeline-counts { display: grid; grid-template-columns: repeat(4, minmax(66px, 84px)); margin: 0; }
.timeline-counts div { padding: 2px 14px; border-left: var(--hair) solid var(--line); }
.timeline-counts dt { color: var(--ink-3); font-size: var(--fs-xs); }
.timeline-counts dd { margin: 4px 0 0; font-family: var(--font-mono); font-size: 24px; font-weight: 500; }
.timeline-counts [data-alert='true'] dd { color: var(--alert); }

.timeline-tools {
  min-height: 48px;
  display: flex;
  align-items: center;
  gap: var(--u4);
  padding: 7px clamp(24px, 4vw, 54px);
  border-bottom: var(--hair) solid var(--line);
  background: var(--panel-sunken);
}

.timeline-segment { display: inline-flex; border: var(--hair) solid var(--line-strong); border-radius: 5px; overflow: hidden; }
.timeline-segment button { min-height: 30px; padding: 0 12px; border: 0; border-right: var(--hair) solid var(--line-strong); color: var(--ink-3); background: var(--panel); font: inherit; cursor: pointer; }
.timeline-segment button:last-child { border-right: 0; }
.timeline-segment button[aria-pressed='true'] { color: var(--paper); background: var(--ink); }
.timeline-filter { display: flex; align-items: center; gap: var(--u2); color: var(--ink-3); font-size: var(--fs-sm); }
.timeline-filter select { height: 30px; min-width: 140px; padding: 0 28px 0 9px; border: var(--hair) solid var(--line-strong); border-radius: 4px; color: var(--ink); background: var(--panel); font: inherit; }
.timeline-legend { display: flex; align-items: center; gap: 6px; margin-left: auto; color: var(--ink-3); font-size: var(--fs-xs); }
.timeline-legend i { width: 8px; height: 8px; border: 2px solid var(--primary); background: var(--panel); transform: rotate(45deg); }
.timeline-legend i[data-review='true'] { margin-left: var(--u2); border-color: var(--alert); }

.timeline-board { min-width: 0; padding: 20px clamp(24px, 4vw, 54px) 54px; overflow-x: auto; }
.timeline-scroll { min-width: 820px; border-top: var(--hair) solid var(--line-strong); }
.timeline-ruler, .timeline-lane { display: grid; grid-template-columns: 154px minmax(650px, 1fr); }
.timeline-ruler { min-height: 42px; color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-xs); }
.lane-label { min-width: 0; padding: 15px 16px 12px 0; border-right: var(--hair) solid var(--line-strong); }
.ruler-label { padding-top: 13px; text-transform: uppercase; }
.ruler-track { position: relative; margin: 0 20px; }
.ruler-track span { position: absolute; bottom: 9px; transform: translateX(-50%); white-space: nowrap; }
.timeline-lane { border-top: var(--hair) solid var(--line); }
.timeline-lane:last-child { border-bottom: var(--hair) solid var(--line-strong); }
.timeline-lane > .lane-label strong { display: block; overflow: hidden; color: var(--ink); text-overflow: ellipsis; white-space: nowrap; }
.timeline-lane > .lane-label span { display: block; margin-top: 5px; color: var(--ink-4); font-size: var(--fs-xs); }
.lane-track { position: relative; min-height: 230px; margin: 0 20px; overflow: hidden; }
.track-line { position: absolute; top: 28px; right: 0; left: 0; height: 1px; background: var(--line-strong); }
.track-tick { position: absolute; top: 22px; bottom: 0; width: 1px; background: var(--line); }
.track-tick::before { content: ''; position: absolute; top: 2px; left: -3px; width: 7px; height: 7px; border: 1px solid var(--line-strong); background: var(--paper); transform: rotate(45deg); }

.timeline-event {
  position: absolute;
  z-index: 1;
  width: 184px;
  min-height: 46px;
  padding: 7px 9px;
  border: var(--hair) solid var(--primary-line);
  border-left: 3px solid var(--primary);
  border-radius: 4px;
  color: var(--ink);
  background: var(--panel);
  text-align: left;
  transform: translateX(-50%);
  cursor: pointer;
}
.timeline-event:hover, .timeline-event:focus-visible { border-color: var(--primary); outline: 2px solid var(--primary-soft); outline-offset: 1px; }
.timeline-event > span, .timeline-event small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.timeline-event > span { font-size: var(--fs-sm); font-weight: 700; }
.timeline-event small { margin-top: 3px; color: var(--ink-3); font-size: var(--fs-xs); }
.timeline-event-review { border-color: var(--alert-line); border-left-color: var(--alert); }

.lane-pending { grid-column: 2; display: flex; align-items: center; gap: var(--u2); min-width: 0; margin: -2px 20px 14px; padding-top: 10px; border-top: var(--hair) dashed var(--alert-line); overflow-x: auto; }
.lane-pending-title { flex: 0 0 auto; color: var(--alert-ink); font-size: var(--fs-xs); font-weight: 700; }
.lane-pending button { flex: 0 0 226px; min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) 14px; gap: 2px 6px; padding: 7px 9px; border: var(--hair) solid var(--alert-line); border-radius: 4px; color: var(--ink); background: var(--alert-soft); text-align: left; cursor: pointer; }
.lane-pending button strong, .lane-pending button span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lane-pending button strong { font-size: var(--fs-sm); }
.lane-pending button span { grid-column: 1; color: var(--alert-ink); font-size: var(--fs-xs); }
.lane-pending button svg { grid-column: 2; grid-row: 1 / 3; align-self: center; }

.timeline-state { min-height: 280px; display: grid; place-content: center; justify-items: center; gap: var(--u2); padding: var(--u6); color: var(--ink-3); text-align: center; }
.timeline-state strong { color: var(--ink); font-size: var(--fs-lg); }
.timeline-state-error strong { color: var(--alert-ink); }

@media (max-width: 900px) {
  .timeline-summary { grid-template-columns: 1fr; align-items: start; gap: var(--u5); }
  .timeline-counts { width: 100%; grid-template-columns: repeat(4, 1fr); }
  .timeline-counts div:first-child { border-left: 0; padding-left: 0; }
}

@media (max-width: 640px) {
  .timeline-summary { padding: 18px var(--u4); }
  .timeline-intro h1 { font-size: 23px; }
  .timeline-counts dd { font-size: 19px; }
  .timeline-tools { align-items: flex-start; flex-wrap: wrap; padding: 8px var(--u4); }
  .timeline-legend { width: 100%; margin-left: 0; }
  .timeline-board { padding: 14px var(--u4) 40px; }
}

@media (prefers-reduced-motion: reduce) {
  .timeline-event { scroll-behavior: auto; }
}
</style>
