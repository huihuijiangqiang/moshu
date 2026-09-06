<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useShellStore } from '@/stores/shell'
import type { Chapter, ProjectTrash } from '@/types'
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
const codex = useCodexStore()
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
const povSaving = ref(false)
const povNotice = ref('')
const povError = ref('')
const structureBusy = ref(false)
const structureError = ref('')
const trashOpen = ref(false)
const trash = ref<ProjectTrash>({ volumes: [], chapters: [] })
const volumeEditor = ref<{ mode: 'create' | 'edit'; id?: string; title: string; summary: string } | null>(null)
const deleteVolumeId = ref<string | null>(null)
const targetVolumeId = ref('')

onMounted(() => shell.setCrumb('大纲'))

const currentVolume = computed(() =>
  store.byVolume.find((item) => item.volume.id === selectedVolumeId.value) ?? store.byVolume[0] ?? null
)
const selected = computed<Chapter | null>(
  () => currentVolume.value?.chapters.find((c) => c.id === selectedId.value) ?? currentVolume.value?.chapters.at(-1) ?? null
)
const currentDraft = computed(() => selected.value ? drafts.value[selected.value.id] ?? null : null)
const confirmedCharacters = computed(() => codex.entries
  .filter((entry) => entry.kind === 'character' && entry.status === 'confirmed')
  .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')))
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
  povNotice.value = ''
  povError.value = ''
  if (chapter && !drafts.value[chapter.id]) drafts.value[chapter.id] = draftFrom(chapter)
}, { immediate: true })

watch(() => store.loadedProjectId, (projectId) => {
  if (projectId) void codex.load(projectId)
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

function povName(entryId?: string) {
  return entryId ? codex.byId.get(entryId)?.name : undefined
}

async function changeChapterPov(event: Event) {
  const chapter = selected.value
  if (!chapter || povSaving.value) return
  const entryId = (event.target as HTMLSelectElement).value || undefined
  if (entryId === chapter.povEntryId) return
  povSaving.value = true
  povNotice.value = ''
  povError.value = ''
  try {
    await store.updateChapterPov(chapter.id, entryId)
    povNotice.value = entryId ? `本章视角已设为${povName(entryId) ?? '所选人物'}` : '已清除本章视角'
  } catch (error) {
    if (error instanceof Error && error.message === 'pov_revision_conflict') {
      await store.refreshStructure()
      povError.value = '本章视角已在其他位置更新，已载入最新选择，请重新设置。'
    } else {
      povError.value = '本章视角没有保存，请重试。'
    }
  } finally {
    povSaving.value = false
  }
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

function openVolumeEditor(mode: 'create' | 'edit') {
  if (mode === 'create') {
    volumeEditor.value = { mode, title: '', summary: '' }
    return
  }
  const volume = currentVolume.value?.volume
  if (volume) volumeEditor.value = { mode, id: volume.id, title: volume.title, summary: volume.summary ?? '' }
}

async function saveVolume() {
  const draft = volumeEditor.value
  if (!draft || !draft.title.trim() || structureBusy.value) return
  structureBusy.value = true
  structureError.value = ''
  try {
    if (draft.mode === 'create') await store.createVolume(draft.title, draft.summary)
    else await store.updateVolume(draft.id!, { title: draft.title, summary: draft.summary })
    const selectedVolume = draft.mode === 'create' ? store.project?.volumes.at(-1) : store.project?.volumes.find((item) => item.id === draft.id)
    selectedVolumeId.value = selectedVolume?.id ?? selectedVolumeId.value
    volumeEditor.value = null
  } catch {
    structureError.value = '卷信息没有保存，请检查标题后重试。'
  } finally {
    structureBusy.value = false
  }
}

async function moveCurrentVolume(offset: number) {
  const volumes = store.project?.volumes ?? []
  const currentId = currentVolume.value?.volume.id
  const index = volumes.findIndex((volume) => volume.id === currentId)
  const target = index + offset
  if (index < 0 || target < 0 || target >= volumes.length || structureBusy.value) return
  const ids = volumes.map((volume) => volume.id)
  ;[ids[index], ids[target]] = [ids[target]!, ids[index]!]
  structureBusy.value = true
  structureError.value = ''
  try {
    await store.reorderVolumes(ids)
  } catch {
    structureError.value = '分卷顺序没有更新，请重试。'
  } finally {
    structureBusy.value = false
  }
}

function requestTrashVolume() {
  const volume = currentVolume.value?.volume
  const alternatives = (store.project?.volumes ?? []).filter((item) => item.id !== volume?.id)
  if (!volume || !alternatives.length) {
    structureError.value = '作品至少需要保留一个卷。'
    return
  }
  deleteVolumeId.value = volume.id
  targetVolumeId.value = alternatives[0]!.id
}

async function confirmTrashVolume() {
  if (!deleteVolumeId.value || structureBusy.value) return
  const group = store.byVolume.find((item) => item.volume.id === deleteVolumeId.value)
  structureBusy.value = true
  structureError.value = ''
  try {
    await store.trashVolume(deleteVolumeId.value, group?.chapters.length ? targetVolumeId.value : undefined)
    selectedVolumeId.value = targetVolumeId.value
    selectedId.value = store.byVolume.find((item) => item.volume.id === targetVolumeId.value)?.chapters.at(-1)?.id ?? null
    deleteVolumeId.value = null
  } catch {
    structureError.value = '当前卷没有移入回收站，请确认接收章节的卷仍然存在。'
  } finally {
    structureBusy.value = false
  }
}

async function moveSelectedChapter(offset: number) {
  const chapter = selected.value
  const siblings = currentVolume.value?.chapters ?? []
  const index = siblings.findIndex((item) => item.id === chapter?.id)
  const target = index + offset
  if (!chapter || target < 0 || target >= siblings.length || structureBusy.value) return
  structureBusy.value = true
  structureError.value = ''
  try {
    if (offset < 0) {
      await store.moveChapter(chapter.id, chapter.volumeId, target === 0 ? 'first' : 'after', target === 0 ? undefined : siblings[target - 1]?.id)
    } else {
      await store.moveChapter(chapter.id, chapter.volumeId, 'after', siblings[target]?.id)
    }
  } catch {
    structureError.value = '章节顺序没有更新，请重试。'
  } finally {
    structureBusy.value = false
  }
}

async function changeChapterVolume(event: Event) {
  const chapter = selected.value
  const volumeId = (event.target as HTMLSelectElement).value
  if (!chapter || volumeId === chapter.volumeId || structureBusy.value) return
  structureBusy.value = true
  structureError.value = ''
  try {
    await store.moveChapter(chapter.id, volumeId, 'last')
    selectedVolumeId.value = volumeId
  } catch {
    structureError.value = '章节没有移动，请重试。'
  } finally {
    structureBusy.value = false
  }
}

async function trashSelectedChapter() {
  const chapter = selected.value
  if (!chapter || structureBusy.value || !window.confirm(`将“${chapter.title || '未命名章节'}”移入回收站？`)) return
  const siblings = currentVolume.value?.chapters ?? []
  const fallback = siblings[siblings.indexOf(chapter) - 1] ?? siblings[siblings.indexOf(chapter) + 1]
  structureBusy.value = true
  structureError.value = ''
  try {
    await store.trashChapter(chapter.id)
    delete drafts.value[chapter.id]
    selectedId.value = fallback?.id ?? store.chapters.at(-1)?.id ?? null
  } catch {
    structureError.value = store.chapters.length === 1 ? '作品至少需要保留一个章节。' : '章节没有移入回收站，请重试。'
  } finally {
    structureBusy.value = false
  }
}

async function showTrash() {
  trashOpen.value = true
  structureError.value = ''
  try { trash.value = await store.getTrash() } catch { structureError.value = '回收站加载失败，请重试。' }
}

async function restoreItem(kind: 'volumes' | 'chapters', id: string) {
  structureBusy.value = true
  try {
    await store.restoreTrash(kind, id)
    trash.value = await store.getTrash()
  } catch {
    structureError.value = '内容没有恢复，请重试。'
  } finally {
    structureBusy.value = false
  }
}

async function removeTrashItem(kind: 'volumes' | 'chapters', id: string, title: string) {
  if (!window.confirm(`永久删除“${title}”？此操作无法撤销。`)) return
  structureBusy.value = true
  try {
    await store.deleteTrash(kind, id)
    trash.value = await store.getTrash()
  } catch {
    structureError.value = kind === 'volumes' ? '卷内仍有关联章节，请先恢复或永久删除这些章节。' : '内容没有永久删除，请重试。'
  } finally {
    structureBusy.value = false
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
      <button class="topbar-btn" type="button" @click="openVolumeEditor('create')"><AppIcon name="plus" />新建卷</button>
      <button class="topbar-btn" type="button" @click="showTrash"><AppIcon name="trash" />回收站</button>
    </Teleport>

    <section class="rule-b outline-structure">
      <div class="kicker" :style="{ marginBottom: '16px' }">分卷结构</div>
      <div class="outline-volume-track" :style="{ gridTemplateColumns: `repeat(${Math.max(store.byVolume.length, 1)}, minmax(190px, 1fr))` }">
        <div
          v-for="item in store.byVolume"
          :key="item.volume.id"
          class="outline-volume-slot"
          :data-active="item.volume.id === currentVolume?.volume.id"
        >
          <button type="button" :aria-pressed="item.volume.id === currentVolume?.volume.id" @click="selectVolume(item.volume.id)">
            <strong>第 {{ item.volume.index }} 卷</strong>
            <span>{{ item.volume.title }}</span>
            <small>{{ item.chapters.length }} 章 · {{ (item.chapters.reduce((sum, chapter) => sum + chapter.words, 0) / 10000).toFixed(1) }} 万字</small>
          </button>
          <div v-if="item.volume.id === currentVolume?.volume.id" class="outline-volume-actions">
            <button type="button" title="编辑当前卷" aria-label="编辑当前卷" @click="openVolumeEditor('edit')"><AppIcon name="edit" :size="14" /></button>
            <button type="button" title="前移当前卷" aria-label="前移当前卷" :disabled="item.volume.index === 1 || structureBusy" @click="moveCurrentVolume(-1)"><AppIcon name="chevron" class="is-left" :size="14" /></button>
            <button type="button" title="后移当前卷" aria-label="后移当前卷" :disabled="item.volume.index === store.byVolume.length || structureBusy" @click="moveCurrentVolume(1)"><AppIcon name="chevron" :size="14" /></button>
            <button type="button" title="将当前卷移入回收站" aria-label="将当前卷移入回收站" @click="requestTrashVolume"><AppIcon name="trash" :size="14" /></button>
          </div>
        </div>
      </div>
      <p>{{ currentVolume?.volume.summary || '本卷尚未填写卷纲，可先从章节计划开始。' }}</p>
      <p v-if="structureError" class="outline-structure-error" role="alert">{{ structureError }}</p>
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
            <div v-if="povName(c.povEntryId)" class="outline-pov-flag">视角 · {{ povName(c.povEntryId) }}</div>
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
            <span class="outline-list-meta">
              <small v-if="povName(c.povEntryId)">视角 · {{ povName(c.povEntryId) }}</small>
              <span :class="c.bodyNeedsRevision ? 'outline-list-warning' : 'muted'">
                {{ c.bodyNeedsRevision ? '正文待调整' : c.outlineNote || '无章纲' }}
              </span>
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

          <div class="outline-chapter-tools" aria-label="章节结构操作">
            <button type="button" title="上移章节" aria-label="上移章节" :disabled="(currentVolume?.chapters.findIndex((item) => item.id === selected?.id) ?? 0) <= 0 || structureBusy" @click="moveSelectedChapter(-1)"><AppIcon name="chevron" class="is-up" /></button>
            <button type="button" title="下移章节" aria-label="下移章节" :disabled="(currentVolume?.chapters.findIndex((item) => item.id === selected?.id) ?? 0) >= (currentVolume?.chapters.length ?? 1) - 1 || structureBusy" @click="moveSelectedChapter(1)"><AppIcon name="chevron" class="is-down" /></button>
            <label><span>所属卷</span><select :value="selected.volumeId" :disabled="structureBusy" @change="changeChapterVolume"><option v-for="volume in store.project?.volumes" :key="volume.id" :value="volume.id">第 {{ volume.index }} 卷 · {{ volume.title }}</option></select></label>
            <button class="is-danger" type="button" title="移入回收站" aria-label="将章节移入回收站" :disabled="structureBusy" @click="trashSelectedChapter"><AppIcon name="trash" /></button>
          </div>

          <label class="outline-field">
            <span>章节标题</span>
            <input v-model="currentDraft.title" type="text" maxlength="80" placeholder="未命名章节">
          </label>

          <label class="outline-field outline-pov-field">
            <span>本章叙事视角</span>
            <select :value="selected.povEntryId ?? ''" :disabled="povSaving || !confirmedCharacters.length" @change="changeChapterPov">
              <option value="">未指定</option>
              <option v-for="character in confirmedCharacters" :key="character.id" :value="character.id">{{ character.name }}{{ character.character?.role ? ` · ${character.character.role}` : '' }}</option>
            </select>
            <small v-if="!confirmedCharacters.length">先在设定库确认人物，再为章节指定视角。</small>
            <small v-else-if="povSaving">正在保存视角…</small>
            <small v-else-if="povError" class="is-error" role="alert">{{ povError }}</small>
            <small v-else-if="povNotice" class="is-success" role="status">{{ povNotice }}</small>
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

    <div v-if="volumeEditor" class="outline-dialog-backdrop" @click.self="volumeEditor = null">
      <section class="outline-dialog" role="dialog" aria-modal="true" aria-labelledby="volume-editor-title">
        <header><div><span>分卷结构</span><h2 id="volume-editor-title">{{ volumeEditor.mode === 'create' ? '新建卷' : '编辑当前卷' }}</h2></div><button type="button" aria-label="关闭" @click="volumeEditor = null">×</button></header>
        <label><span>卷名</span><input v-model="volumeEditor.title" maxlength="200" placeholder="例如：第一卷 · 落脚"></label>
        <label><span>卷纲摘要</span><textarea v-model="volumeEditor.summary" rows="5" maxlength="20000" placeholder="记录本卷目标、转折与收束"></textarea></label>
        <footer><span>卷纲会进入长文本上下文。</span><div><button class="wk-btn" type="button" @click="volumeEditor = null">取消</button><button class="wk-btn" data-primary="true" type="button" :disabled="!volumeEditor.title.trim() || structureBusy" @click="saveVolume">{{ structureBusy ? '保存中…' : '保存' }}</button></div></footer>
      </section>
    </div>

    <div v-if="deleteVolumeId" class="outline-dialog-backdrop" @click.self="deleteVolumeId = null">
      <section class="outline-confirm" role="alertdialog" aria-modal="true" aria-labelledby="delete-volume-title">
        <span>移入回收站</span><h2 id="delete-volume-title">删除当前卷</h2>
        <p v-if="currentVolume?.chapters.length">当前卷有 {{ currentVolume.chapters.length }} 个章节。选择一个卷接收这些章节后再删除。</p>
        <p v-else>当前卷没有章节，可直接移入回收站。</p>
        <label v-if="currentVolume?.chapters.length"><span>章节移至</span><select v-model="targetVolumeId"><option v-for="volume in store.project?.volumes.filter((item) => item.id !== deleteVolumeId)" :key="volume.id" :value="volume.id">第 {{ volume.index }} 卷 · {{ volume.title }}</option></select></label>
        <div><button class="wk-btn" type="button" @click="deleteVolumeId = null">取消</button><button class="wk-btn is-danger" type="button" :disabled="structureBusy" @click="confirmTrashVolume">移入回收站</button></div>
      </section>
    </div>

    <div v-if="trashOpen" class="outline-trash-backdrop" @click.self="trashOpen = false">
      <aside class="outline-trash" aria-label="作品回收站">
        <header><div><span>作品结构</span><h2>回收站</h2></div><button type="button" aria-label="关闭回收站" @click="trashOpen = false"><AppIcon name="close" /></button></header>
        <div class="outline-trash-body">
          <section><h3>章节 <small>{{ trash.chapters.length }}</small></h3><p v-if="!trash.chapters.length" class="outline-trash-empty">没有已删除章节</p><article v-for="item in trash.chapters" :key="item.id"><div><strong>{{ item.title || '未命名章节' }}</strong><small>{{ item.volumeTitle || '原卷已删除' }} · {{ item.words.toLocaleString() }} 字</small></div><div><button type="button" title="恢复章节" @click="restoreItem('chapters', item.id)"><AppIcon name="restore" /></button><button type="button" title="永久删除章节" @click="removeTrashItem('chapters', item.id, item.title)"><AppIcon name="trash" /></button></div></article></section>
          <section><h3>分卷 <small>{{ trash.volumes.length }}</small></h3><p v-if="!trash.volumes.length" class="outline-trash-empty">没有已删除分卷</p><article v-for="item in trash.volumes" :key="item.id"><div><strong>{{ item.title }}</strong><small>删除于 {{ new Date(item.deletedAt).toLocaleString('zh-CN') }}</small></div><div><button type="button" title="恢复分卷" @click="restoreItem('volumes', item.id)"><AppIcon name="restore" /></button><button type="button" title="永久删除分卷" @click="removeTrashItem('volumes', item.id, item.title)"><AppIcon name="trash" /></button></div></article></section>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.outline-structure { flex: none; padding: 22px 24px; }
.outline-volume-track { display: grid; overflow-x: auto; border: var(--hair) solid var(--line-strong); }
.outline-volume-slot { position: relative; min-width: 190px; border-right: var(--hair) solid var(--line-strong); background: var(--paper); }
.outline-volume-slot:last-child { border-right: 0; }
.outline-volume-slot > button { width: 100%; min-height: 88px; display: grid; align-content: center; gap: 3px; padding: 10px 42px 10px 12px; border: 0; background: transparent; color: var(--ink); text-align: left; font: inherit; cursor: pointer; }
.outline-volume-slot[data-active="true"] { background: var(--primary-soft); box-shadow: inset 0 -3px 0 var(--primary); }
.outline-volume-slot span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.outline-volume-slot small { color: var(--ink-3); }
.outline-volume-actions { position: absolute; top: 7px; right: 7px; display: grid; gap: 2px; }
.outline-volume-actions button { width: 26px; height: 17px; display: grid; place-items: center; padding: 0; border: 0; background: transparent; color: var(--ink-3); cursor: pointer; }
.outline-volume-actions button:hover:not(:disabled) { color: var(--primary); background: var(--paper); }
.outline-volume-actions button:disabled { opacity: .22; cursor: default; }
.outline-volume-actions .is-left { transform: rotate(180deg); }
.outline-structure > p { margin: 11px 0 0; color: var(--ink-2); line-height: 1.55; font-size: var(--fs-sm); }
.outline-structure > .outline-structure-error { color: var(--alert-ink); font-weight: 700; }
.outline-workspace { min-height: 0; grid-template-columns: minmax(0, 1fr) 390px; }
.outline-chapter-cell { position: relative; }
.outline-revision-flag { margin-top: 7px; color: var(--alert-ink); font-size: var(--fs-xs); font-weight: 700; }
.outline-pov-flag { margin-top: 5px; overflow: hidden; color: var(--primary); font-size: var(--fs-xs); font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.outline-list-meta { display: flex; align-items: center; justify-content: flex-end; gap: var(--u3); }
.outline-list-meta small { color: var(--primary); font-size: var(--fs-xs); font-weight: 700; }
.outline-insert { min-height: 92px; display: grid; place-items: center; align-content: center; gap: 7px; border: 2px dashed var(--line-strong); background: var(--paper); color: var(--ink-3); font: inherit; font-size: var(--fs-sm); cursor: pointer; }
.outline-insert:hover, .outline-list-insert:hover { border-color: var(--primary); color: var(--primary); }
.outline-insert:disabled, .outline-list-insert:disabled { cursor: wait; opacity: .55; }
.outline-list-warning { color: var(--alert-ink); font-weight: 700; }
.outline-list-insert { min-height: 48px; display: flex; align-items: center; justify-content: center; gap: var(--u2); border: 0; border-bottom: var(--hair) solid var(--line); background: var(--paper); color: var(--ink-3); font: inherit; cursor: pointer; }
.outline-editor { min-width: 0; padding: 22px 20px; overflow: auto; font-size: var(--fs-sm); }
.outline-editor-kicker { min-height: 24px; display: flex; align-items: center; justify-content: space-between; gap: var(--u2); margin-bottom: var(--u3); color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.outline-editor-state { padding: 2px 6px; border: var(--hair) solid var(--alert); color: var(--alert-ink); background: var(--alert-soft); }
.outline-editor-state.is-neutral { border-color: var(--line-strong); color: var(--ink-3); background: var(--panel); }
.outline-chapter-tools { min-height: 38px; display: grid; grid-template-columns: 30px 30px minmax(0, 1fr) 30px; align-items: center; gap: 3px; margin: 0 0 var(--u4); border-block: var(--hair) solid var(--line); }
.outline-chapter-tools > button { width: 30px; height: 30px; display: grid; place-items: center; padding: 0; border: 0; background: transparent; color: var(--ink-3); cursor: pointer; }
.outline-chapter-tools > button:hover:not(:disabled) { color: var(--primary); background: var(--primary-soft); }
.outline-chapter-tools > button.is-danger:hover:not(:disabled) { color: var(--alert-ink); background: var(--alert-soft); }
.outline-chapter-tools > button:disabled { opacity: .25; cursor: default; }
.outline-chapter-tools .is-up { transform: rotate(-90deg); }
.outline-chapter-tools .is-down { transform: rotate(90deg); }
.outline-chapter-tools label { min-width: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 7px; padding-inline: 7px; border-inline: var(--hair) solid var(--line); color: var(--ink-4); font-size: var(--fs-xs); }
.outline-chapter-tools select { min-width: 0; height: 30px; border: 0; background: transparent; color: var(--ink-2); font: inherit; }
.outline-field { display: grid; gap: 7px; margin-bottom: var(--u4); }
.outline-field > span, .outline-node-editor header > span { color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.outline-field input, .outline-field textarea, .outline-node-row input { width: 100%; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink); font: inherit; }
.outline-field select { width: 100%; height: 38px; padding: 0 var(--u3); border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink); font: inherit; }
.outline-pov-field small { min-height: 16px; color: var(--ink-4); font-size: var(--fs-xs); line-height: 1.45; }
.outline-pov-field small.is-success { color: var(--primary); }
.outline-pov-field small.is-error { color: var(--alert-ink); }
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
.outline-field input:focus-visible, .outline-field textarea:focus-visible, .outline-field select:focus-visible, .outline-node-row input:focus-visible, .outline-node-actions button:focus-visible, .outline-write-action:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.outline-dialog-backdrop, .outline-trash-backdrop { position: fixed; inset: 0; z-index: 80; background: rgb(20 24 25 / 55%); }
.outline-dialog-backdrop { display: grid; place-items: center; padding: 18px; }
.outline-dialog { width: min(560px, 100%); overflow: hidden; border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--paper); box-shadow: 0 18px 56px rgb(0 0 0 / 24%); }
.outline-dialog > header, .outline-trash > header { min-height: 72px; display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 2px solid var(--ink); }
.outline-dialog header span, .outline-confirm > span, .outline-trash header span { color: var(--ink-4); font: 9px/1 var(--font-mono); }
.outline-dialog h2, .outline-confirm h2, .outline-trash h2 { margin: 5px 0 0; font-size: 20px; }
.outline-dialog > header > button, .outline-trash > header > button { width: 30px; height: 30px; display: grid; place-items: center; border: 0; background: transparent; color: var(--ink-2); font-size: 22px; cursor: pointer; }
.outline-dialog > label { display: grid; gap: 7px; margin: 18px 20px; color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.outline-dialog input, .outline-dialog textarea, .outline-confirm select { width: 100%; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink); font: inherit; }
.outline-dialog input { height: 40px; padding: 0 11px; }
.outline-dialog textarea { min-height: 104px; padding: 10px 11px; line-height: 1.6; resize: vertical; }
.outline-dialog footer { min-height: 60px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 20px; border-top: var(--hair) solid var(--line); color: var(--ink-4); font-size: var(--fs-xs); }
.outline-dialog footer > div, .outline-confirm > div { display: flex; justify-content: flex-end; gap: 7px; }
.outline-confirm { width: min(430px, 100%); padding: 24px; border-top: 3px solid var(--alert); border-radius: 4px; background: var(--paper); box-shadow: 0 18px 56px rgb(0 0 0 / 24%); }
.outline-confirm p { margin: 14px 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.65; }
.outline-confirm label { display: grid; gap: 6px; margin-bottom: 20px; color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.outline-confirm select { height: 38px; padding: 0 9px; }
.outline-confirm .is-danger { border-color: var(--alert); background: var(--alert-soft); color: var(--alert-ink); }
.outline-trash-backdrop { display: flex; justify-content: flex-end; }
.outline-trash { width: min(480px, 100%); height: 100%; display: grid; grid-template-rows: auto minmax(0, 1fr); background: var(--paper); box-shadow: -18px 0 48px rgb(0 0 0 / 20%); }
.outline-trash-body { overflow: auto; padding: 20px; }
.outline-trash-body section + section { margin-top: 28px; }
.outline-trash-body h3 { display: flex; justify-content: space-between; margin: 0 0 8px; padding-bottom: 8px; border-bottom: var(--hair) solid var(--line-strong); font-size: var(--fs-md); }
.outline-trash-body h3 small { color: var(--ink-4); font-family: var(--font-mono); }
.outline-trash-body article { min-height: 62px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: var(--hair) solid var(--line); }
.outline-trash-body article > div:first-child { min-width: 0; display: grid; gap: 5px; }
.outline-trash-body article strong, .outline-trash-body article small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.outline-trash-body article small, .outline-trash-empty { color: var(--ink-4); font-size: var(--fs-xs); }
.outline-trash-body article > div:last-child { display: flex; flex: none; }
.outline-trash-body article button { width: 30px; height: 30px; display: grid; place-items: center; border: 0; background: transparent; color: var(--ink-3); cursor: pointer; }
.outline-trash-body article button:hover { color: var(--primary); background: var(--primary-soft); }
.outline-trash-empty { margin: 18px 0; }

@media (max-width: 1100px) {
  .outline-workspace { grid-template-columns: minmax(0, 1fr) 350px; }
}

@media (max-width: 840px) {
  .outline-structure { padding: 18px 16px; }
  .outline-workspace { grid-template-columns: minmax(0, 1fr); overflow: auto; }
  .outline-editor { min-height: 520px; border-top: var(--hair) solid var(--line-strong); border-left: 0; }
  .outline-dialog-backdrop { padding: 0; place-items: end stretch; }
  .outline-dialog { width: 100%; border-radius: 0; }
  .outline-dialog footer { align-items: flex-start; flex-direction: column; }
  .outline-dialog footer > div { align-self: stretch; }
  .outline-dialog footer .wk-btn { flex: 1; }
}
</style>
