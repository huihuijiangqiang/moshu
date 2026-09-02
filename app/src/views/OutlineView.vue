<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import type { Chapter } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import AppIcon from '@/components/ui/AppIcon.vue'

interface PlanDraft {
  title: string
  nodes: string[]
  note: string
}

const route = useRoute()
const router = useRouter()
const store = useProjectStore()
const shell = useShellStore()
const { toProject } = useProjectNavigation()
const view = ref<'grid' | 'list'>('grid')
const selectedVolumeId = ref<string | null>(null)
const selectedId = ref<string | null>(null)
const drafts = ref<Record<string, PlanDraft>>({})
const pendingDecision = ref(false)
const saving = ref(false)
const inserting = ref(false)
const saveNotice = ref('')
const saveError = ref('')

onMounted(() => shell.setCrumb('大纲'))

const currentVolume = computed(() =>
  store.byVolume.find((item) => item.volume.id === selectedVolumeId.value) ?? store.byVolume[0] ?? null
)
const selected = computed<Chapter | null>(
  () => currentVolume.value?.chapters.find((c) => c.id === selectedId.value) ?? currentVolume.value?.chapters.at(-1) ?? null
)
const currentDraft = computed(() => selected.value ? drafts.value[selected.value.id] ?? null : null)
const isDirty = computed(() => {
  const chapter = selected.value
  const draft = currentDraft.value
  if (!chapter || !draft) return false
  return draft.title !== chapter.title
    || draft.note !== chapter.outlineNote
    || JSON.stringify(draft.nodes) !== JSON.stringify(chapter.outline)
})

function draftFrom(chapter: Chapter): PlanDraft {
  return { title: chapter.title, nodes: [...chapter.outline], note: chapter.outlineNote }
}

watch(selected, (chapter) => {
  pendingDecision.value = false
  saveNotice.value = ''
  saveError.value = ''
  if (chapter && !drafts.value[chapter.id]) drafts.value[chapter.id] = draftFrom(chapter)
}, { immediate: true })

watch(
  [() => route.query.chapter, () => store.chapters.length, () => store.project?.id],
  ([chapterId]) => {
    const requestedId = typeof chapterId === 'string' ? chapterId : store.activeId
    const requested = requestedId ? store.chapters.find((chapter) => chapter.id === requestedId) : undefined
    const group = requested
      ? store.byVolume.find((item) => item.volume.id === requested.volumeId)
      : store.byVolume.find((item) => item.chapters.length > 0) ?? store.byVolume[0]
    if (!group) return
    selectedVolumeId.value = group.volume.id
    selectedId.value = requested?.volumeId === group.volume.id ? requested.id : group.chapters.at(-1)?.id ?? null
  },
  { immediate: true }
)

const pacing = computed(() => {
  const chapters = currentVolume.value?.chapters ?? []
  const planned = chapters.filter((chapter) => chapter.outline.length > 0).length
  const written = chapters.filter((chapter) => chapter.words > 0).length
  const needsRevision = chapters.filter((chapter) => chapter.bodyNeedsRevision).length
  const ratio = (value: number) => chapters.length ? Math.round((value / chapters.length) * 100) : 0
  return [
    { title: '章纲覆盖', body: `${planned} / ${chapters.length} 章已有章纲（${ratio(planned)}%）`, warn: planned < chapters.length },
    { title: '正文进度', body: `${written} / ${chapters.length} 章已有正文（${ratio(written)}%）`, warn: false },
    { title: '计划变更', body: needsRevision ? `${needsRevision} 章正文需要按新章纲调整` : '当前没有待调整正文', warn: needsRevision > 0 }
  ]
})

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

function writeChapter(chapter: Chapter) {
  router.push({ path: toProject('write'), query: { chapter: chapter.id } })
}

function selectChapter(id: string) {
  selectedId.value = id
}

function selectVolume(id: string) {
  selectedVolumeId.value = id
  const group = store.byVolume.find((item) => item.volume.id === id)
  selectedId.value = group?.chapters.at(-1)?.id ?? null
}

function addNode() {
  currentDraft.value?.nodes.push('')
  saveNotice.value = ''
}

function removeNode(index: number) {
  currentDraft.value?.nodes.splice(index, 1)
  saveNotice.value = ''
}

function moveNode(index: number, offset: number) {
  const nodes = currentDraft.value?.nodes
  if (!nodes) return
  const target = index + offset
  if (target < 0 || target >= nodes.length) return
  const [node] = nodes.splice(index, 1)
  if (node !== undefined) nodes.splice(target, 0, node)
  saveNotice.value = ''
}

function requestSave() {
  if (!selected.value || !currentDraft.value || !isDirty.value || saving.value) return
  saveError.value = ''
  if (selected.value.words > 0) {
    pendingDecision.value = true
    return
  }
  void savePlan(false)
}

async function savePlan(markBodyForRevision: boolean) {
  const chapter = selected.value
  const draft = currentDraft.value
  if (!chapter || !draft || saving.value) return
  saving.value = true
  saveError.value = ''
  try {
    const updated = await store.updateChapterPlan(chapter.id, {
      title: draft.title,
      outline: draft.nodes,
      outlineNote: draft.note,
      bodyNeedsRevision: markBodyForRevision || !!chapter.bodyNeedsRevision,
      baseRevision: chapter.outlineRevision ?? 0
    })
    drafts.value[chapter.id] = draftFrom(updated)
    pendingDecision.value = false
    saveNotice.value = markBodyForRevision ? '计划已更新，正文已标记为待调整' : '计划已更新，正文未改动'
  } catch (error) {
    saveError.value = error instanceof Error && error.message === 'outline_revision_conflict'
      ? '章纲已在其他位置更新，请刷新后再合并修改。'
      : '保存失败，当前修改仍保留在页面中。'
  } finally {
    saving.value = false
  }
}

async function insertChapter() {
  const volume = currentVolume.value
  if (!volume || inserting.value) return
  inserting.value = true
  try {
    const afterIndex = selected.value?.index ?? volume.chapters.at(-1)?.index ?? 0
    const chapter = await store.insertChapterAfter(volume.volume.id, afterIndex)
    selectedId.value = chapter.id
  } finally {
    inserting.value = false
  }
}
</script>

<template>
  <div class="wk-pane" :style="{ height: '100%', display: 'flex', flexDirection: 'column' }">
    <!-- 顶栏动作 Teleport 到外壳，本屏不再自带 header -->
    <Teleport defer to="#topbar-actions">
      <button
        v-for="v in (['grid', 'list'] as const)"
        :key="v"
        class="topbar-btn"
        type="button"
        :data-primary="view === v"
        @click="view = v"
      >{{ v === 'grid' ? '网格' : '列表' }}</button>
    </Teleport>

    <section class="rule-b outline-structure">
      <div class="kicker" :style="{ marginBottom: '16px' }">分卷结构</div>
      <div class="outline-volume-track" :style="{ gridTemplateColumns: `repeat(${Math.max(store.byVolume.length, 1)}, minmax(150px, 1fr))` }">
        <button
          v-for="item in store.byVolume"
          :key="item.volume.id"
          type="button"
          :aria-pressed="item.volume.id === currentVolume?.volume.id"
          @click="selectVolume(item.volume.id)"
        >
          <strong>第 {{ item.volume.index }} 卷</strong>
          <span>{{ item.volume.title }}</span>
          <small>{{ item.chapters.length }} 章 · {{ (item.chapters.reduce((sum, chapter) => sum + chapter.words, 0) / 10000).toFixed(1) }} 万字</small>
        </button>
      </div>
      <p>{{ currentVolume?.volume.summary || '本卷尚未填写卷纲，可先从章节计划开始。' }}</p>
    </section>

    <div class="app-body outline-workspace">
      <main class="pane" :style="{ padding: '24px', background: 'var(--color-neutral-100)' }">
        <div class="kicker" :style="{ marginBottom: '16px' }">
          第{{ currentVolume?.volume.index }}卷 · {{ currentVolume?.volume.title }} · {{ currentVolume?.chapters.length }} 章
        </div>

        <div v-if="view === 'grid'" class="grid-rule" :style="{ gridTemplateColumns: 'repeat(6, 1fr)' }">
          <button
            v-for="c in currentVolume?.chapters ?? []"
            :key="c.id"
            type="button"
            class="outline-chapter-cell"
            :data-chapter-id="c.id"
            :style="{ ...cellStyle(c), border: 0, textAlign: 'left', fontSize: '13px' }"
            @click="selectChapter(c.id)"
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
            <div v-if="c.bodyNeedsRevision" class="outline-revision-flag">正文待调整</div>
          </button>
          <button class="outline-insert" type="button" :disabled="inserting" @click="insertChapter">
            <AppIcon name="plus" />
            <span>{{ inserting ? '插入中…' : '在当前章后插入' }}</span>
          </button>
        </div>

        <div v-else class="grid-rule">
          <button
            v-for="c in currentVolume?.chapters ?? []"
            :key="c.id"
            type="button"
            class="row-between"
            :data-chapter-id="c.id"
            :style="{ border: 0, padding: '14px 16px', fontSize: '13px', cursor: 'pointer', textAlign: 'left' }"
            @click="selectChapter(c.id)"
          >
            <span><strong>{{ String(c.index).padStart(3, '0') }}</strong>　{{ c.title || '未命名' }}</span>
            <span :class="c.bodyNeedsRevision ? 'outline-list-warning' : 'muted'">
              {{ c.bodyNeedsRevision ? '正文待调整' : c.outlineNote || '无章纲' }}
            </span>
          </button>
          <button class="outline-list-insert" type="button" :disabled="inserting" @click="insertChapter">
            <AppIcon name="plus" />{{ inserting ? '插入中…' : '在当前章后插入新章' }}
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

      <aside class="pane pane-right outline-editor">
        <template v-if="selected && currentDraft">
          <div class="outline-editor-kicker">
            <span>第 {{ String(selected.index).padStart(3, '0') }} 章 · 章纲</span>
            <span v-if="selected.bodyNeedsRevision" class="outline-editor-state">正文待调整</span>
            <span v-else-if="selected.words > 0" class="outline-editor-state is-neutral">已有正文</span>
          </div>

          <label class="outline-field">
            <span>章节标题</span>
            <input v-model="currentDraft.title" type="text" maxlength="80" placeholder="未命名章节">
          </label>

          <section class="outline-node-editor" aria-labelledby="outline-node-title">
            <header>
              <span id="outline-node-title">章纲节点</span>
              <button type="button" @click="addNode"><AppIcon name="plus" />添加节点</button>
            </header>
            <div v-if="currentDraft.nodes.length" class="outline-node-list">
              <div v-for="(_, index) in currentDraft.nodes" :key="index" class="outline-node-row">
                <span>{{ String(index + 1).padStart(2, '0') }}</span>
                <input v-model="currentDraft.nodes[index]" type="text" :aria-label="`第 ${index + 1} 个章纲节点`" placeholder="这一段需要发生什么">
                <div class="outline-node-actions">
                  <button type="button" :disabled="index === 0" :aria-label="`上移第 ${index + 1} 个节点`" title="上移" @click="moveNode(index, -1)">
                    <AppIcon name="chevron" class="is-up" />
                  </button>
                  <button type="button" :disabled="index === currentDraft.nodes.length - 1" :aria-label="`下移第 ${index + 1} 个节点`" title="下移" @click="moveNode(index, 1)">
                    <AppIcon name="chevron" class="is-down" />
                  </button>
                  <button type="button" :aria-label="`删除第 ${index + 1} 个节点`" title="删除节点" @click="removeNode(index)">
                    <AppIcon name="close" />
                  </button>
                </div>
              </div>
            </div>
            <button v-else class="outline-empty-add" type="button" @click="addNode">添加第一个章纲节点</button>
          </section>

          <label class="outline-field">
            <span>本章补充说明</span>
            <textarea v-model="currentDraft.note" rows="3" placeholder="记录人物选择、伏笔或章末钩子" />
          </label>

          <section v-if="pendingDecision" class="outline-decision" aria-label="计划与正文确认">
            <strong>这章已经有正文</strong>
            <p>保存只会更新章纲，不会自动修改正文。请选择是否把正文标记为待调整。</p>
            <div>
              <button class="wk-btn" type="button" :disabled="saving" @click="savePlan(false)">仅更新计划</button>
              <button class="wk-btn" type="button" data-primary="true" :disabled="saving" @click="savePlan(true)">更新计划并标记正文</button>
              <button class="wk-btn" type="button" :disabled="saving" @click="pendingDecision = false">取消</button>
            </div>
          </section>

          <p v-if="saveError" class="outline-save-message is-error">{{ saveError }}</p>
          <p v-else-if="saveNotice" class="outline-save-message">{{ saveNotice }}</p>

          <div class="outline-editor-actions">
            <span>{{ isDirty ? '有未保存修改' : `计划版本 ${selected.outlineRevision ?? 0}` }}</span>
            <button class="wk-btn" type="button" :disabled="!isDirty || saving" data-primary="true" @click="requestSave">
              {{ saving ? '保存中…' : '保存章纲' }}
            </button>
          </div>

          <button class="outline-write-action" type="button" :disabled="isDirty" @click="writeChapter(selected)">
            {{ selected.words > 0 ? '打开正文' : '按此章纲开始写作' }}
          </button>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.outline-structure { flex: none; padding: 22px 24px; }
.outline-volume-track { display: grid; overflow-x: auto; border: var(--hair) solid var(--line-strong); }
.outline-volume-track button { min-width: 150px; min-height: 70px; display: grid; gap: 3px; padding: 10px 12px; border: 0; border-right: var(--hair) solid var(--line-strong); background: var(--paper); color: var(--ink); text-align: left; font: inherit; cursor: pointer; }
.outline-volume-track button:last-child { border-right: 0; }
.outline-volume-track button[aria-pressed="true"] { background: var(--primary-soft); box-shadow: inset 0 -3px 0 var(--primary); }
.outline-volume-track span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.outline-volume-track small { color: var(--ink-3); }
.outline-structure > p { margin: 11px 0 0; color: var(--ink-2); line-height: 1.55; font-size: var(--fs-sm); }
.outline-workspace { min-height: 0; grid-template-columns: minmax(0, 1fr) 390px; }
.outline-chapter-cell { position: relative; }
.outline-revision-flag { margin-top: 7px; color: var(--alert-ink); font-size: var(--fs-xs); font-weight: 700; }
.outline-insert { min-height: 92px; display: grid; place-items: center; align-content: center; gap: 7px; border: 2px dashed var(--line-strong); background: var(--paper); color: var(--ink-3); font: inherit; font-size: var(--fs-sm); cursor: pointer; }
.outline-insert:hover, .outline-list-insert:hover { border-color: var(--primary); color: var(--primary); }
.outline-insert:disabled, .outline-list-insert:disabled { cursor: wait; opacity: .55; }
.outline-list-warning { color: var(--alert-ink); font-weight: 700; }
.outline-list-insert { min-height: 48px; display: flex; align-items: center; justify-content: center; gap: var(--u2); border: 0; border-bottom: var(--hair) solid var(--line); background: var(--paper); color: var(--ink-3); font: inherit; cursor: pointer; }
.outline-editor { min-width: 0; padding: 22px 20px; overflow: auto; font-size: var(--fs-sm); }
.outline-editor-kicker { min-height: 24px; display: flex; align-items: center; justify-content: space-between; gap: var(--u2); margin-bottom: var(--u3); color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.outline-editor-state { padding: 2px 6px; border: var(--hair) solid var(--alert); color: var(--alert-ink); background: var(--alert-soft); }
.outline-editor-state.is-neutral { border-color: var(--line-strong); color: var(--ink-3); background: var(--panel); }
.outline-field { display: grid; gap: 7px; margin-bottom: var(--u4); }
.outline-field > span, .outline-node-editor header > span { color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.outline-field input, .outline-field textarea, .outline-node-row input { width: 100%; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink); font: inherit; }
.outline-field input { height: 40px; padding: 0 var(--u3); font-size: var(--fs-md); font-weight: 700; }
.outline-field textarea { min-height: 74px; padding: var(--u2) var(--u3); line-height: 1.65; resize: vertical; }
.outline-node-editor { margin-bottom: var(--u4); border-top: var(--hair) solid var(--line-strong); }
.outline-node-editor header { min-height: 42px; display: flex; align-items: center; justify-content: space-between; }
.outline-node-editor header button { display: flex; align-items: center; gap: 5px; border: 0; background: none; color: var(--primary); font: inherit; font-size: var(--fs-sm); font-weight: 700; cursor: pointer; }
.outline-node-list { border-top: var(--hair) solid var(--line); }
.outline-node-row { min-width: 0; display: grid; grid-template-columns: 24px minmax(0, 1fr) 76px; align-items: center; gap: 6px; min-height: 44px; border-bottom: var(--hair) solid var(--line); }
.outline-node-row > span { color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-xs); }
.outline-node-row input { min-width: 0; height: 34px; padding: 0 var(--u2); }
.outline-node-actions { width: 76px; display: grid; grid-template-columns: repeat(3, 24px); gap: 2px; }
.outline-node-actions button { width: 24px; height: 28px; display: grid; place-items: center; padding: 0; border: 0; background: none; color: var(--ink-3); cursor: pointer; }
.outline-node-actions button:hover:not(:disabled) { color: var(--primary); background: var(--primary-soft); }
.outline-node-actions button:disabled { opacity: .25; cursor: default; }
.outline-node-actions .is-up { transform: rotate(-90deg); }
.outline-node-actions .is-down { transform: rotate(90deg); }
.outline-empty-add { width: 100%; min-height: 54px; border: var(--hair) dashed var(--line-strong); background: var(--panel-sunken); color: var(--ink-3); font: inherit; cursor: pointer; }
.outline-decision { margin: var(--u4) -20px; padding: var(--u4) 20px; border-block: var(--hair) solid var(--alert); background: var(--alert-soft); }
.outline-decision strong { color: var(--alert-ink); }
.outline-decision p { margin: 6px 0 var(--u3); color: var(--ink-2); line-height: 1.6; }
.outline-decision > div { display: flex; flex-wrap: wrap; gap: 6px; }
.outline-save-message { margin: 0 0 var(--u3); color: var(--primary); line-height: 1.55; }
.outline-save-message.is-error { color: var(--alert-ink); }
.outline-editor-actions { min-height: 46px; display: flex; align-items: center; justify-content: space-between; gap: var(--u3); padding-top: var(--u3); border-top: var(--hair) solid var(--line-strong); }
.outline-editor-actions > span { color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-xs); }
.outline-editor-actions button:disabled { cursor: not-allowed; opacity: .45; }
.outline-write-action { width: 100%; height: 38px; margin-top: var(--u2); border: var(--hair) solid var(--line-strong); background: var(--paper); color: var(--ink); font: inherit; font-weight: 700; cursor: pointer; }
.outline-write-action:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }
.outline-write-action:disabled { cursor: not-allowed; opacity: .4; }
.outline-field input:focus-visible, .outline-field textarea:focus-visible, .outline-node-row input:focus-visible, .outline-node-actions button:focus-visible, .outline-write-action:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }

@media (max-width: 1100px) {
  .outline-workspace { grid-template-columns: minmax(0, 1fr) 350px; }
}

@media (max-width: 840px) {
  .outline-structure { padding: 18px 16px; }
  .outline-workspace { grid-template-columns: minmax(0, 1fr); overflow: auto; }
  .outline-editor { min-height: 520px; border-top: var(--hair) solid var(--line-strong); border-left: 0; }
}
</style>
