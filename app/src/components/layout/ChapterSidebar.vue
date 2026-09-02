<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'

const store = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const route = useRoute()

const navStyle = {
  display: 'flex', justifyContent: 'space-between', padding: '9px 16px',
  fontSize: '13px', border: 0, color: 'var(--color-neutral-800)'
} as const
</script>

<template>
  <aside class="pane pane-left">
    <div class="row-between rule-b" :style="{ padding: '14px 16px' }">
      <span class="kicker">章节</span>
      <button class="chip chip-ghost" type="button" :style="{ color: 'var(--color-accent)', fontSize: '16px', padding: '0 4px' }">+</button>
    </div>

    <div :style="{ padding: '12px 0' }">
      <template v-for="group in store.byVolume" :key="group.volume.id">
        <div class="kicker rule-t" :style="{ padding: '10px 16px 8px', marginTop: '6px' }">
          第{{ group.volume.index }}卷 · {{ group.volume.title }} · {{ group.chapters.length }} 章
        </div>
        <button
          v-for="c in group.chapters"
          :key="c.id"
          class="side-item"
          type="button"
          :aria-current="c.id === store.activeId"
          @click="store.openChapter(c.id)"
        >
          <span>{{ String(c.index).padStart(3, '0') }}　{{ c.title || '未命名' }}</span>
          <span v-if="c.status === 'outlined'" :style="{ color: c.id === store.activeId ? '#fff' : 'var(--color-accent-700)', fontSize: '11px', fontWeight: 700 }">
            有章纲
          </span>
          <span v-else :style="{ opacity: 0.65 }">{{ (c.words / 1000).toFixed(1) }}k</span>
        </button>
      </template>
    </div>

    <nav class="rule-t" :style="{ padding: '14px 0', display: 'grid' }">
      <RouterLink to="/write" :style="navStyle" :aria-current="route.path === '/write'">正文</RouterLink>
      <RouterLink to="/outline" :style="navStyle" :aria-current="route.path === '/outline'">大纲</RouterLink>
      <RouterLink to="/codex" :style="navStyle" :aria-current="route.path === '/codex'">
        <span>设定库</span><span class="muted">{{ codex.entries.length }}</span>
      </RouterLink>
      <RouterLink to="/guard" :style="navStyle" :aria-current="route.path === '/guard'">
        <span>一致性守卫</span>
        <span v-if="guard.open.length" class="badge">{{ guard.open.length }}</span>
      </RouterLink>
      <RouterLink to="/style" :style="navStyle" :aria-current="route.path === '/style'">风格档</RouterLink>
      <RouterLink to="/ai-ratio" :style="navStyle" :aria-current="route.path === '/ai-ratio'">AI 来源账本</RouterLink>
      <RouterLink to="/export" :style="navStyle" :aria-current="route.path === '/export'">导出</RouterLink>
    </nav>
  </aside>
</template>
