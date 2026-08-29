<script setup lang="ts">
import { onMounted } from 'vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import ChapterSidebar from '@/components/layout/ChapterSidebar.vue'
import { useCodexStore } from '@/stores/codex'
import { CODEX_KIND_LABEL, type CodexKind } from '@/types'

const codex = useCodexStore()
const kinds = Object.keys(CODEX_KIND_LABEL) as CodexKind[]

onMounted(() => codex.load())
</script>

<template>
  <div class="app">
    <AppHeader subtitle="设定库">
      <template #actions>
        <input v-model="codex.query" class="input" placeholder="搜索条目、别名、关系……" :style="{ minWidth: '200px', height: '32px' }">
        <button class="btn btn-primary" type="button" :style="{ height: '32px', fontSize: '12px' }">新建条目</button>
      </template>
    </AppHeader>

    <div class="app-body" :style="{ gridTemplateColumns: '200px 1fr' }">
      <aside class="pane pane-left" :style="{ padding: '16px 0' }">
        <button
          v-for="k in kinds"
          :key="k"
          class="side-item"
          type="button"
          :aria-current="codex.kind === k"
          @click="codex.kind = k"
        >
          <span>{{ CODEX_KIND_LABEL[k] }}</span>
          <span :style="{ opacity: 0.7 }">{{ codex.counts[k] ?? 0 }}</span>
        </button>
        <div class="rule-t" :style="{ marginTop: '16px', paddingTop: '16px' }">
          <div class="side-item">
            <span>待确认</span>
            <span v-if="codex.pending.length" class="badge">{{ codex.pending.length }}</span>
          </div>
          <div class="side-item"><span>常驻上下文</span><span :style="{ opacity: 0.7 }">{{ codex.resident.length }}</span></div>
          <div class="side-item"><span>导出设定</span></div>
        </div>
      </aside>

      <main class="pane" :style="{ padding: '22px', background: 'var(--color-neutral-100)' }">
        <div class="grid-rule" :style="{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }">
          <article
            v-for="e in codex.visible"
            :key="e.id"
            :style="{
              padding: '20px 18px', fontSize: '13px',
              boxShadow: e.status === 'pending'
                ? 'inset 0 0 0 2px var(--color-accent-400)'
                : e.resident ? 'inset 0 0 0 2px var(--color-accent)' : 'none'
            }"
          >
            <span class="tag" :class="e.status === 'pending' ? 'tag-accent' : e.resident ? 'tag-accent' : 'tag-neutral'" :style="{ fontSize: '11px' }">
              {{ e.status === 'pending' ? '待确认' : CODEX_KIND_LABEL[e.kind] + (e.resident ? ' · 常驻' : '') }}
            </span>
            <h3 :style="{ fontSize: '19px', fontWeight: 700, margin: '10px 0 8px' }">{{ e.name }}</h3>
            <p :style="{ margin: '0 0 14px', lineHeight: 1.65, color: 'var(--color-neutral-800)' }">{{ e.summary }}</p>

            <div class="rule-t" :style="{ paddingTop: '10px' }">
              <div v-if="e.status === 'pending'" class="row" :style="{ gap: '8px' }">
                <button class="chip chip-strong" type="button" @click="codex.confirm(e.id)">入库</button>
                <button class="chip" type="button" @click="codex.drop(e.id)">忽略</button>
              </div>
              <span v-else-if="e.conflicts" :style="{ color: 'var(--color-accent-700)', fontWeight: 700 }">
                {{ e.conflicts }} 处冲突待处理
              </span>
              <span v-else class="muted">
                引用 {{ e.refChapters.length }} 章 · 最近第 {{ Math.max(...e.refChapters) }} 章
              </span>
            </div>
          </article>
        </div>
        <p v-if="!codex.visible.length" class="muted" :style="{ padding: '40px 0' }">这一类还没有条目。</p>
      </main>
    </div>
  </div>
</template>
