<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ratioApi, type SegmentSource } from '@/api/mock/ai-ratio'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'

const store = useProjectStore()
const shell = useShellStore()
const report = ref<Awaited<ReturnType<typeof ratioApi.report>> | null>(null)
const scope = ref<'chapter' | 'book'>('chapter')

onMounted(async () => {
  shell.setCrumb('AI 占比自查')
  report.value = await ratioApi.report(store.activeId ?? '')
})

const legend: { source: SegmentSource; label: string }[] = [
  { source: 'ai-raw', label: 'AI 原文' },
  { source: 'ai-edited', label: 'AI 起草已改' },
  { source: 'human', label: '你手写' },
  { source: 'suspect', label: '疑似 AI 句式' }
]

function swatch(source: SegmentSource) {
  switch (source) {
    case 'ai-raw': return { background: 'var(--color-accent-200)' }
    case 'ai-edited': return { background: 'var(--color-neutral-300)' }
    case 'suspect': return { background: 'var(--color-accent)' }
    default: return { background: 'transparent', border: '2px solid var(--color-neutral-400)' }
  }
}

function segStyle(source: SegmentSource) {
  switch (source) {
    case 'ai-raw': return { background: 'var(--color-accent-200)' }
    case 'ai-edited': return { background: 'var(--color-neutral-300)' }
    case 'suspect': return { background: 'var(--color-accent-100)', borderBottom: '2px solid var(--color-accent)' }
    default: return {}
  }
}
</script>

<template>
  <div class="wk-pane" :style="{ height: '100%', overflow: 'auto' }">
    <Teleport to="#topbar-actions">
      <button
        v-for="s in (['chapter', 'book'] as const)"
        :key="s"
        class="topbar-btn"
        type="button"
        :data-primary="scope === s"
        @click="scope = s"
      >{{ s === 'chapter' ? '本章' : '全书' }}</button>
      <button class="topbar-btn" type="button">导出自查报告</button>
    </Teleport>

    <template v-if="report">
      <div class="grid-rule rule-b" :style="{ gridTemplateColumns: 'repeat(4, 1fr)', flex: 'none' }">
        <div :style="{ padding: '24px 22px', background: 'var(--color-bg)', fontSize: '13px' }">
          <div class="num" :style="{ fontSize: '40px' }">{{ report.aiRaw }}%</div>
          <div :style="{ marginTop: '8px', fontWeight: 700 }">AI 原文未改动</div>
          <div class="muted" :style="{ marginTop: '4px' }">采纳后一字未动的部分</div>
        </div>
        <div :style="{ padding: '24px 22px', background: 'var(--color-bg)', fontSize: '13px' }">
          <div class="num" :style="{ fontSize: '40px' }">{{ report.aiEdited }}%</div>
          <div :style="{ marginTop: '8px', fontWeight: 700 }">AI 起草后你改过</div>
          <div class="muted" :style="{ marginTop: '4px' }">改动 30% 以上视为你的</div>
        </div>
        <div :style="{ padding: '24px 22px', background: 'var(--color-bg)', fontSize: '13px' }">
          <div class="num" :style="{ fontSize: '40px' }">{{ report.human }}%</div>
          <div :style="{ marginTop: '8px', fontWeight: 700 }">你手写</div>
          <div class="muted" :style="{ marginTop: '4px' }">完全由你键入</div>
        </div>
        <div :style="{ padding: '24px 22px', background: 'var(--color-bg)', fontSize: '13px' }">
          <div class="num" :style="{ fontSize: '40px', color: 'var(--color-accent)' }">{{ report.suspectCount }}</div>
          <div :style="{ marginTop: '8px', fontWeight: 700 }">疑似 AI 句式</div>
          <div class="muted" :style="{ marginTop: '4px' }">本章内，建议逐条改写</div>
        </div>
      </div>

      <div class="app-body" :style="{ gridTemplateColumns: '1fr 340px' }">
        <main class="pane" :style="{ padding: '30px 40px', background: 'var(--color-neutral-100)' }">
          <div class="row muted" :style="{ gap: '20px', marginBottom: '24px', fontSize: '13px' }">
            <span v-for="l in legend" :key="l.source" class="row" :style="{ gap: '8px' }">
              <span :style="{ width: '16px', height: '10px', ...swatch(l.source) }" />{{ l.label }}
            </span>
          </div>

          <div :style="{ maxWidth: '640px', fontSize: '15px', lineHeight: 2.1 }">
            <p v-for="(para, i) in report.paragraphs" :key="i" :style="{ margin: '0 0 20px' }">
              <span v-for="(seg, j) in para" :key="j" :style="segStyle(seg.source)">{{ seg.text }}</span>
              <span
                v-if="para.some((s) => s.note)"
                :style="{ color: 'var(--color-accent-700)', fontWeight: 700, fontSize: '12px', marginLeft: '8px' }"
              >← 疑似 AI 句式：{{ para.find((s) => s.note)?.note }}</span>
            </p>
          </div>
        </main>

        <aside class="pane pane-right" :style="{ padding: '24px 20px', fontSize: '13px' }">
          <div class="kicker" :style="{ color: 'var(--color-accent)', marginBottom: '18px' }">
            疑似 AI 句式 · {{ report.suspectCount }}
          </div>
          <div class="grid-rule">
            <div v-for="s in report.suspects" :key="s.id" :style="{ padding: '14px 0', background: 'var(--color-bg)' }">
              <div :style="{ lineHeight: 1.7, marginBottom: '10px' }">{{ s.text }}</div>
              <div class="muted" :style="{ lineHeight: 1.6, marginBottom: '10px' }">{{ s.reason }}</div>
              <div class="row" :style="{ gap: '8px' }">
                <button class="chip chip-strong" type="button">按我的风格改写</button>
                <button class="chip" type="button">保留</button>
              </div>
            </div>
            <div class="muted" :style="{ padding: '14px 0', background: 'var(--color-bg)' }">
              还有 {{ Math.max(0, report.suspectCount - report.suspects.length) }} 条 · 展开
            </div>
          </div>
          <button class="btn btn-primary" type="button" :style="{ width: '100%', height: '38px', fontSize: '13px', marginTop: '20px' }">
            全部按我的风格改写
          </button>
          <p class="muted" :style="{ margin: '18px 0 0', lineHeight: 1.7 }">
            这些数字只在你的账号内可见，不会提供给任何平台。检测标准会随平台政策更新。
          </p>
        </aside>
      </div>
    </template>
  </div>
</template>
