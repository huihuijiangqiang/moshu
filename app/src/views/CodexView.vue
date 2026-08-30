<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { CODEX_KIND_LABEL, type CodexEntry, type CodexKind } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'

/**
 * 设定库：三栏（分类 / 列表 / 详情），不用卡片墙。
 * 142 条条目做成等宽卡片要滚五屏，而作者的动作是「找一条、看它、改它」，
 * 主从布局比网格快得多。
 */
const codex = useCodexStore()
const project = useProjectStore()
const shell = useShellStore()
const router = useRouter()
const { toProject } = useProjectNavigation()

const kinds = Object.keys(CODEX_KIND_LABEL) as CodexKind[]
type Scope = 'kind' | 'resident' | 'pending' | 'conflict' | 'all'
const scope = ref<Scope>('kind')
const selectedId = ref<string | null>(null)

onMounted(() => {
  shell.setCrumb('设定库')
})

/** 列表：范围先过滤，再套搜索词。搜索时跨类型，否则「玄铁」要先切到伏笔才搜得到。 */
const rows = computed<CodexEntry[]>(() => {
  const q = codex.query.trim().toLowerCase()
  let pool = codex.entries
  if (scope.value === 'resident') pool = pool.filter((e) => e.resident)
  else if (scope.value === 'pending') pool = pool.filter((e) => e.status === 'pending')
  else if (scope.value === 'conflict') pool = pool.filter((e) => e.conflicts > 0)
  else if (scope.value === 'kind' && !q) pool = pool.filter((e) => e.kind === codex.kind)

  if (q) {
    pool = pool.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.aliases.some((a) => a.toLowerCase().includes(q)) ||
        e.summary.toLowerCase().includes(q)
    )
  }
  // 待确认排最前（要作者处理），然后有冲突的，再按引用章数降序
  return [...pool].sort((a, b) => {
    if ((a.status === 'pending') !== (b.status === 'pending')) return a.status === 'pending' ? -1 : 1
    if (!!a.conflicts !== !!b.conflicts) return a.conflicts ? -1 : 1
    return b.refChapters.length - a.refChapters.length
  })
})

const selected = computed(() => rows.value.find((e) => e.id === selectedId.value) ?? rows.value[0] ?? null)

// 列表变了就把选中项拉回第一条，避免详情栏显示已被过滤掉的条目
watch(rows, (list) => {
  if (!list.some((e) => e.id === selectedId.value)) selectedId.value = list[0]?.id ?? null
}, { immediate: true })

function pickKind(k: CodexKind) {
  scope.value = 'kind'
  codex.kind = k
  codex.query = ''
}

const scopes: { key: Scope; label: string; count: () => number }[] = [
  { key: 'all', label: '全部', count: () => codex.entries.length },
  { key: 'resident', label: '常驻上下文', count: () => codex.resident.length },
  { key: 'pending', label: '待确认', count: () => codex.pending.length },
  { key: 'conflict', label: '有冲突', count: () => codex.entries.filter((e) => e.conflicts > 0).length }
]

/** 常驻条目的预算占用：第 1 层上限 6k，142 条里只有 3 条常驻，作者需要知道还能加几条 */
const residentTokens = computed(() => codex.resident.length * 1900)

const latestRef = (e: CodexEntry) => (e.refChapters.length ? Math.max(...e.refChapters) : null)
const gapChapters = (e: CodexEntry) => {
  const last = latestRef(e)
  if (last === null) return null
  return project.chapters.length - last
}
</script>

<template>
  <div class="wk-cols" :style="{ gridTemplateColumns: '188px 300px minmax(0, 1fr)' }">
    <!-- 分类 + 范围 -->
    <aside class="wk-pane" aria-label="分类">
      <div class="wk-head"><span>分类</span><span class="wk-head-push">{{ codex.entries.length }}</span></div>
      <button
        v-for="k in kinds"
        :key="k"
        class="wk-row"
        type="button"
        role="option"
        :aria-selected="scope === 'kind' && codex.kind === k"
        @click="pickKind(k)"
      >
        <span class="wk-row-name">{{ CODEX_KIND_LABEL[k] }}</span>
        <span class="wk-row-meta">{{ codex.counts[k] ?? 0 }}</span>
      </button>

      <div class="wk-group"><span>视图</span></div>
      <button
        v-for="s in scopes"
        :key="s.key"
        class="wk-row"
        type="button"
        role="option"
        :aria-selected="scope === s.key"
        @click="scope = s.key"
      >
        <span class="wk-row-name">{{ s.label }}</span>
        <span v-if="s.key === 'pending' && s.count()" class="pill pill-alert">{{ s.count() }}</span>
        <span v-else-if="s.key === 'conflict' && s.count()" class="pill pill-soft">{{ s.count() }}</span>
        <span v-else class="wk-row-meta">{{ s.count() }}</span>
      </button>

      <div class="wk-sec" :style="{ borderTop: 'var(--hair) solid var(--line)' }">
        <div class="wk-label" :style="{ marginBottom: '6px' }">第 1 层 · 常驻预算</div>
        <div class="bar" data-layer="resident" :data-over="residentTokens > 6000">
          <span :style="{ width: Math.min(100, (residentTokens / 6000) * 100) + '%' }" />
        </div>
        <p :style="{ margin: '6px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', lineHeight: 1.6 }">
          {{ codex.resident.length }} 条常驻 · 约 {{ (residentTokens / 1000).toFixed(1) }}k / 6k。
          常驻条目每次生成都会带上，超预算会挤掉检索条目。
        </p>
      </div>
    </aside>

    <!-- 列表 -->
    <div class="wk-pane" aria-label="条目列表">
      <div class="wk-head">
        <span>{{ scope === 'kind' ? CODEX_KIND_LABEL[codex.kind] : scopes.find((s) => s.key === scope)?.label }}</span>
        <span class="wk-head-push">{{ rows.length }} 条</span>
      </div>
      <div :style="{ padding: 'var(--u2)', borderBottom: 'var(--hair) solid var(--line)' }">
        <input v-model="codex.query" class="wk-input" placeholder="搜索名称、别名、描述" aria-label="搜索条目">
      </div>

      <button
        v-for="e in rows"
        :key="e.id"
        class="wk-row"
        type="button"
        role="option"
        :aria-selected="e.id === selected?.id"
        :style="{ minHeight: 'auto', padding: '6px var(--u3)', display: 'grid', gap: '2px', alignItems: 'start' }"
        @click="selectedId = e.id"
      >
        <span class="row" :style="{ gap: '6px', width: '100%' }">
          <span :style="{ fontWeight: 700, color: 'var(--ink)' }">{{ e.name }}</span>
          <span v-if="e.status === 'pending'" class="pill pill-alert">待确认</span>
          <span v-if="e.resident" class="pill pill-soft">常驻</span>
          <span v-if="e.conflicts" class="pill pill-alert">{{ e.conflicts }} 冲突</span>
          <span class="wk-row-meta" :style="{ marginLeft: 'auto' }">{{ e.refChapters.length }} 章</span>
        </span>
        <span :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', width: '100%' }">
          {{ CODEX_KIND_LABEL[e.kind] }} · {{ e.summary }}
        </span>
      </button>

      <p v-if="!rows.length" :style="{ padding: 'var(--u5) var(--u3)', color: 'var(--ink-3)', lineHeight: 1.7 }">
        没有匹配的条目。{{ codex.query ? '换个词，或清空搜索框。' : '这一类还没有条目。' }}
      </p>
    </div>

    <!-- 详情 -->
    <main class="wk-pane wk-pane-paper" aria-label="条目详情">
      <template v-if="selected">
        <div class="paper-bar">
          <span :style="{ fontWeight: 700, color: 'var(--ink)', fontSize: 'var(--fs)' }">{{ selected.name }}</span>
          <span class="pill">{{ CODEX_KIND_LABEL[selected.kind] }}</span>
          <span v-if="selected.resident" class="pill pill-soft">常驻上下文</span>
          <span :style="{ marginLeft: 'auto' }" />
          <button class="wk-btn wk-btn-xs" type="button">编辑</button>
          <button class="wk-btn wk-btn-xs" type="button">关系图</button>
        </div>

        <div :style="{ maxWidth: '760px', padding: 'var(--u6)' }">
          <!-- 待确认：动作放最上面，这是唯一需要立刻决定的条目状态 -->
          <div
            v-if="selected.status === 'pending'"
            :style="{ padding: 'var(--u3)', marginBottom: 'var(--u5)', background: 'var(--alert-soft)', border: 'var(--hair) solid var(--alert-line)' }"
          >
            <div class="wk-label" :style="{ color: 'var(--alert-ink)', marginBottom: '6px' }">守卫自动抽取，等你确认</div>
            <p :style="{ margin: '0 0 var(--u3)', fontSize: 'var(--fs)', lineHeight: 1.75, color: 'var(--ink-2)' }">
              入库后会进入检索池，之后每一章生成都能引用它；忽略则不再提示。
            </p>
            <div class="row" :style="{ gap: 'var(--u2)' }">
              <button class="wk-btn" type="button" data-primary="true" @click="codex.confirm(selected.id)">入库</button>
              <button class="wk-btn" type="button" @click="codex.drop(selected.id)">忽略</button>
            </div>
          </div>

          <h2 :style="{ fontSize: 'var(--fs-xl)', fontWeight: 700, margin: '0 0 var(--u2)' }">{{ selected.name }}</h2>
          <p v-if="selected.aliases.length" :style="{ margin: '0 0 var(--u4)', fontSize: 'var(--fs-sm)', color: 'var(--ink-3)' }">
            别名：{{ selected.aliases.join('、') }}
          </p>
          <p :style="{ margin: '0 0 var(--u6)', fontFamily: 'var(--font-prose)', fontSize: 'var(--fs-lg)', lineHeight: 1.95, color: 'var(--ink)' }">
            {{ selected.summary }}
          </p>

          <!-- 数据行：引用章数、最近出现、断档、冲突 -->
          <div
            :style="{
              display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
              gap: 'var(--rule)', background: 'var(--line)', marginBottom: 'var(--u5)'
            }"
          >
            <div :style="{ background: 'var(--paper)', padding: 'var(--u3)' }">
              <div :style="{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xl)', fontWeight: 700 }">
                {{ selected.refChapters.length }}
              </div>
              <div :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', marginTop: '3px' }">引用章数</div>
            </div>
            <div :style="{ background: 'var(--paper)', padding: 'var(--u3)' }">
              <div :style="{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xl)', fontWeight: 700 }">
                {{ latestRef(selected) ?? '—' }}
              </div>
              <div :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', marginTop: '3px' }">最近出现章</div>
            </div>
            <div :style="{ background: 'var(--paper)', padding: 'var(--u3)' }">
              <div
                :style="{
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xl)', fontWeight: 700,
                  color: (gapChapters(selected) ?? 0) > 30 ? 'var(--alert)' : 'var(--ink)'
                }"
              >{{ gapChapters(selected) ?? '—' }}</div>
              <div :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', marginTop: '3px' }">已断档章数</div>
            </div>
            <div :style="{ background: 'var(--paper)', padding: 'var(--u3)' }">
              <div
                :style="{
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xl)', fontWeight: 700,
                  color: selected.conflicts ? 'var(--alert)' : 'var(--ink)'
                }"
              >{{ selected.conflicts }}</div>
              <div :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', marginTop: '3px' }">待处理冲突</div>
            </div>
          </div>

          <div v-if="selected.conflicts" :style="{ marginBottom: 'var(--u5)' }">
            <button class="wk-btn" type="button" data-primary="true" @click="router.push(toProject('guard'))">
              去一致性守卫处理 {{ selected.conflicts }} 处冲突
            </button>
          </div>

          <div v-if="selected.kind === 'foreshadow'" :style="{ marginBottom: 'var(--u5)', paddingTop: 'var(--u4)', borderTop: 'var(--hair) solid var(--line)' }">
            <div class="wk-label" :style="{ marginBottom: '6px' }">伏笔追踪</div>
            <p :style="{ margin: 0, fontSize: 'var(--fs)', lineHeight: 1.8, color: 'var(--ink-2)' }">
              埋于第 {{ selected.plantedAt }} 章 · 预计回收 {{ selected.expectedBy }}。
              超过 30 章未提会自动提醒，你不需要自己记。
            </p>
          </div>

          <!-- 引用章列表：点进去直接跳那一章 -->
          <div :style="{ paddingTop: 'var(--u4)', borderTop: 'var(--hair) solid var(--line)' }">
            <div class="wk-label" :style="{ marginBottom: 'var(--u2)' }">出现在这些章</div>
            <div class="row" :style="{ flexWrap: 'wrap', gap: '4px' }">
              <button
                v-for="n in selected.refChapters.slice(-40)"
                :key="n"
                class="pill"
                type="button"
                :style="{ cursor: 'pointer', height: '20px', fontFamily: 'var(--font-mono)' }"
                @click="router.push(toProject('write'))"
              >{{ n }}</button>
              <span v-if="selected.refChapters.length > 40" :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)' }">
                仅显示最近 40 章
              </span>
            </div>
          </div>
        </div>
      </template>

      <p v-else :style="{ padding: 'var(--u6)', color: 'var(--ink-3)' }">左侧选一条设定。</p>
    </main>
  </div>
</template>
