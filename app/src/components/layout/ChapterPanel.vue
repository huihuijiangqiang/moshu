<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useProjectStore } from '@/stores/project'
import type { Chapter } from '@/types'

const GROUP_HEIGHT = 24
const ROW_HEIGHT = 30
const OVERSCAN = 180
const DEFAULT_VIEWPORT_HEIGHT = 480

type LayoutItem =
  | { kind: 'group'; key: string; top: number; height: number; volumeIndex: number; title: string; total: number }
  | { kind: 'chapter'; key: string; top: number; height: number; position: number; chapter: Chapter }

/** 章节栏只负责作品结构导航；长篇按固定行高只挂载视口附近的节点。 */
const project = useProjectStore()
const props = defineProps<{ createChapterAction?: () => void }>()
const filter = ref('')
const viewport = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const viewportHeight = ref(DEFAULT_VIEWPORT_HEIGHT)
const focusIndex = ref(-1)
const emit = defineEmits<{ pick: []; 'create-chapter': [] }>()
let resizeObserver: ResizeObserver | null = null

const groups = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return project.byVolume
    .map((group) => ({
      volume: group.volume,
      total: group.chapters.length,
      chapters: q
        ? group.chapters.filter(
            (chapter) => chapter.title.toLowerCase().includes(q) || String(chapter.index).includes(q)
          )
        : group.chapters
    }))
    .filter((group) => group.chapters.length > 0)
})

const hits = computed(() => groups.value.reduce((sum, group) => sum + group.chapters.length, 0))
const filteredChapters = computed(() => groups.value.flatMap((group) => group.chapters))

const layoutItems = computed<LayoutItem[]>(() => {
  const items: LayoutItem[] = []
  let top = 0
  let position = 0
  for (const group of groups.value) {
    items.push({
      kind: 'group',
      key: `volume-${group.volume.id}`,
      top,
      height: GROUP_HEIGHT,
      volumeIndex: group.volume.index,
      title: group.volume.title,
      total: group.total
    })
    top += GROUP_HEIGHT
    for (const chapter of group.chapters) {
      position += 1
      items.push({ kind: 'chapter', key: chapter.id, top, height: ROW_HEIGHT, position, chapter })
      top += ROW_HEIGHT
    }
  }
  return items
})

const totalHeight = computed(() => {
  const last = layoutItems.value.at(-1)
  return last ? last.top + last.height : 0
})

const visibleItems = computed(() => {
  const start = Math.max(0, scrollTop.value - OVERSCAN)
  const end = scrollTop.value + viewportHeight.value + OVERSCAN
  return layoutItems.value.filter((item) => item.top + item.height >= start && item.top <= end)
})

const tabStopId = computed(() => {
  const intendedId = focusIndex.value >= 0 ? filteredChapters.value[focusIndex.value]?.id : project.activeId
  const visibleChapters = visibleItems.value.filter(
    (item): item is Extract<LayoutItem, { kind: 'chapter' }> => item.kind === 'chapter'
  )
  return visibleChapters.some((item) => item.chapter.id === intendedId)
    ? intendedId
    : visibleChapters[0]?.chapter.id
})

const stickyGroup = computed(() => {
  let current: Extract<LayoutItem, { kind: 'group' }> | null = null
  for (const item of layoutItems.value) {
    if (item.kind !== 'group') continue
    if (item.top > scrollTop.value) break
    current = item
  }
  return current
})

function measureViewport() {
  if (viewport.value?.clientHeight) viewportHeight.value = viewport.value.clientHeight
}

function handleScroll() {
  scrollTop.value = viewport.value?.scrollTop ?? 0
}

function chapterTop(id: string) {
  const item = layoutItems.value.find(
    (candidate): candidate is Extract<LayoutItem, { kind: 'chapter' }> =>
      candidate.kind === 'chapter' && candidate.chapter.id === id
  )
  return item?.top
}

function revealChapter(id: string) {
  const element = viewport.value
  const top = chapterTop(id)
  if (!element || top === undefined) return
  const visibleHeight = element.clientHeight || viewportHeight.value
  const visibleTop = element.scrollTop + GROUP_HEIGHT
  const visibleBottom = element.scrollTop + visibleHeight
  if (top < visibleTop) element.scrollTop = Math.max(0, top - GROUP_HEIGHT)
  else if (top + ROW_HEIGHT > visibleBottom) element.scrollTop = top + ROW_HEIGHT - visibleHeight
  handleScroll()
}

async function focusChapter(index: number) {
  const chapters = filteredChapters.value
  if (!chapters.length) return
  focusIndex.value = Math.max(0, Math.min(index, chapters.length - 1))
  const chapter = chapters[focusIndex.value]
  if (!chapter) return
  revealChapter(chapter.id)
  await nextTick()
  viewport.value?.querySelector<HTMLElement>(`[data-chapter-id="${CSS.escape(chapter.id)}"]`)?.focus()
}

function open(chapter: Chapter) {
  focusIndex.value = filteredChapters.value.findIndex((candidate) => candidate.id === chapter.id)
  void project.openChapter(chapter.id)
  emit('pick')
}

function handleChapterKeydown(event: KeyboardEvent, chapter: Chapter) {
  const currentIndex = filteredChapters.value.findIndex((candidate) => candidate.id === chapter.id)
  if (currentIndex < 0) return
  let nextIndex: number | null = null
  if (event.key === 'ArrowDown') nextIndex = currentIndex + 1
  else if (event.key === 'ArrowUp') nextIndex = currentIndex - 1
  else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = filteredChapters.value.length - 1
  else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    open(chapter)
    return
  }
  if (nextIndex === null) return
  event.preventDefault()
  void focusChapter(nextIndex)
}

watch(filter, () => {
  focusIndex.value = filteredChapters.value.findIndex((chapter) => chapter.id === project.activeId)
  if (viewport.value) viewport.value.scrollTop = 0
  scrollTop.value = 0
})

watch(
  () => project.activeId,
  async (id) => {
    if (!id) return
    focusIndex.value = filteredChapters.value.findIndex((chapter) => chapter.id === id)
    await nextTick()
    revealChapter(id)
  }
)

onMounted(async () => {
  await nextTick()
  measureViewport()
  if (typeof ResizeObserver !== 'undefined' && viewport.value) {
    resizeObserver = new ResizeObserver(measureViewport)
    resizeObserver.observe(viewport.value)
  } else {
    window.addEventListener('resize', measureViewport)
  }
  if (project.activeId) {
    focusIndex.value = filteredChapters.value.findIndex((chapter) => chapter.id === project.activeId)
    revealChapter(project.activeId)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  window.removeEventListener('resize', measureViewport)
})
</script>

<template>
  <div class="chapter-panel">
    <div class="wk-head">
      <span>章节</span>
      <span class="wk-head-push">{{ filter ? `${hits} / ` : '' }}{{ project.chapters.length }}</span>
      <button class="wk-btn wk-btn-xs" type="button" title="新建章节" aria-label="新建章节" @click="props.createChapterAction?.(); emit('create-chapter')">＋</button>
    </div>

    <div class="chapter-filter">
      <input v-model="filter" class="wk-input" placeholder="筛选章号或标题" aria-label="筛选章节">
    </div>

    <div v-if="groups.length" class="chapter-list-shell">
      <div v-if="stickyGroup" class="wk-group chapter-sticky-group" aria-hidden="true">
        <span>第{{ stickyGroup.volumeIndex }}卷 · {{ stickyGroup.title }}</span>
        <span class="chapter-group-total">{{ stickyGroup.total }} 章</span>
      </div>

      <div
        ref="viewport"
        class="chapter-viewport"
        role="listbox"
        aria-label="章节列表"
        @scroll="handleScroll"
      >
        <div class="chapter-virtual-canvas" role="presentation" :style="{ height: `${totalHeight}px` }">
          <template v-for="item in visibleItems" :key="item.key">
            <div
              v-if="item.kind === 'group'"
              class="wk-group chapter-virtual-item"
              :style="{ transform: `translateY(${item.top}px)` }"
              role="presentation"
            >
              <span>第{{ item.volumeIndex }}卷 · {{ item.title }}</span>
              <span class="chapter-group-total">{{ item.total }} 章</span>
            </div>

            <button
              v-else
              :id="`chapter-option-${item.chapter.id}`"
              class="wk-row chapter-virtual-item"
              type="button"
              role="option"
              :data-chapter-id="item.chapter.id"
              :style="{ transform: `translateY(${item.top}px)` }"
              :aria-selected="item.chapter.id === project.activeId"
              :aria-posinset="item.position"
              :aria-setsize="filteredChapters.length"
              :tabindex="tabStopId === item.chapter.id ? 0 : -1"
              @click="open(item.chapter)"
              @focus="focusIndex = filteredChapters.findIndex((chapter) => chapter.id === item.chapter.id)"
              @keydown="handleChapterKeydown($event, item.chapter)"
            >
              <span class="wk-row-num">{{ String(item.chapter.index).padStart(3, '0') }}</span>
              <span class="wk-row-name">{{ item.chapter.title || '未命名' }}</span>
              <span v-if="item.chapter.status === 'drafting'" class="pill pill-soft">在写</span>
              <span v-else-if="item.chapter.status === 'outlined'" class="wk-row-meta">章纲 {{ item.chapter.outline.length }}</span>
              <span v-else class="wk-row-meta">{{ (item.chapter.words / 1000).toFixed(1) }}k</span>
            </button>
          </template>
        </div>
      </div>
    </div>

    <p v-else class="chapter-empty">没有匹配「{{ filter }}」的章节。</p>
  </div>
</template>

<style scoped>
.chapter-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.chapter-filter {
  flex: none;
  padding: var(--u2);
  border-bottom: var(--hair) solid var(--line);
}
.chapter-list-shell {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.chapter-viewport {
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.chapter-virtual-canvas { position: relative; width: 100%; }
.chapter-virtual-item {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
}
.chapter-sticky-group {
  position: absolute;
  inset: 0 0 auto;
  z-index: 3;
  box-shadow: 0 1px 0 var(--line);
  pointer-events: none;
}
.chapter-group-total { margin-left: auto; font-weight: 400; }
.chapter-empty {
  margin: 0;
  padding: var(--u5) var(--u3);
  color: var(--ink-3);
}
.wk-row:focus-visible {
  position: absolute;
  z-index: 2;
  outline: 2px solid var(--primary);
  outline-offset: -2px;
}
</style>
