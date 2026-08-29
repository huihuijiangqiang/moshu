<script setup lang="ts">
import { onMounted } from 'vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import { useGuardStore } from '@/stores/guard'
import { useProjectStore } from '@/stores/project'
import type { GuardKind } from '@/types'

const guard = useGuardStore()
const store = useProjectStore()

const tabs: { key: GuardKind | 'resolved'; label: string }[] = [
  { key: 'conflict', label: '冲突' },
  { key: 'foreshadow', label: '伏笔' },
  { key: 'pending-entry', label: '待确认' },
  { key: 'resolved', label: '已处置' }
]

onMounted(() => guard.load())
</script>

<template>
  <div class="app">
    <AppHeader subtitle="一致性守卫">
      <template #actions>
        <span class="muted">上次全量扫描 · {{ store.chapters.length }} 章 / {{ (store.totalWords / 10000).toFixed(1) }} 万字</span>
        <button class="btn btn-secondary" type="button" :disabled="guard.scanning" :style="{ height: '30px', fontSize: '12px' }" @click="guard.rescan()">
          {{ guard.scanning ? '扫描中…' : '重新全量扫描' }}
        </button>
      </template>
    </AppHeader>

    <div class="pane" :style="{ flex: 1 }">
      <div class="grid-rule rule-b" :style="{ gridTemplateColumns: 'repeat(4, 1fr)' }">
        <div v-for="t in tabs" :key="t.key" :style="{ padding: '24px 22px', background: 'var(--color-bg)' }">
          <div class="num" :style="{ fontSize: '40px', color: t.key === 'conflict' ? 'var(--color-accent)' : 'inherit' }">
            {{ guard.counts[t.key] }}
          </div>
          <div :style="{ marginTop: '8px', fontWeight: 700, fontSize: '13px' }">{{ t.label }}</div>
        </div>
      </div>

      <div :style="{ padding: '26px 22px' }">
        <div class="row" :style="{ gap: '16px', marginBottom: '22px', fontSize: '13px' }">
          <button
            v-for="t in tabs"
            :key="t.key"
            type="button"
            :style="{
              border: 0, background: 'none', cursor: 'pointer', paddingBottom: '4px',
              borderBottom: guard.tab === t.key ? '2px solid var(--color-accent)' : '2px solid transparent',
              fontWeight: guard.tab === t.key ? 700 : 400,
              color: guard.tab === t.key ? 'var(--color-text)' : 'var(--color-neutral-700)'
            }"
            @click="guard.tab = t.key"
          >{{ t.label }} {{ guard.counts[t.key] }}</button>
        </div>

        <div class="grid-rule">
          <article
            v-for="i in guard.visible"
            :key="i.id"
            :style="{ padding: '26px 24px', display: 'grid', gridTemplateColumns: '130px 1fr 250px', gap: '32px', alignItems: 'start', fontSize: '13px' }"
          >
            <div>
              <span class="tag" :class="i.severity === 'high' ? 'tag-accent' : 'tag-neutral'" :style="{ fontSize: '11px' }">
                {{ i.severity === 'high' ? '高' : '中' }} · {{ i.category }}
              </span>
              <div class="muted" :style="{ marginTop: '10px', lineHeight: 1.6 }">{{ i.chapterRef }}</div>
            </div>

            <div>
              <h3 :style="{ fontSize: '18px', fontWeight: 700, margin: '0 0 12px' }">{{ i.title }}</h3>
              <p v-if="i.detail" :style="{ margin: '0 0 12px', lineHeight: 1.75, color: 'var(--color-neutral-800)' }">{{ i.detail }}</p>
              <div
                v-for="(ev, n) in i.evidence"
                :key="n"
                :style="{
                  borderLeft: '2px solid ' + (ev.accent ? 'var(--color-accent)' : 'var(--color-neutral-400)'),
                  paddingLeft: '14px', marginBottom: '12px', lineHeight: 1.75
                }"
              >
                <div :style="{ fontSize: '12px', marginBottom: '4px', fontWeight: ev.accent ? 700 : 400, color: ev.accent ? 'var(--color-accent-700)' : 'var(--color-neutral-700)' }">
                  {{ ev.label }}
                </div>
                {{ ev.text }}
              </div>
            </div>

            <div :style="{ display: 'grid', gap: '8px' }">
              <button
                v-for="(a, n) in i.actions"
                :key="a"
                type="button"
                class="chip"
                :class="n === 0 ? 'chip-strong' : ''"
                :style="{ textAlign: 'left', padding: '6px 12px' }"
                @click="guard.resolve(i.id)"
              >{{ a }}</button>
              <button class="chip chip-ghost" type="button" :style="{ textAlign: 'left' }" @click="guard.resolve(i.id)">这是误报</button>
            </div>
          </article>
        </div>
        <p v-if="!guard.visible.length" class="muted" :style="{ padding: '40px 0' }">这一类没有待处理项。</p>
      </div>
    </div>
  </div>
</template>
