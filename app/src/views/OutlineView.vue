<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppHeader from '@/components/layout/AppHeader.vue'
import { useProjectStore } from '@/stores/project'
import type { Chapter } from '@/types'

const router = useRouter()
const store = useProjectStore()
const view = ref<'grid' | 'list'>('grid')
const selectedId = ref<string | null>(null)

const currentVolume = computed(() => store.byVolume.at(-1) ?? null)
const selected = computed<Chapter | null>(
  () => currentVolume.value?.chapters.find((c) => c.id === selectedId.value) ?? currentVolume.value?.chapters.at(-2) ?? null
)

const pacing = [
  { title: '爽点间隔', body: '平均 4.2 章一次，健康区间内。', warn: false },
  { title: '连续铺垫过长', body: '第 78-84 章连续 7 章无冲突推进。', warn: true },
  { title: '伏笔密度', body: '本卷埋 3 收 1，末章需回收 2。', warn: false }
]

function cellStyle(c: Chapter) {
  const active = c.id === selected.value?.id
  return {
    padding: '14px 12px',
    minHeight: '92px',
    cursor: 'pointer',
    background: c.status === 'done' ? 'var(--color-neutral-200)' : 'var(--color-neutral-100)',
    boxShadow: active ? 'inset 0 0 0 2px var(--color-accent)' : 'none'
  }
}
</script>

<template>
  <div class="app">
    <AppHeader subtitle="大纲">
      <template #actions>
        <button
          v-for="v in (['grid', 'list'] as const)"
          :key="v"
          type="button"
          :style="{
            border: 0, background: 'none', cursor: 'pointer', fontSize: '13px', paddingBottom: '3px',
            borderBottom: view === v ? '2px solid var(--color-accent)' : '2px solid transparent',
            fontWeight: view === v ? 700 : 400,
            color: view === v ? 'var(--color-text)' : 'var(--color-neutral-700)'
          }"
          @click="view = v"
        >{{ v === 'grid' ? '网格' : '列表' }}</button>
        <button class="btn btn-primary" type="button" :style="{ height: '30px', fontSize: '12px' }">AI 续排后续章纲</button>
      </template>
    </AppHeader>

    <section class="rule-b" :style="{ padding: '26px 24px', flex: 'none' }">
      <div class="kicker" :style="{ marginBottom: '16px' }">故事内时间线</div>
      <div :style="{ display: 'flex', height: '44px', border: '2px solid var(--color-divider)', fontSize: '13px' }">
        <div :style="{ flex: 62, background: 'var(--color-neutral-200)', borderRight: '2px solid var(--color-divider)', padding: '0 12px', display: 'flex', alignItems: 'center', gap: '10px' }">
          <strong>第一卷</strong><span class="muted">三月 — 四月初 · 62 章</span>
        </div>
        <div :style="{ flex: 25, borderRight: '2px solid var(--color-divider)', padding: '0 12px', display: 'flex', alignItems: 'center', gap: '10px' }">
          <strong>第二卷</strong><span class="muted">四月 — ? · 25 章</span>
        </div>
        <button
          type="button"
          :style="{ flex: 13, border: 0, cursor: 'pointer', background: 'var(--color-accent-100)', color: 'var(--color-accent-700)', fontWeight: 700, textAlign: 'left', padding: '0 12px' }"
          @click="router.push('/guard')"
        >时间线冲突</button>
      </div>
      <p :style="{ margin: '12px 0 0', color: 'var(--color-neutral-800)', lineHeight: 1.6, fontSize: '13px' }">
        第 79 章交代「四月始融雪」，第 86 章「风雪走了三日」推算已到五月中。点击红段查看守卫详情。
      </p>
    </section>

    <div class="app-body" :style="{ gridTemplateColumns: '1fr 340px' }">
      <main class="pane" :style="{ padding: '24px', background: 'var(--color-neutral-100)' }">
        <div class="kicker" :style="{ marginBottom: '16px' }">
          第{{ currentVolume?.volume.index }}卷 · {{ currentVolume?.volume.title }} · {{ currentVolume?.chapters.length }} 章
        </div>

        <div v-if="view === 'grid'" class="grid-rule" :style="{ gridTemplateColumns: 'repeat(6, 1fr)' }">
          <button
            v-for="c in currentVolume?.chapters ?? []"
            :key="c.id"
            type="button"
            :style="{ ...cellStyle(c), border: 0, textAlign: 'left', fontSize: '13px' }"
            @click="selectedId = c.id"
          >
            <div :style="{ fontWeight: 700, color: c.status === 'drafting' ? 'var(--color-accent)' : 'inherit' }">
              {{ String(c.index).padStart(3, '0') }}
            </div>
            <div :style="{ marginTop: '6px', lineHeight: 1.5, color: c.title ? 'inherit' : 'var(--color-neutral-600)' }">
              {{ c.title || '未命名' }}
            </div>
            <div v-if="c.status === 'outlined'" :style="{ marginTop: '8px', fontSize: '11px', fontWeight: 700, color: 'var(--color-accent-700)' }">
              章纲 {{ c.outline.length }} 点
            </div>
            <div v-else class="muted" :style="{ marginTop: '8px', fontSize: '11px' }">
              {{ c.status === 'drafting' ? '在写 · ' : '' }}{{ (c.words / 1000).toFixed(1) }}k
            </div>
          </button>
          <div :style="{ padding: '14px 12px', border: '2px dashed var(--color-neutral-400)', color: 'var(--color-neutral-600)', fontSize: '13px', background: 'var(--color-bg)' }">
            <div :style="{ fontWeight: 700 }">+</div>
            <div :style="{ marginTop: '6px' }">插入新章</div>
          </div>
        </div>

        <div v-else class="grid-rule">
          <button
            v-for="c in currentVolume?.chapters ?? []"
            :key="c.id"
            type="button"
            class="row-between"
            :style="{ border: 0, padding: '14px 16px', fontSize: '13px', cursor: 'pointer', textAlign: 'left' }"
            @click="selectedId = c.id"
          >
            <span><strong>{{ String(c.index).padStart(3, '0') }}</strong>　{{ c.title || '未命名' }}</span>
            <span class="muted">{{ c.outlineNote || '无章纲' }}</span>
          </button>
        </div>

        <div class="rule-t" :style="{ marginTop: '32px', paddingTop: '24px' }">
          <div class="kicker" :style="{ marginBottom: '14px' }">卷节奏检查</div>
          <div class="grid-rule" :style="{ gridTemplateColumns: 'repeat(3, 1fr)' }">
            <div v-for="p in pacing" :key="p.title" :style="{ padding: '16px 14px', fontSize: '13px' }">
              <div :style="{ fontWeight: 700, marginBottom: '6px', color: p.warn ? 'var(--color-accent-700)' : 'inherit' }">{{ p.title }}</div>
              <div :style="{ color: 'var(--color-neutral-800)', lineHeight: 1.6 }">{{ p.body }}</div>
            </div>
          </div>
        </div>
      </main>

      <aside class="pane pane-right" :style="{ padding: '22px 20px', fontSize: '13px' }">
        <template v-if="selected">
          <div class="kicker" :style="{ marginBottom: '16px' }">第 {{ String(selected.index).padStart(3, '0') }} 章 · 章纲</div>
          <div :style="{ fontSize: '20px', fontWeight: 700, marginBottom: '18px' }">{{ selected.title || '未命名' }}</div>

          <div v-if="selected.outline.length" class="grid-rule" :style="{ marginBottom: '18px', background: 'var(--color-divider)' }">
            <div v-for="(o, i) in selected.outline" :key="i" class="row" :style="{ padding: '11px 0', gap: '10px', background: 'var(--color-bg)' }">
              <span class="muted">{{ i + 1 }}</span><span>{{ o }}</span>
            </div>
          </div>
          <p v-else class="muted" :style="{ margin: '0 0 18px', lineHeight: 1.7 }">这一章还没有章纲。</p>

          <div :style="{ display: 'grid', gap: '8px' }">
            <button class="btn btn-primary" type="button" :style="{ height: '36px', fontSize: '13px' }" @click="router.push('/write')">
              按此章纲生成正文
            </button>
            <button class="btn btn-secondary" type="button" :style="{ height: '36px', fontSize: '13px' }">AI 补全章纲节点</button>
          </div>

          <p class="rule-t" :style="{ marginTop: '20px', paddingTop: '16px', color: 'var(--color-neutral-800)', lineHeight: 1.7 }">
            本章会引用 <strong>8</strong> 条设定。生成前建议先确认待入库条目。
          </p>
        </template>
      </aside>
    </div>
  </div>
</template>
