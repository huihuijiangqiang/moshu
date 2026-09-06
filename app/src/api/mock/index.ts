import { delay } from '../http'
import * as seed from './seed'
import { findShelfBook } from './shelf'
import type { Chapter, ChapterPlanPatch, ChapterVersionDetail, ChapterVersionRestoreResult, ChapterVersionSummary, CodexEntry, CodexEntryDraft, GuardIssue, GuardOverview, GuardResolutionAction, Project, ContextLayer, ProjectPatch, ProjectTrash, TimelineReflowResult } from '@/types'

/** 内存态副本：mock 下的写操作要真的改变数据，否则界面行为是假的。 */
const state = {
  project: structuredClone(seed.project) as Project,
  chapters: structuredClone(seed.chapters) as Chapter[],
  codex: structuredClone(seed.codex) as CodexEntry[],
  issues: structuredClone(seed.guardIssues) as GuardIssue[]
}

const projectDrafts = new Map<string, Chapter[]>()
const projectStates = new Map<string, Project>()
const trashStates = new Map<string, ProjectTrash>()
const chapterVersionStates = new Map<string, ChapterVersionDetail[]>()

function projectFor(id: string): Project {
  if (id === seed.project.id) return state.project
  const existing = projectStates.get(id)
  if (existing) return existing
  const book = findShelfBook(id)
  const project: Project = {
    id,
    title: book?.title ?? '未命名作品',
    genre: book?.genre ?? null,
    status: book?.status === 'finished' ? 'finished' : 'ongoing',
    wordCount: book?.words ?? 0,
    chapterCount: book?.chapters ?? 0,
    dailyGoal: 3000,
    dailyWords: book?.todayWords ?? 0,
    styleProfile: null,
    volumes: [{ id: `${id}-v1`, index: 1, title: book?.status === 'planning' ? '故事构思' : '第一卷' }]
  }
  projectStates.set(id, project)
  return project
}

function trashFor(id: string): ProjectTrash {
  const existing = trashStates.get(id)
  if (existing) return existing
  const trash: ProjectTrash = { volumes: [], chapters: [] }
  trashStates.set(id, trash)
  return trash
}

function renumberChapters(rows: Chapter[]) {
  rows.sort((a, b) => a.index - b.index).forEach((chapter, index) => { chapter.index = index + 1 })
}

function chaptersFor(id: string): Chapter[] {
  if (id === seed.project.id) return state.chapters
  const existing = projectDrafts.get(id)
  if (existing) return existing

  const book = findShelfBook(id)
  const chapterTitle: Record<string, string> = {
    p2: '无人知晓',
    p3: '纸船灯影',
    p5: '失重边界'
  }
  const chapterCount = Math.max(1, book?.chapters ?? 1)
  const averageWords = book?.words ? Math.floor(book.words / chapterCount) : 0
  const rows: Chapter[] = book?.status === 'planning'
    ? [{ id: `${id}-ch1`, volumeId: `${id}-v1`, index: 1, title: '开篇', words: 0, status: 'outlined', outline: ['确定开篇人物', '建立核心冲突', '留下第一处悬念'], outlineNote: '当前处于构思阶段，确认章纲后再进入正文写作。' }]
    : Array.from({ length: chapterCount }, (_, index): Chapter => {
        const isLatest = index === chapterCount - 1
        const words = isLatest
          ? Math.max(0, (book?.words ?? 0) - averageWords * (chapterCount - 1))
          : averageWords
        return {
          id: `${id}-ch${index + 1}`,
          volumeId: `${id}-v1`,
          index: index + 1,
          title: isLatest ? (chapterTitle[id] ?? '最新章节') : `第 ${index + 1} 章`,
          words,
          status: isLatest && book?.status !== 'finished' ? 'drafting' : 'done',
          outline: [],
          outlineNote: '',
          content: isLatest ? `<h1>第${chapterCount}章　${chapterTitle[id] ?? '最新章节'}</h1><p>这部作品的演示正文尚未接入完整数据。你可以在这里继续梳理章节与设定。</p>` : undefined
        }
      })
  projectDrafts.set(id, rows)
  return rows
}

function findChapter(id: string): Chapter | undefined {
  return [...state.chapters, ...projectDrafts.values()].flat().find((chapter) => chapter.id === id)
}

function plainText(content = ''): string {
  return new DOMParser().parseFromString(content, 'text/html').body.textContent?.trim() ?? ''
}

function versionsFor(id: string): ChapterVersionDetail[] {
  const existing = chapterVersionStates.get(id)
  if (existing) return existing
  const chapter = findChapter(id)
  const text = plainText(chapter?.content)
  const versions: ChapterVersionDetail[] = chapter?.content === undefined ? [] : [{
    id: 1,
    rev: chapter.rev ?? 1,
    trigger: 'manual',
    words: text.length,
    excerpt: text.slice(0, 140) || '空白正文',
    createdAt: new Date(Date.now() - 15 * 60_000).toISOString(),
    isCurrent: true,
    content: chapter.content,
    contentJson: {}
  }]
  if (chapter && versions.length) chapter.rev = versions[0]!.rev
  chapterVersionStates.set(id, versions)
  return versions
}

function addMockVersion(chapter: Chapter, trigger: string): ChapterVersionDetail {
  const rows = versionsFor(chapter.id)
  rows.forEach((version) => { version.isCurrent = false })
  const text = plainText(chapter.content)
  const version: ChapterVersionDetail = {
    id: Math.max(0, ...rows.map((item) => item.id)) + 1,
    rev: chapter.rev ?? 1,
    trigger,
    words: text.length,
    excerpt: text.length > 140 ? `${text.slice(0, 140)}…` : text || '空白正文',
    createdAt: new Date().toISOString(),
    isCurrent: true,
    content: chapter.content ?? '',
    contentJson: {}
  }
  rows.unshift(version)
  return version
}

export const mockApi = {
  async getProject(projectId = 'p1'): Promise<Project> {
    await delay()
    return structuredClone(projectFor(projectId))
  },

  /** 列表不带正文——80 万字作品靠这个保证首屏 < 2s */
  async listChapters(projectId = 'p1'): Promise<Chapter[]> {
    await delay()
    return chaptersFor(projectId).map(({ content, ...rest }) => ({ ...rest }))
  },

  async getChapter(id: string): Promise<Chapter | undefined> {
    await delay(180)
    const c = findChapter(id)
    versionsFor(id)
    return c ? structuredClone(c) : undefined
  },

  async listChapterVersions(id: string): Promise<ChapterVersionSummary[]> {
    await delay(120)
    return structuredClone(versionsFor(id).map(({ content, contentJson, ...summary }) => summary))
  },

  async getChapterVersion(id: string, rev: number): Promise<ChapterVersionDetail> {
    await delay(100)
    const version = versionsFor(id).find((item) => item.rev === rev)
    if (!version) throw new Error('chapter_version_not_found')
    return structuredClone(version)
  },

  async restoreChapterVersion(id: string, rev: number): Promise<ChapterVersionRestoreResult> {
    await delay(140)
    const chapter = findChapter(id)
    const selected = versionsFor(id).find((item) => item.rev === rev)
    if (!chapter || !selected) throw new Error('chapter_version_not_found')
    chapter.content = selected.content
    chapter.words = selected.words
    chapter.rev = (chapter.rev ?? 0) + 1
    addMockVersion(chapter, 'restore_version')
    return {
      rev: chapter.rev,
      restoredFromRev: rev,
      content: chapter.content,
      contentJson: selected.contentJson,
      words: chapter.words,
      consistencyStatus: 'queued'
    }
  },

  async saveChapter(id: string, patch: Partial<Chapter>): Promise<{ rev: number }> {
    await delay(120)
    const c = findChapter(id)
    if (!c) return { rev: 0 }
    versionsFor(id)
    Object.assign(c, patch)
    c.rev = (c.rev ?? 0) + 1
    addMockVersion(c, 'manual')
    return { rev: c.rev }
  },

  async updateChapterPlan(id: string, patch: ChapterPlanPatch): Promise<Chapter | undefined> {
    await delay(160)
    const c = [...state.chapters, ...projectDrafts.values()].flat().find((x) => x.id === id)
    if (!c) return undefined
    const revision = c.outlineRevision ?? 0
    if (patch.baseRevision !== revision) throw new Error('outline_revision_conflict')
    Object.assign(c, {
      title: patch.title.trim(),
      outline: patch.outline.map((node) => node.trim()).filter(Boolean),
      outlineNote: patch.outlineNote.trim(),
      bodyNeedsRevision: patch.bodyNeedsRevision,
      outlineRevision: revision + 1,
      outlineUpdatedAt: new Date().toISOString()
    })
    return structuredClone(c)
  },

  async insertChapter(projectId: string, volumeId: string, afterIndex: number): Promise<Chapter> {
    await delay(160)
    const rows = chaptersFor(projectId)
    rows
      .filter((chapter) => chapter.volumeId === volumeId && chapter.index > afterIndex)
      .forEach((chapter) => { chapter.index += 1 })
    const chapter: Chapter = {
      id: `${projectId}-ch-${Date.now().toString(36)}`,
      volumeId,
      index: afterIndex + 1,
      title: '',
      words: 0,
      status: 'outlined',
      outline: [],
      outlineNote: '',
      outlineRevision: 0,
      bodyNeedsRevision: false
    }
    rows.push(chapter)
    if (projectId === state.project.id && state.project.chapterCount !== undefined) state.project.chapterCount += 1
    return structuredClone(chapter)
  },

  async updateProject(projectId: string, patch: ProjectPatch): Promise<Project> {
    await delay(120)
    const project = projectFor(projectId)
    if (patch.title !== undefined) project.title = patch.title.trim()
    if (patch.genre !== undefined) project.genre = patch.genre
    if (patch.status !== undefined) project.status = patch.status
    if (patch.dailyGoal !== undefined) project.dailyGoal = patch.dailyGoal
    return structuredClone(project)
  },

  async createVolume(projectId: string, title: string, summary = '') {
    await delay(120)
    const project = projectFor(projectId)
    const volume = { id: `${projectId}-v-${Date.now().toString(36)}`, index: project.volumes.length + 1, title: title.trim(), summary: summary.trim() || undefined }
    project.volumes.push(volume)
    return structuredClone(volume)
  },

  async updateVolume(projectId: string, volumeId: string, patch: { title?: string; summary?: string }): Promise<void> {
    await delay(120)
    const volume = projectFor(projectId).volumes.find((item) => item.id === volumeId)
    if (!volume) throw new Error('volume_not_found')
    if (patch.title !== undefined) volume.title = patch.title.trim()
    if (patch.summary !== undefined) volume.summary = patch.summary.trim() || undefined
  },

  async reorderVolumes(projectId: string, volumeIds: string[]): Promise<void> {
    await delay(120)
    const project = projectFor(projectId)
    const byId = new Map(project.volumes.map((volume) => [volume.id, volume]))
    project.volumes = volumeIds.map((id, index) => ({ ...byId.get(id)!, index: index + 1 }))
    const order = new Map(volumeIds.map((id, index) => [id, index]))
    const rows = chaptersFor(projectId)
    rows.sort((a, b) => (order.get(a.volumeId) ?? volumeIds.length) - (order.get(b.volumeId) ?? volumeIds.length) || a.index - b.index)
    renumberChapters(rows)
  },

  async moveChapter(projectId: string, chapterId: string, volumeId: string, placement: 'first' | 'last' | 'after', afterChapterId?: string): Promise<void> {
    await delay(120)
    const rows = chaptersFor(projectId)
    const chapter = rows.find((item) => item.id === chapterId)
    if (!chapter) throw new Error('chapter_not_found')
    rows.splice(rows.indexOf(chapter), 1)
    const targets = rows.filter((item) => item.volumeId === volumeId)
    let insertion = rows.length
    if (placement === 'first' && targets[0]) insertion = rows.indexOf(targets[0])
    if (placement === 'last' && targets.at(-1)) insertion = rows.indexOf(targets.at(-1)!) + 1
    if (placement === 'after') {
      const anchor = rows.find((item) => item.id === afterChapterId)
      if (!anchor) throw new Error('after_chapter_not_found')
      insertion = rows.indexOf(anchor) + 1
    }
    chapter.volumeId = volumeId
    rows.splice(insertion, 0, chapter)
    renumberChapters(rows)
  },

  async trashChapter(projectId: string, chapterId: string): Promise<void> {
    await delay(100)
    const rows = chaptersFor(projectId)
    const index = rows.findIndex((item) => item.id === chapterId)
    if (index < 0 || rows.length === 1) throw new Error('chapter_not_found')
    const [chapter] = rows.splice(index, 1)
    if (chapter) trashFor(projectId).chapters.unshift({
      id: chapter.id, title: chapter.title, words: chapter.words, volumeId: chapter.volumeId,
      volumeTitle: projectFor(projectId).volumes.find((item) => item.id === chapter.volumeId)?.title ?? null,
      deletedAt: new Date().toISOString()
    })
    renumberChapters(rows)
  },

  async trashVolume(projectId: string, volumeId: string, targetVolumeId?: string): Promise<void> {
    await delay(100)
    const project = projectFor(projectId)
    const index = project.volumes.findIndex((item) => item.id === volumeId)
    if (index < 0 || project.volumes.length === 1) throw new Error('volume_not_found')
    const volumeChapters = chaptersFor(projectId).filter((item) => item.volumeId === volumeId)
    if (volumeChapters.length && !targetVolumeId) throw new Error('target_volume_required')
    volumeChapters.forEach((chapter) => { chapter.volumeId = targetVolumeId! })
    const [volume] = project.volumes.splice(index, 1)
    project.volumes.forEach((item, volumeIndex) => { item.index = volumeIndex + 1 })
    if (volume) trashFor(projectId).volumes.unshift({ id: volume.id, title: volume.title, deletedAt: new Date().toISOString() })
  },

  async getTrash(projectId: string): Promise<ProjectTrash> {
    await delay(100)
    return structuredClone(trashFor(projectId))
  },

  async restoreVolume(projectId: string, volumeId: string): Promise<void> {
    await delay(100)
    const trash = trashFor(projectId)
    const index = trash.volumes.findIndex((item) => item.id === volumeId)
    const [volume] = trash.volumes.splice(index, 1)
    if (volume) projectFor(projectId).volumes.push({ id: volume.id, title: volume.title, index: projectFor(projectId).volumes.length + 1 })
  },

  async restoreChapter(projectId: string, chapterId: string, volumeId?: string): Promise<void> {
    await delay(100)
    const trash = trashFor(projectId)
    const index = trash.chapters.findIndex((item) => item.id === chapterId)
    const [item] = trash.chapters.splice(index, 1)
    if (!item) return
    const target = volumeId ?? item.volumeId ?? projectFor(projectId).volumes[0]!.id
    const rows = chaptersFor(projectId)
    rows.push({ id: item.id, volumeId: target, index: rows.length + 1, title: item.title, words: item.words, status: item.words ? 'done' : 'outlined', outline: [], outlineNote: '' })
  },

  async deleteTrashItem(projectId: string, kind: 'volumes' | 'chapters', id: string): Promise<void> {
    await delay(100)
    const rows = trashFor(projectId)[kind]
    const index = rows.findIndex((item) => item.id === id)
    if (index >= 0) rows.splice(index, 1)
  },

  async listCodex(projectId = 'p1'): Promise<CodexEntry[]> {
    await delay()
    return projectId === 'p1' ? structuredClone(state.codex) : []
  },

  async createCodexEntry(projectId: string, draft: CodexEntryDraft): Promise<CodexEntry> {
    await delay(140)
    const entry: CodexEntry = {
      id: `cx_mock_${Date.now().toString(36)}`,
      kind: draft.kind,
      name: draft.name.trim(),
      aliases: [...draft.aliases],
      summary: draft.summary.trim(),
      resident: draft.resident,
      status: draft.status,
      refChapters: [],
      conflicts: 0,
      character: draft.kind === 'character' ? structuredClone(draft.character ?? {}) : undefined,
      facts: draft.kind === 'character' ? undefined : structuredClone(draft.facts ?? []),
      plantedChapterId: draft.plantedChapterId,
      expectedChapterId: draft.expectedChapterId,
      foreshadowResolved: draft.foreshadowResolved,
      resolvedChapterId: draft.resolvedChapterId
    }
    if (projectId === 'p1') state.codex.push(entry)
    return structuredClone(entry)
  },

  async updateCodexEntry(id: string, draft: CodexEntryDraft): Promise<CodexEntry> {
    await delay(140)
    const entry = state.codex.find((item) => item.id === id)
    if (!entry) throw new Error('codex_entry_not_loaded')
    Object.assign(entry, {
      kind: draft.kind,
      name: draft.name.trim(),
      aliases: [...draft.aliases],
      summary: draft.summary.trim(),
      resident: draft.resident,
      status: draft.status,
      character: draft.kind === 'character' ? structuredClone(draft.character ?? {}) : undefined,
      facts: draft.kind === 'character' ? undefined : structuredClone(draft.facts ?? []),
      plantedChapterId: draft.plantedChapterId,
      expectedChapterId: draft.expectedChapterId,
      foreshadowResolved: draft.foreshadowResolved,
      resolvedChapterId: draft.resolvedChapterId
    })
    return structuredClone(entry)
  },

  async confirmCodexEntry(id: string): Promise<void> {
    await delay(120)
    const e = state.codex.find((x) => x.id === id)
    if (e) e.status = 'confirmed'
  },

  async dropCodexEntry(id: string): Promise<void> {
    await delay(120)
    state.codex = state.codex.filter((x) => x.id !== id)
  },

  async listGuardIssues(projectId = 'p1'): Promise<GuardIssue[]> {
    await delay()
    if (projectId === 'p1') return structuredClone(state.issues)
    const book = findShelfBook(projectId)
    return Array.from({ length: book?.guardOpen ?? 0 }, (_, index): GuardIssue => ({
      id: `${projectId}-g${index + 1}`,
      kind: 'conflict',
      severity: index === 0 ? 'high' : 'mid',
      category: '待核对设定',
      title: `《${book?.title ?? '当前作品'}》有一处前后信息需要确认`,
      chapterRef: `第 ${Math.max(1, (book?.chapters ?? 1) - index)} 章`,
      detail: '这是当前作品的独立演示告警，不会引用其他作品的人物或设定。',
      evidence: [],
      actions: ['回到正文确认'],
      resolved: false,
      arbitrationStatus: 'not_requested'
    }))
  },

  async getGuardOverview(projectId = 'p1'): Promise<GuardOverview> {
    await delay(100)
    const completed = chaptersFor(projectId).filter((chapter) => chapter.words > 0).length
    return {
      status: completed ? 'completed' : 'idle', queued: 0, running: 0, completed, failed: 0,
      outboxPending: 0, outboxDeadLetter: 0, latestActivityAt: new Date().toISOString(), runs: []
    }
  },
  async reflowProjectTimeline(_projectId = 'p1'): Promise<TimelineReflowResult> {
    return {
      claimsExamined: 38,
      claimsChanged: 6,
      affectedChapterIds: ['c1', 'c2'],
      resolved: 4,
      unresolved: 2,
      ambiguous: 0,
      cyclic: 0,
      cycles: [],
      rescansQueued: 2,
      rescanRunIds: [1, 2]
    }
  },

  async scanProject(projectId = 'p1'): Promise<{ queued: number; run_ids: number[] }> {
    await delay(120)
    return { queued: chaptersFor(projectId).filter((chapter) => chapter.words > 0).length, run_ids: [] }
  },

  async resolveGuardIssue(_projectId: string, id: string, _issueRev: number, _action: GuardResolutionAction): Promise<void> {
    await delay(120)
    const i = state.issues.find((x) => x.id === id)
    if (i) i.resolved = true
  },

  async getContextLayers(_projectId?: string, _chapterId?: string): Promise<ContextLayer[]> {
    await delay(150)
    return structuredClone(seed.contextLayers)
  },

  draftParagraphs: seed.draftParagraphs
}
