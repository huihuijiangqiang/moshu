<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import type { SaveState } from '@/composables/use-autosave'

const props = defineProps<{ subtitle?: string; saveState?: SaveState; savedAt?: Date | null }>()
const store = useProjectStore()

const pct = computed(() => {
  const p = store.project
  if (!p) return 0
  return Math.min(100, Math.round((p.dailyWords / p.dailyGoal) * 100))
})

const saveLabel = computed(() => {
  switch (props.saveState) {
    case 'saving': return '保存中…'
    case 'dirty': return '未保存'
    case 'offline': return '离线 · 已存本地'
    case 'saved': return `已保存 · ${props.savedAt?.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) ?? ''}`
    default: return ''
  }
})
</script>

<template>
  <header class="row-between rule-b" :style="{ height: 'var(--app-header-h)', padding: '0 18px', flex: 'none' }">
    <div class="row" :style="{ gap: '22px' }">
      <RouterLink to="/workspace" class="app-header-brand" :style="{ fontWeight: 700, fontSize: '15px', letterSpacing: '0.06em', border: 0, color: 'var(--color-text)' }">
        <img src="/brand/moshu-icon.svg" alt="" />
        <span>墨枢</span>
      </RouterLink>
      <span :style="{ width: '2px', height: '20px', background: 'var(--color-divider)' }" />
      <span :style="{ fontWeight: 700, fontSize: '13px' }">{{ store.project?.title ?? '加载中' }}</span>
      <span v-if="subtitle" class="muted" :style="{ fontSize: '13px' }">{{ subtitle }}</span>
    </div>

    <div class="row" :style="{ gap: '16px', fontSize: '13px' }">
      <span class="muted">
        今日 {{ store.project?.dailyWords.toLocaleString() }} / {{ store.project?.dailyGoal.toLocaleString() }} 字
      </span>
      <span :style="{ width: '90px', height: '6px', background: 'var(--color-neutral-300)' }">
        <span :style="{ display: 'block', width: pct + '%', height: '100%', background: 'var(--color-accent)' }" />
      </span>
      <template v-if="saveLabel">
        <span :style="{ width: '2px', height: '20px', background: 'var(--color-divider)' }" />
        <span :class="saveState === 'offline' ? '' : 'muted'"
              :style="saveState === 'offline' ? { color: 'var(--color-accent-700)', fontWeight: 700 } : {}">
          {{ saveLabel }}
        </span>
      </template>
      <slot name="actions" />
    </div>
  </header>
</template>
