<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { mockApi } from '@/api/mock'
import { useProjectStore } from '@/stores/project'
import { useGuardStore } from '@/stores/guard'
import { useUsageStore } from '@/stores/usage'
import { useStylesStore } from '@/stores/styles'
import type { ContextLayer } from '@/types'

const emit = defineEmits<{ generate: [] }>()

const store = useProjectStore()
const guard = useGuardStore()
const usage = useUsageStore()
const styles = useStylesStore()
const layers = ref<ContextLayer[]>([])
const tab = ref<'ai' | 'refs' | 'notes'>('ai')

onMounted(async () => {
  const [context] = await Promise.all([mockApi.getContextLayers(), styles.load()])
  layers.value = context
})

const total = computed(() => layers.value.reduce((s, l) => s + l.tokens, 0))
const fmt = (n: number) => (n / 1000).toFixed(1) + 'k'
const styleName = computed(() => {
  const id = store.project?.styleProfile
  return id ? styles.byId.get(id)?.name ?? '已绑定风格档' : '未设置'
})
</script>

<template>
  <aside class="pane pane-right">
    <div class="rule-b" :style="{ display: 'flex', height: '42px' }">
      <button
        v-for="t in (['ai', 'refs', 'notes'] as const)"
        :key="t"
        type="button"
        :style="{
          flex: 1, border: 0, cursor: 'pointer', fontSize: '13px',
          borderRight: t !== 'notes' ? '2px solid var(--color-divider)' : '0',
          background: tab === t ? 'var(--color-neutral-100)' : 'transparent',
          fontWeight: tab === t ? 700 : 400,
          color: tab === t ? 'var(--color-text)' : 'var(--color-neutral-700)'
        }"
        @click="tab = t"
      >{{ t === 'ai' ? 'AI' : t === 'refs' ? '引用' : '笔记' }}</button>
    </div>

    <template v-if="tab === 'ai'">
      <section class="rule-b" :style="{ padding: '18px 16px' }">
        <div class="kicker" :style="{ marginBottom: '14px' }">本章章纲</div>
        <p :style="{ margin: '0 0 14px', fontSize: '13px', lineHeight: 1.75 }">
          {{ store.active?.outlineNote || '本章还没有章纲，先写一句你想发生什么。' }}
        </p>
        <button class="btn btn-primary" type="button" :style="{ width: '100%', height: '36px', fontSize: '13px' }" @click="emit('generate')">
          按章纲生成整章
        </button>
      </section>

      <section class="rule-b" :style="{ padding: '18px 16px' }">
        <div class="kicker" :style="{ marginBottom: '14px' }">上下文（已装配 {{ fmt(total) }} / 25k 上限）</div>
        <div :style="{ display: 'grid', gap: '9px', fontSize: '13px' }">
          <div v-for="l in layers" :key="l.key" class="row-between">
            <span :title="l.detail">{{ l.label }}</span>
            <span class="muted">{{ fmt(l.tokens) }}</span>
          </div>
          <div class="row-between rule-t" :style="{ paddingTop: '9px', fontWeight: 700 }">
            <span>风格档 · {{ styleName }}</span>
            <span :style="{ color: 'var(--color-accent-700)' }">{{ store.project?.styleProfile ? '已启用' : '未启用' }}</span>
          </div>
        </div>
      </section>

      <section class="rule-b" :style="{ padding: '18px 16px' }">
        <div class="kicker" :style="{ marginBottom: '14px', color: 'var(--color-accent)' }">
          守卫提醒 · {{ guard.open.length }}
        </div>
        <div :style="{ display: 'grid', gap: '12px', fontSize: '13px' }">
          <div
            v-for="i in guard.topThree"
            :key="i.id"
            :style="{
              borderLeft: '2px solid ' + (i.severity === 'high' ? 'var(--color-accent)' : 'var(--color-neutral-500)'),
              paddingLeft: '12px', lineHeight: 1.6
            }"
          >
            <strong>{{ i.category }}</strong><br>
            <span :style="{ color: 'var(--color-neutral-800)' }">{{ i.title }}</span>
          </div>
          <p v-if="!guard.open.length" class="muted" :style="{ margin: 0 }">暂无提醒。</p>
        </div>
      </section>

      <section :style="{ padding: '16px', fontSize: '13px' }" class="row-between">
        <span class="muted">本月积分</span>
        <span><strong>{{ usage.remaining.toLocaleString() }}</strong> <span class="muted">/ {{ usage.quota.toLocaleString() }}</span></span>
      </section>
    </template>

    <template v-else-if="tab === 'refs'">
      <section :style="{ padding: '18px 16px', fontSize: '13px' }">
        <div class="kicker" :style="{ marginBottom: '14px' }">本章引用的设定</div>
        <p class="muted" :style="{ margin: 0, lineHeight: 1.7 }">
          正文里以 <code>@</code> 插入的条目会出现在这里，并自动进入生成上下文。
        </p>
      </section>
    </template>

    <template v-else>
      <section :style="{ padding: '18px 16px', fontSize: '13px' }">
        <div class="kicker" :style="{ marginBottom: '14px' }">笔记</div>
        <p class="muted" :style="{ margin: 0, lineHeight: 1.7 }">只属于你的备忘，不进入 AI 上下文。</p>
      </section>
    </template>
  </aside>
</template>
