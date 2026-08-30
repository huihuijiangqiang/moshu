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

onMounted(() => shell.setCrumb('设定库'))

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

watch(rows, (list) => {
  if (!list.some((entry) => entry.id === selectedId.value)) {
    const preferred = scope.value === 'pending'
      ? list[0]
      : list.find((entry) => entry.status === 'confirmed') ?? list[0]
    selectedId.value = preferred?.id ?? null
  }
}, { immediate: true })

function pickKind(kind: CodexKind) {
  scope.value = 'kind'
  codex.kind = kind
  codex.query = ''
}

function selectRelation(targetId?: string) {
  if (!targetId || !codex.byId.has(targetId)) return
  const target = codex.byId.get(targetId)
  scope.value = 'all'
  codex.query = ''
  if (target) codex.kind = target.kind
  selectedId.value = targetId
}

const scopes: { key: Scope; label: string; count: () => number }[] = [
  { key: 'all', label: '全部', count: () => codex.entries.length },
  { key: 'resident', label: '常驻上下文', count: () => codex.resident.length },
  { key: 'pending', label: '待确认', count: () => codex.pending.length },
  { key: 'conflict', label: '有冲突', count: () => codex.entries.filter((entry) => entry.conflicts > 0).length }
]

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
</script>

<template>
  <div class="wk-cols codex-layout">
    <aside class="wk-pane codex-categories" aria-label="设定分类">
      <div class="wk-head"><span>分类</span><span class="wk-head-push">{{ codex.entries.length }}</span></div>
      <button
        v-for="kind in kinds"
        :key="kind"
        class="wk-row"
        type="button"
        role="option"
        :aria-selected="scope === 'kind' && codex.kind === kind"
        @click="pickKind(kind)"
      >
        <span class="wk-row-name">{{ CODEX_KIND_LABEL[kind] }}</span>
        <span class="wk-row-meta">{{ codex.counts[kind] ?? 0 }}</span>
      </button>

      <div class="wk-group"><span>视图</span></div>
      <button
        v-for="item in scopes"
        :key="item.key"
        class="wk-row"
        type="button"
        role="option"
        :aria-selected="scope === item.key"
        @click="scope = item.key"
      >
        <span class="wk-row-name">{{ item.label }}</span>
        <span v-if="item.key === 'pending' && item.count()" class="pill pill-alert">{{ item.count() }}</span>
        <span v-else-if="item.key === 'conflict' && item.count()" class="pill pill-soft">{{ item.count() }}</span>
        <span v-else class="wk-row-meta">{{ item.count() }}</span>
      </button>

      <div class="wk-sec codex-budget">
        <div class="wk-label">第 1 层 · 常驻预算</div>
        <div class="bar" data-layer="resident" :data-over="residentTokens > 6000">
          <span :style="{ width: Math.min(100, (residentTokens / 6000) * 100) + '%' }" />
        </div>
        <p>{{ codex.resident.length }} 条常驻 · 约 {{ (residentTokens / 1000).toFixed(1) }}k / 6k</p>
      </div>
    </aside>

    <section class="wk-pane codex-list" aria-label="设定条目">
      <div class="wk-head">
        <span>{{ scope === 'kind' ? CODEX_KIND_LABEL[codex.kind] : scopes.find((item) => item.key === scope)?.label }}</span>
        <span class="wk-head-push">{{ rows.length }} 条</span>
      </div>
      <div class="codex-search">
        <input v-model="codex.query" class="wk-input" placeholder="搜索名称、动机、约束或关系" aria-label="搜索设定">
      </div>

      <button
        v-for="entry in rows"
        :key="entry.id"
        class="wk-row codex-list-row"
        type="button"
        role="option"
        :aria-selected="entry.id === selected?.id"
        @click="selectedId = entry.id"
      >
        <span class="codex-list-title">
          <strong>{{ entry.name }}</strong>
          <span v-if="entry.status === 'pending'" class="pill pill-alert">待确认</span>
          <span v-if="entry.resident" class="pill pill-soft">常驻</span>
          <span v-if="entry.conflicts" class="pill pill-alert">{{ entry.conflicts }} 冲突</span>
          <span class="wk-row-meta">{{ entry.refChapters.length }} 章</span>
        </span>
        <span class="codex-list-summary">{{ CODEX_KIND_LABEL[entry.kind] }} · {{ entry.summary }}</span>
      </button>

      <p v-if="!rows.length" class="codex-empty">
        没有匹配的条目。{{ codex.query ? '换个词，或清空搜索框。' : '这一类还没有条目。' }}
      </p>
    </section>

    <main class="wk-pane wk-pane-paper codex-detail" aria-label="设定详情">
      <template v-if="selected">
        <div class="paper-bar codex-detail-bar">
          <span class="codex-bar-name">{{ selected.name }}</span>
          <span class="pill">{{ CODEX_KIND_LABEL[selected.kind] }}</span>
          <span v-if="selected.resident" class="pill pill-soft">常驻上下文</span>
          <span v-if="selected.status === 'pending'" class="pill pill-alert">资料待确认</span>
        </div>

        <article class="codex-sheet">
          <section v-if="selected.status === 'pending'" class="codex-pending">
            <div>
              <strong>守卫从正文抽取到这个人物</strong>
              <p>目前只有原文能够确认的资料，空缺项会保留为“待补充”，不会被系统擅自推断。</p>
            </div>
            <div class="row">
              <button class="wk-btn" type="button" data-primary="true" @click="codex.confirm(selected.id)">确认入库</button>
              <button class="wk-btn" type="button" @click="codex.drop(selected.id)">忽略</button>
            </div>
          </section>

          <header class="codex-identity">
            <div class="wk-label">{{ selected.kind === 'character' ? '人物档案' : `${CODEX_KIND_LABEL[selected.kind]}档案` }}</div>
            <h2>{{ selected.name }}</h2>
            <p v-if="selected.aliases.length" class="codex-aliases">别名：{{ selected.aliases.join('、') }}</p>
            <p class="codex-lead">{{ selected.summary }}</p>
            <div v-if="selected.kind === 'character'" class="codex-character-meta">
              <span>{{ selected.character?.role ?? '角色定位待补充' }}</span>
              <span>{{ selected.character?.age ?? '年龄待补充' }}</span>
              <span v-for="trait in selected.character?.personality ?? []" :key="trait">{{ trait }}</span>
            </div>
          </header>

          <template v-if="selected.kind === 'character'">
            <section class="codex-section" aria-labelledby="character-core-title">
              <div class="codex-section-heading">
                <span>01</span>
                <div><h3 id="character-core-title">人物内核</h3><p>决定这个人为什么行动</p></div>
              </div>
              <dl class="codex-definition-grid">
                <div v-for="item in characterCore" :key="item.label">
                  <dt>{{ item.label }}</dt>
                  <dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd>
                </div>
              </dl>
            </section>

            <section class="codex-section" aria-labelledby="character-constraint-title">
              <div class="codex-section-heading">
                <span>02</span>
                <div><h3 id="character-constraint-title">写作约束</h3><p>生成正文时必须保持一致</p></div>
              </div>
              <dl class="codex-constraint-list">
                <div v-for="item in characterConstraints" :key="item.label">
                  <dt>{{ item.label }}</dt>
                  <dd :data-empty="!item.value">{{ item.value ?? '待补充' }}</dd>
                </div>
              </dl>
            </section>

            <section class="codex-section" aria-labelledby="character-arc-title">
              <div class="codex-section-heading">
                <span>03</span>
                <div><h3 id="character-arc-title">人物弧</h3><p>过去、当前与下一步</p></div>
              </div>
              <div class="character-arc">
                <div v-for="stage in characterArc" :key="stage.key" class="character-arc-step" :data-current="stage.key === 'current'">
                  <span class="character-arc-marker" />
                  <div><strong>{{ stage.label }}</strong><p :data-empty="!stage.value">{{ stage.value ?? '待补充' }}</p></div>
                </div>
              </div>
              <div class="codex-current-state">
                <span>当前落点</span>
                <p :data-empty="!selected.character?.currentState">{{ selected.character?.currentState ?? '待补充' }}</p>
              </div>
            </section>
          </template>

          <section v-else-if="selected.facts?.length" class="codex-section" aria-labelledby="codex-facts-title">
            <div class="codex-section-heading">
              <span>01</span>
              <div><h3 id="codex-facts-title">关键设定</h3><p>写作时不可违背的事实</p></div>
            </div>
            <dl class="codex-constraint-list">
              <div v-for="fact in selected.facts" :key="fact.label"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
            </dl>
          </section>

          <section v-if="selected.relations?.length" class="codex-section" aria-labelledby="codex-relations-title">
            <div class="codex-section-heading">
              <span>{{ selected.kind === 'character' ? '04' : '02' }}</span>
              <div><h3 id="codex-relations-title">关联设定</h3><p>点击跳到对应档案</p></div>
            </div>
            <div class="codex-relations">
              <button
                v-for="relation in selected.relations"
                :key="`${relation.name}-${relation.relation}`"
                type="button"
                :disabled="!relation.targetId"
                @click="selectRelation(relation.targetId)"
              >
                <span><strong>{{ relation.name }}</strong><small>{{ relation.relation }}</small></span>
                <p>{{ relation.note }}</p>
                <span v-if="relation.targetId" aria-hidden="true">→</span>
              </button>
            </div>
          </section>

          <section class="codex-section codex-evidence" aria-labelledby="codex-evidence-title">
            <div class="codex-section-heading">
              <span>{{ selected.kind === 'character' ? '05' : '03' }}</span>
              <div><h3 id="codex-evidence-title">正文依据</h3><p>资料来自哪些章节</p></div>
            </div>
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

      <p v-else class="codex-empty">左侧选一条设定。</p>
    </main>
  </div>
</template>
