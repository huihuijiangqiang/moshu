<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useCodexStore, codexEntrySearchText } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { CODEX_KIND_LABEL, type CodexEntry, type CodexKind } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'

const codex = useCodexStore()
const project = useProjectStore()
const shell = useShellStore()
const router = useRouter()
const { toProject } = useProjectNavigation()

const kinds = Object.keys(CODEX_KIND_LABEL) as CodexKind[]
type Scope = 'kind' | 'resident' | 'pending' | 'conflict' | 'all'
const scope = ref<Scope>('kind')
const selectedId = ref<string | null>(null)
const mobileDetailOpen = ref(false)

onMounted(() => shell.setCrumb('设定库'))

const scopes: { key: Scope; label: string; count: () => number }[] = [
  { key: 'all', label: '全部', count: () => codex.entries.length },
  { key: 'resident', label: '常驻', count: () => codex.resident.length },
  { key: 'pending', label: '待确认', count: () => codex.pending.length },
  { key: 'conflict', label: '有冲突', count: () => codex.entries.filter((entry) => entry.conflicts > 0).length }
]

/** 搜索时跨类型，人物动机、能力边界和关系信息也参与检索。 */
const rows = computed<CodexEntry[]>(() => {
  const q = codex.query.trim().toLowerCase()
  let pool = codex.entries
  if (scope.value === 'resident') pool = pool.filter((entry) => entry.resident)
  else if (scope.value === 'pending') pool = pool.filter((entry) => entry.status === 'pending')
  else if (scope.value === 'conflict') pool = pool.filter((entry) => entry.conflicts > 0)
  else if (scope.value === 'kind' && !q) pool = pool.filter((entry) => entry.kind === codex.kind)

  if (q) pool = pool.filter((entry) => codexEntrySearchText(entry).includes(q))

  return [...pool].sort((a, b) => {
    if ((a.status === 'pending') !== (b.status === 'pending')) return a.status === 'pending' ? -1 : 1
    if (!!a.conflicts !== !!b.conflicts) return a.conflicts ? -1 : 1
    return b.refChapters.length - a.refChapters.length
  })
})

const selected = computed(() => rows.value.find((entry) => entry.id === selectedId.value) ?? rows.value[0] ?? null)
const currentListLabel = computed(() => scope.value === 'kind' ? CODEX_KIND_LABEL[codex.kind] : scopes.find((item) => item.key === scope.value)?.label)

watch(rows, (list) => {
  if (!list.some((entry) => entry.id === selectedId.value)) {
    const preferred = scope.value === 'pending' ? list[0] : list.find((entry) => entry.status === 'confirmed') ?? list[0]
    selectedId.value = preferred?.id ?? null
  }
}, { immediate: true })

function pickKind(kind: CodexKind) {
  scope.value = 'kind'
  codex.kind = kind
  codex.query = ''
  mobileDetailOpen.value = false
}

function pickScope(nextScope: Scope) {
  scope.value = nextScope
  codex.query = ''
  mobileDetailOpen.value = false
}

function pickEntry(id: string) {
  selectedId.value = id
  mobileDetailOpen.value = true
}

function selectRelation(targetId?: string) {
  if (!targetId || !codex.byId.has(targetId)) return
  const target = codex.byId.get(targetId)
  scope.value = 'all'
  codex.query = ''
  if (target) codex.kind = target.kind
  selectedId.value = targetId
  mobileDetailOpen.value = true
}

const residentTokens = computed(() => codex.resident.length * 1900)
const latestRef = (entry: CodexEntry) => entry.refChapters.length ? Math.max(...entry.refChapters) : null
const gapChapters = (entry: CodexEntry) => {
  const last = latestRef(entry)
  return last === null ? null : Math.max(0, project.totalChapters - last)
}

const characterCore = computed(() => [
  { label: '核心欲望', value: selected.value?.character?.desire },
  { label: '行动动机', value: selected.value?.character?.motivation },
  { label: '致命缺陷', value: selected.value?.character?.flaw },
  { label: '深层恐惧', value: selected.value?.character?.fear }
])

const characterConstraints = computed(() => [
  { label: '能力', value: selected.value?.character?.ability },
  { label: '能力边界', value: selected.value?.character?.limitation },
  { label: '语言习惯', value: selected.value?.character?.speech },
  { label: '外在识别', value: selected.value?.character?.appearance },
  { label: '前史', value: selected.value?.character?.background }
])

const characterArc = computed(() => [
  { key: 'past', label: '过去', value: selected.value?.character?.arc?.past },
  { key: 'current', label: '当前', value: selected.value?.character?.arc?.current },
  { key: 'next', label: '下一步', value: selected.value?.character?.arc?.next }
])

const writingAnchors = computed(() => {
  if (selected.value?.kind === 'character') {
    return [
      { label: '当前状态', value: selected.value.character?.currentState },
      { label: '推进方向', value: selected.value.character?.arc?.next },
      { label: '不可写错', value: selected.value.character?.limitation }
    ]
  }
  return (selected.value?.facts ?? []).slice(0, 3)
})
</script>

<template>
  <div class="codex-layout" :data-mobile-detail="mobileDetailOpen">
    <aside class="codex-library" aria-label="设定库导航">
      <header class="codex-library-head">
        <div><span class="wk-label">世界资料</span><h1>设定库</h1></div>
        <strong>{{ codex.entries.length }}</strong>
      </header>

      <div class="codex-library-controls">
        <input v-model="codex.query" class="wk-input" placeholder="搜索人物、动机或约束" aria-label="搜索设定">

        <div class="codex-kind-tabs" role="tablist" aria-label="设定类型">
          <button
            v-for="kind in kinds"
            :key="kind"
            type="button"
            role="tab"
            :aria-selected="scope === 'kind' && codex.kind === kind"
            @click="pickKind(kind)"
          >
            <span>{{ CODEX_KIND_LABEL[kind] }}</span><small>{{ codex.counts[kind] ?? 0 }}</small>
          </button>
        </div>

        <div class="codex-scope-tabs" aria-label="设定范围">
          <button
            v-for="item in scopes"
            :key="item.key"
            type="button"
            :aria-pressed="scope === item.key"
            @click="pickScope(item.key)"
          >{{ item.label }}<span v-if="item.key === 'pending' || item.key === 'conflict'">{{ item.count() }}</span></button>
        </div>
      </div>

      <div class="codex-list-head"><span>{{ currentListLabel }}</span><small>{{ rows.length }} 条</small></div>
      <div class="codex-list" role="listbox" :aria-label="`${currentListLabel}设定`">
        <button
          v-for="entry in rows"
          :key="entry.id"
          class="codex-list-row"
          type="button"
          role="option"
          :aria-selected="entry.id === selected?.id"
          @click="pickEntry(entry.id)"
        >
          <span class="codex-list-title">
            <strong>{{ entry.name }}</strong>
            <span v-if="entry.status === 'pending'" class="pill pill-alert">待确认</span>
            <span v-if="entry.resident" class="pill pill-soft">常驻</span>
            <span v-if="entry.conflicts" class="pill pill-alert">{{ entry.conflicts }} 冲突</span>
            <small>{{ entry.refChapters.length }} 章</small>
          </span>
          <span class="codex-list-summary">{{ entry.summary }}</span>
        </button>

        <p v-if="!rows.length" class="codex-empty">
          {{ codex.query ? '没有匹配的设定，试试人物动机或能力名称。' : '这一类还没有条目。' }}
        </p>
      </div>

      <footer class="codex-budget">
        <span><strong>常驻上下文</strong><small>{{ codex.resident.length }} 条 · {{ (residentTokens / 1000).toFixed(1) }}k / 6k</small></span>
        <span class="bar" data-layer="resident" :data-over="residentTokens > 6000"><i :style="{ width: Math.min(100, (residentTokens / 6000) * 100) + '%' }" /></span>
      </footer>
    </aside>

    <main class="codex-detail" aria-label="设定详情">
      <template v-if="selected">
        <div class="codex-detail-bar">
          <button class="codex-mobile-back" type="button" aria-label="返回设定列表" @click="mobileDetailOpen = false">←</button>
          <span>{{ CODEX_KIND_LABEL[selected.kind] }}档案</span>
          <span class="codex-detail-status">
            <span v-if="selected.resident" class="pill pill-soft">常驻上下文</span>
            <span v-if="selected.status === 'pending'" class="pill pill-alert">资料待确认</span>
          </span>
        </div>

        <article class="codex-sheet">
          <section v-if="selected.status === 'pending'" class="codex-pending">
            <div><strong>守卫从正文抽取到这个人物</strong><p>只保留原文能够确认的资料，空缺项不会被系统擅自推断。</p></div>
            <div class="row">
              <button class="wk-btn" type="button" data-primary="true" @click="codex.confirm(selected.id)">确认入库</button>
              <button class="wk-btn" type="button" @click="codex.drop(selected.id)">忽略</button>
            </div>
          </section>

          <header class="codex-identity">
            <div class="codex-identity-main">
              <span class="wk-label">{{ selected.character?.role ?? CODEX_KIND_LABEL[selected.kind] }}</span>
              <h2>{{ selected.name }}</h2>
              <p v-if="selected.aliases.length" class="codex-aliases">又名 {{ selected.aliases.join('、') }}</p>
              <p class="codex-lead">{{ selected.summary }}</p>
              <div v-if="selected.kind === 'character'" class="codex-character-meta">
                <span>{{ selected.character?.age ?? '年龄待补充' }}</span>
                <span v-for="trait in selected.character?.personality ?? []" :key="trait">{{ trait }}</span>
              </div>
            </div>

            <aside v-if="writingAnchors.length" class="codex-writing-anchor">
              <div class="wk-label">{{ selected.kind === 'character' ? '本章写作锚点' : '写作核对' }}</div>
              <dl>
                <div v-for="item in writingAnchors" :key="item.label">
                  <dt>{{ item.label }}</dt><dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd>
                </div>
              </dl>
            </aside>
          </header>

          <template v-if="selected.kind === 'character'">
            <section class="codex-section" aria-labelledby="character-core-title">
              <div class="codex-section-heading"><h3 id="character-core-title">人物内核</h3><p>这个人为什么行动，又会被什么拖住</p></div>
              <dl class="codex-definition-grid">
                <div v-for="item in characterCore" :key="item.label"><dt>{{ item.label }}</dt><dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd></div>
              </dl>
            </section>

            <section class="codex-section" aria-labelledby="character-constraint-title">
              <div class="codex-section-heading"><h3 id="character-constraint-title">写作约束</h3><p>写对白、动作和能力时必须保持一致</p></div>
              <dl class="codex-constraint-list">
                <div v-for="item in characterConstraints" :key="item.label"><dt>{{ item.label }}</dt><dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd></div>
              </dl>
            </section>

            <section class="codex-section" aria-labelledby="character-arc-title">
              <div class="codex-section-heading"><h3 id="character-arc-title">人物弧</h3><p>变化不是履历，而是下一场戏的方向</p></div>
              <div class="character-arc">
                <div v-for="stage in characterArc" :key="stage.key" class="character-arc-step" :data-current="stage.key === 'current'">
                  <span class="character-arc-marker" /><strong>{{ stage.label }}</strong><p :data-empty="!stage.value">{{ stage.value ?? '待补充' }}</p>
                </div>
              </div>
            </section>
          </template>

          <section v-else-if="selected.facts?.length" class="codex-section" aria-labelledby="codex-facts-title">
            <div class="codex-section-heading"><h3 id="codex-facts-title">关键设定</h3><p>写作时不可违背的事实</p></div>
            <dl class="codex-constraint-list">
              <div v-for="fact in selected.facts" :key="fact.label"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
            </dl>
          </section>

          <section v-if="selected.relations?.length" class="codex-section" aria-labelledby="codex-relations-title">
            <div class="codex-section-heading"><h3 id="codex-relations-title">关联设定</h3><p>人物与世界如何彼此施力</p></div>
            <div class="codex-relations">
              <button
                v-for="relation in selected.relations"
                :key="`${relation.name}-${relation.relation}`"
                type="button"
                :disabled="!relation.targetId"
                @click="selectRelation(relation.targetId)"
              >
                <span><strong>{{ relation.name }}</strong><small>{{ relation.relation }}</small></span>
                <p>{{ relation.note }}</p><span v-if="relation.targetId" aria-hidden="true">→</span>
              </button>
            </div>
          </section>

          <section class="codex-section codex-evidence" aria-labelledby="codex-evidence-title">
            <div class="codex-section-heading"><h3 id="codex-evidence-title">正文依据</h3><p>档案来自哪些章节</p></div>
            <div class="codex-stats">
              <div><strong>{{ selected.refChapters.length }}</strong><span>引用章数</span></div>
              <div><strong>{{ latestRef(selected) ?? '—' }}</strong><span>最近出现</span></div>
              <div><strong :data-alert="(gapChapters(selected) ?? 0) > 30">{{ gapChapters(selected) ?? '—' }}</strong><span>断档章数</span></div>
              <div><strong :data-alert="selected.conflicts > 0">{{ selected.conflicts }}</strong><span>待处理冲突</span></div>
            </div>

            <button v-if="selected.conflicts" class="wk-btn codex-guard-link" type="button" data-primary="true" @click="router.push(toProject('guard'))">
              去一致性守卫处理 {{ selected.conflicts }} 处冲突
            </button>
            <p v-if="selected.kind === 'foreshadow'" class="codex-foreshadow">
              埋于第 {{ selected.plantedAt }} 章 · 预计在{{ selected.expectedBy }}回收。超过 30 章未提会自动提醒。
            </p>
            <div class="codex-chapters">
              <button
                v-for="chapter in selected.refChapters.slice(-40)"
                :key="chapter"
                class="pill"
                type="button"
                :aria-label="`打开第 ${chapter} 章`"
                @click="router.push(toProject('write'))"
              >{{ chapter }}</button>
              <span v-if="selected.refChapters.length > 40">仅显示最近 40 章</span>
            </div>
          </section>
        </article>
      </template>

      <p v-else class="codex-empty">从左侧选择一条设定。</p>
    </main>
  </div>
</template>
