<script setup lang="ts">
import { computed, ref } from 'vue'
import { useProjectStore } from '@/stores/project'

/**
 * 章节栏：26px 密集行，卷标题 sticky。
 * 旧版把全局导航塞在这个滚动容器最底下，88 章之后——等于没有导航。
 * 现在导航在图标轨上，这里只管章节，一件事做一件事。
 */
const project = useProjectStore()
const filter = ref('')
const emit = defineEmits<{ pick: [] }>()

const groups = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return project.byVolume
    .map((g) => ({
      volume: g.volume,
      total: g.chapters.length,
      chapters: q
        ? g.chapters.filter(
            (c) => c.title.toLowerCase().includes(q) || String(c.index).includes(q)
          )
        : g.chapters
    }))
    .filter((g) => g.chapters.length > 0)
})

const hits = computed(() => groups.value.reduce((s, g) => s + g.chapters.length, 0))

function open(id: string) {
  project.openChapter(id)
  emit('pick')
}
</script>

<template>
  <div>
    <div class="wk-head">
      <span>章节</span>
      <span class="wk-head-push">{{ filter ? `${hits} / ` : '' }}{{ project.chapters.length }}</span>
      <button class="wk-btn wk-btn-xs" type="button" title="新建章节">＋</button>
    </div>

    <div :style="{ padding: 'var(--u2) var(--u2)', borderBottom: 'var(--hair) solid var(--line)' }">
      <input v-model="filter" class="wk-input" placeholder="筛选章号或标题" aria-label="筛选章节">
    </div>

    <template v-for="g in groups" :key="g.volume.id">
      <div class="wk-group">
        <span>第{{ g.volume.index }}卷 · {{ g.volume.title }}</span>
        <span :style="{ marginLeft: 'auto', fontWeight: 400 }">{{ g.total }} 章</span>
      </div>

      <button
        v-for="c in g.chapters"
        :key="c.id"
        class="wk-row"
        type="button"
        role="option"
        :aria-selected="c.id === project.activeId"
        @click="open(c.id)"
      >
        <span class="wk-row-num">{{ String(c.index).padStart(3, '0') }}</span>
        <span class="wk-row-name">{{ c.title || '未命名' }}</span>
        <span v-if="c.status === 'drafting'" class="pill pill-soft">在写</span>
        <span v-else-if="c.status === 'outlined'" class="wk-row-meta">章纲 {{ c.outline.length }}</span>
        <span v-else class="wk-row-meta">{{ (c.words / 1000).toFixed(1) }}k</span>
      </button>
    </template>

    <p v-if="!groups.length" :style="{ padding: 'var(--u5) var(--u3)', color: 'var(--ink-3)' }">
      没有匹配「{{ filter }}」的章节。
    </p>
  </div>
</template>
