<script setup lang="ts">
import { computed, ref } from 'vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'

const store = useProjectStore()
const codex = useCodexStore()

const parts = ref({ body: true, codex: true, outline: true, history: false, ratio: false })
const format = ref<'TXT' | 'DOCX' | 'Markdown' | 'EPUB'>('TXT')
const split = ref<'single' | 'zip'>('single')
const done = ref(false)

const items = computed(() => [
  { key: 'body' as const, label: `正文 ${store.chapters.length} 章 · ${(store.totalWords / 10000).toFixed(1)} 万字` },
  { key: 'codex' as const, label: `设定库 ${codex.entries.length} 条` },
  { key: 'outline' as const, label: '大纲与章纲' },
  { key: 'history' as const, label: '版本历史（体积较大）' },
  { key: 'ratio' as const, label: 'AI 占比自查报告' }
])

/** 真实实现：POST /api/export 拿任务 id，轮询完成后下载。这里直接本地拼 TXT 演示。 */
function exportNow() {
  const text = store.chapters
    .map((c) => `第${c.index}章　${c.title}\n\n${(c.content ?? '（正文未加载）').replace(/<[^>]+>/g, '')}`)
    .join('\n\n\n')
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${store.project?.title ?? 'novel'}.txt`
  a.click()
  URL.revokeObjectURL(a.href)
  done.value = true
}
</script>

<template>
  <div class="app">
    <AppHeader subtitle="导出" />
    <main class="pane" :style="{ flex: 1, padding: '40px', background: 'var(--color-neutral-100)' }">
      <div :style="{ maxWidth: '520px', fontSize: '13px' }">
        <h1 :style="{ fontSize: '28px', fontWeight: 700, margin: '0 0 30px' }">导出《{{ store.project?.title }}》</h1>

        <div class="kicker" :style="{ marginBottom: '14px' }">内容</div>
        <div :style="{ display: 'grid', gap: '12px', marginBottom: '26px' }">
          <label v-for="it in items" :key="it.key" class="row" :style="{ gap: '10px', cursor: 'pointer' }">
            <input v-model="parts[it.key]" type="checkbox" :style="{ display: 'none' }">
            <span :style="{ width: '16px', height: '16px', border: '2px solid ' + (parts[it.key] ? 'var(--color-text)' : 'var(--color-neutral-400)'), background: parts[it.key] ? 'var(--color-accent)' : 'transparent' }" />
            <span :style="{ color: parts[it.key] ? 'var(--color-text)' : 'var(--color-neutral-800)' }">{{ it.label }}</span>
          </label>
        </div>

        <div class="kicker" :style="{ marginBottom: '14px' }">格式</div>
        <div :style="{ display: 'flex', gap: '2px', background: 'var(--color-divider)', marginBottom: '26px' }">
          <button
            v-for="fm in (['TXT', 'DOCX', 'Markdown', 'EPUB'] as const)"
            :key="fm"
            type="button"
            :style="{
              flex: 1, border: 0, padding: '10px 0', cursor: 'pointer',
              background: format === fm ? 'var(--color-accent)' : 'var(--color-neutral-100)',
              color: format === fm ? '#fff' : 'var(--color-text)',
              fontWeight: format === fm ? 700 : 400
            }"
            @click="format = fm"
          >{{ fm }}</button>
        </div>

        <div class="kicker" :style="{ marginBottom: '14px' }">分章方式</div>
        <div :style="{ display: 'grid', gap: '10px', marginBottom: '26px' }">
          <label v-for="s in ([['single', '单文件，章间空行分隔（投稿常用）'], ['zip', '一章一文件，打包 zip']] as const)" :key="s[0]" class="row" :style="{ gap: '10px', cursor: 'pointer' }">
            <input v-model="split" type="radio" :value="s[0]" :style="{ display: 'none' }">
            <span :style="{ width: '14px', height: '14px', borderRadius: '50%', border: '2px solid ' + (split === s[0] ? 'var(--color-accent)' : 'var(--color-neutral-400)'), background: split === s[0] ? 'var(--color-accent)' : 'transparent' }" />
            <span :style="{ color: 'var(--color-neutral-800)' }">{{ s[1] }}</span>
          </label>
        </div>

        <button class="btn btn-primary" type="button" :style="{ width: '100%', height: '42px', fontSize: '14px' }" @click="exportNow">
          导出（免费）
        </button>
        <p v-if="done" :style="{ margin: '14px 0 0', color: 'var(--color-accent-700)', fontWeight: 700 }">已开始下载。</p>

        <p class="rule-t" :style="{ margin: '18px 0 0', paddingTop: '16px', color: 'var(--color-neutral-800)', lineHeight: 1.75 }">
          <strong>导出永久免费且全量。</strong>停止订阅后你依然可以随时导出全部作品与设定——你的稿子是你的，不是我们的。
        </p>
      </div>
    </main>
  </div>
</template>
