<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { contentApi } from '@/api/content'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { CODEX_KIND_LABEL, type ContextLayer } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'

/**
 * AI 面板。三件事按重要性排：能不能生成（章纲 + 参数）、
 * 生成会带什么进去（四层预算）、生成前有什么没解决（守卫提醒）。
 * 四层预算是产品的硬约束，必须逐层可见 —— 作者要知道 25k 被谁吃掉了。
 */
const props = defineProps<{ generating?: boolean }>()
const emit = defineEmits<{ generate: []; stop: [] }>()

const router = useRouter()
const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const { toProject } = useProjectNavigation()

const layers = ref<ContextLayer[]>([])
const tab = ref<'ai' | 'refs' | 'notes'>('ai')
const targetWords = ref(3000)
const model = ref<'basic' | 'advanced'>('basic')

const BUDGET = 25000

onMounted(async () => {
  layers.value = await contentApi.getContextLayers()
})

const total = computed(() => layers.value.reduce((s, l) => s + l.tokens, 0))
const over = computed(() => total.value > BUDGET)
const k = (n: number) => (n / 1000).toFixed(1) + 'k'

/** 每层的上限，和后端 assembler 的四层预算一致 */
const CAP: Record<ContextLayer['key'], number> = {
  resident: 6000,
  retrieved: 5000,
  summary: 4000,
  adjacent: 10000
}

const cost = computed(() => (model.value === 'advanced' ? 35 : 18))

/** 本章引用的设定：mock 下按章号反查 refChapters */
const refs = computed(() => {
  const idx = project.active?.index
  if (!idx) return []
  return codex.entries.filter((e) => e.refChapters.includes(idx))
})
</script>

<template>
  <div>
    <div class="wk-tabs">
      <button
        v-for="t in (['ai', 'refs', 'notes'] as const)"
        :key="t"
        class="wk-tab"
        type="button"
        role="tab"
        :aria-selected="tab === t"
        @click="tab = t"
      >
        {{ t === 'ai' ? 'AI' : t === 'refs' ? `引用 ${refs.length}` : '笔记' }}
      </button>
    </div>

    <template v-if="tab === 'ai'">
      <!-- 章纲：生成的依据，放最上面 -->
      <section class="wk-sec">
        <div class="wk-label" :style="{ marginBottom: 'var(--u2)' }">本章章纲</div>
        <ol
          v-if="project.active?.outline.length"
          :style="{ margin: '0 0 var(--u3)', padding: '0 0 0 18px', fontSize: 'var(--fs)', lineHeight: 1.85, color: 'var(--ink-2)' }"
        >
          <li v-for="(o, i) in project.active.outline" :key="i">{{ o }}</li>
        </ol>
        <p v-else :style="{ margin: '0 0 var(--u3)', color: 'var(--ink-3)', lineHeight: 1.7 }">
          本章还没有章纲，先写一句你想发生什么。
        </p>

        <button
          v-if="props.generating"
          class="wk-btn wk-btn-block"
          type="button"
          @click="emit('stop')"
        >停止生成</button>
        <button
          v-else
          class="wk-btn wk-btn-block"
          type="button"
          data-primary="true"
          @click="emit('generate')"
        >按章纲生成整章</button>
      </section>

      <!-- 生成参数 -->
      <section class="wk-sec">
        <div class="wk-label" :style="{ marginBottom: 'var(--u2)' }">生成参数</div>
        <div :style="{ display: 'grid', gap: '6px', fontSize: 'var(--fs)' }">
          <label class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">模型档位</span>
            <select v-model="model" class="wk-input" :style="{ width: '120px', height: '22px' }">
              <option value="basic">基础档</option>
              <option value="advanced">高级档</option>
            </select>
          </label>
          <label class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">目标字数</span>
            <input v-model.number="targetWords" class="wk-input" type="number" step="500"
                   :style="{ width: '120px', height: '22px', fontFamily: 'var(--font-mono)' }">
          </label>
          <div class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">风格档</span>
            <span>{{ project.project?.styleProfile ?? '未设置' }}</span>
          </div>
          <div class="row-between">
            <span :style="{ color: 'var(--ink-2)' }">预估消耗</span>
            <span :style="{ fontFamily: 'var(--font-mono)' }">{{ cost }} 积分</span>
          </div>
        </div>
      </section>

      <!-- 四层上下文预算 -->
      <section class="wk-sec">
        <div class="row-between" :style="{ marginBottom: 'var(--u2)' }">
          <span class="wk-label">上下文预算</span>
          <span
            :style="{
              fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)',
              color: over ? 'var(--alert-ink)' : 'var(--ink-3)',
              fontWeight: over ? 700 : 400
            }"
          >{{ k(total) }} / 25k</span>
        </div>

        <div :style="{ display: 'grid', gap: 'var(--u2)' }">
          <div v-for="l in layers" :key="l.key">
            <div class="row-between" :style="{ fontSize: 'var(--fs-sm)', marginBottom: '3px' }">
              <span :title="l.detail" :style="{ color: 'var(--ink-2)' }">{{ l.label }}</span>
              <span :style="{ fontFamily: 'var(--font-mono)', color: 'var(--ink-3)' }">
                {{ k(l.tokens) }} / {{ k(CAP[l.key]) }}
              </span>
            </div>
            <div class="bar" :data-layer="l.key" :data-over="l.tokens > CAP[l.key]">
              <span :style="{ width: Math.min(100, (l.tokens / CAP[l.key]) * 100) + '%' }" />
            </div>
          </div>
        </div>
      </section>

      <!-- 守卫提醒：只取最紧要的三条 -->
      <section class="wk-sec">
        <div class="row-between" :style="{ marginBottom: 'var(--u2)' }">
          <span class="wk-label" :style="{ color: guard.open.length ? 'var(--alert-ink)' : 'var(--ink-3)' }">
            守卫提醒
          </span>
          <button class="wk-btn wk-btn-xs" type="button" @click="router.push(toProject('guard'))">
            全部 {{ guard.open.length }}
          </button>
        </div>

        <div :style="{ display: 'grid', gap: '6px' }">
          <button
            v-for="i in guard.topThree"
            :key="i.id"
            type="button"
            :style="{
              display: 'grid', gap: '3px', width: '100%', textAlign: 'left', cursor: 'pointer',
              padding: '6px var(--u2)', fontSize: 'var(--fs-sm)', lineHeight: 1.55,
              background: 'var(--panel-sunken)', border: 0,
              borderLeft: '2px solid ' + (i.severity === 'high' ? 'var(--alert)' : 'var(--ink-4)')
            }"
            @click="router.push(toProject('guard'))"
          >
            <span class="row" :style="{ gap: '6px' }">
              <span class="pill" :class="i.severity === 'high' ? 'pill-alert' : ''">
                {{ i.severity === 'high' ? '高' : '中' }}
              </span>
              <span :style="{ fontWeight: 700, color: 'var(--ink)' }">{{ i.category }}</span>
              <span :style="{ marginLeft: 'auto', color: 'var(--ink-4)' }">{{ i.chapterRef }}</span>
            </span>
            <span :style="{ color: 'var(--ink-2)' }">{{ i.title }}</span>
          </button>
          <p v-if="!guard.open.length" :style="{ margin: 0, color: 'var(--ink-3)' }">暂无提醒。</p>
        </div>
      </section>
    </template>

    <template v-else-if="tab === 'refs'">
      <div class="wk-head"><span>本章引用</span><span class="wk-head-push">{{ refs.length }} 条</span></div>
      <button
        v-for="e in refs"
        :key="e.id"
        class="wk-row"
        type="button"
        :style="{ minHeight: 'auto', padding: 'var(--u2) var(--u3)', display: 'grid', gap: '2px' }"
        @click="router.push(toProject('codex'))"
      >
        <span class="row" :style="{ gap: '6px' }">
          <span :style="{ fontWeight: 700, color: 'var(--ink)' }">{{ e.name }}</span>
          <span class="pill">{{ CODEX_KIND_LABEL[e.kind] }}</span>
          <span v-if="e.resident" class="pill pill-soft">常驻</span>
          <span v-if="e.conflicts" class="pill pill-alert">{{ e.conflicts }} 冲突</span>
        </span>
        <span :style="{ color: 'var(--ink-3)', fontSize: 'var(--fs-sm)', lineHeight: 1.6, whiteSpace: 'normal' }">
          {{ e.summary }}
        </span>
      </button>
      <p v-if="!refs.length" :style="{ padding: 'var(--u5) var(--u3)', color: 'var(--ink-3)', lineHeight: 1.7 }">
        正文里以 <code>@</code> 插入的条目会出现在这里，并自动进入生成上下文。
      </p>
    </template>

    <template v-else>
      <div class="wk-head"><span>笔记</span></div>
      <div :style="{ padding: 'var(--u3)' }">
        <textarea
          class="wk-input"
          :style="{ height: '160px', padding: 'var(--u2)', lineHeight: 1.7, resize: 'vertical' }"
          placeholder="只属于你的备忘，不进入 AI 上下文。"
        />
      </div>
    </template>
  </div>
</template>
