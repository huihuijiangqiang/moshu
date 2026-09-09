import { delay } from '../http'
import * as seed from './seed'
import { findShelfBook, type CreatedBook } from './shelf'
import type { Chapter, ChapterPlanPatch, ChapterVersionDetail, ChapterVersionRestoreResult, ChapterVersionSummary, CharacterStatistics, CodexEntry, CodexEntryDraft, CodexRelation, CodexRelationDraft, CodexStateDraft, CodexStateHistoryItem, GuardIssue, GuardOverview, GuardResolutionAction, Project, ProjectNote, ContextLayer, ProjectPatch, ProjectTrash, TemporalDecisionResult, TemporalReviewItem, TimelineBoard, TimelineEntry, TimelineEntryDraft, TimelineReflowResult, WritingProgressDay, StoryboardAdaptation, StoryboardEpisode, StoryboardScene, StoryboardShot, VisualProfile } from '@/types'

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
const temporalReviewStates = new Map<string, TemporalReviewItem[]>()
const timelineEntryStates = new Map<string, TimelineEntry[]>()
const codexEntryStates = new Map<string, CodexEntry[]>()
const codexStateHistoryStates = new Map<string, CodexStateHistoryItem[]>()
const projectNoteStates = new Map<string, ProjectNote[]>()
const storyboardStates = new Map<string, StoryboardAdaptation>()
let timelineEntrySequence = 1
let codexStateSequence = 1
let codexRelationSequence = 1
let projectNoteSequence = 1

function storyboardFor(projectId: string): StoryboardAdaptation {
  const existing = storyboardStates.get(projectId)
  if (existing) return existing
  const entries = codexEntriesFor(projectId)
  const character = entries.find((entry) => entry.kind === 'character')
  const place = entries.find((entry) => entry.kind === 'place')
  const adaptationId = `${projectId}-ad1`
  const profile: VisualProfile | undefined = character ? {
    id: `${projectId}-vp-${character.id}`, adaptationId, codexEntryId: character.id,
    displayName: character.name, style: '国风半厚涂 · 低饱和冷色', appearance: character.character?.appearance ?? character.summary,
    costume: '沿用设定库中的服装和标志性道具，不在镜头间改变', palette: ['#1f3e45', '#b9c6c0', '#d8a36b'],
    referenceAssetIds: [], version: 1, locked: true, notes: '当前为文字视觉档案，后续接入参考图时沿用该版本。'
  } : undefined
  const sceneId = `${projectId}-scene-1`
  const episodeId = `${projectId}-ep1`
  const shot = (order: number, shotType: StoryboardShot['shotType'], action: string, visualPrompt: string): StoryboardShot => ({
    id: `${projectId}-shot-${order}`, sceneId, order, shotType, camera: order === 1 ? '缓慢推近' : '平移跟拍', durationTarget: 4,
    action, dialogue: order === 2 ? '「先把这一处看清。」' : '', narration: order === 1 ? '风雪停后，城墙露出一线灰白的天。' : '', visualPrompt,
    referenceAssetIds: profile ? [profile.id] : [], status: 'draft'
  })
  const scene: StoryboardScene = {
    id: sceneId, episodeId, order: 1, purpose: '建立本集的核心悬念和人物视觉锚点', locationEntryId: place?.id, timeAnchor: '清晨 · 关键地点',
    characterEntryIds: character ? [character.id] : [], summary: '从小说当前章节拆出第一场，先确认人物、地点和叙事动作。',
    shots: [shot(1, 'wide', '人物进入环境，停在视觉焦点前。', '竖屏国风漫剧，环境建立镜头，人物留出字幕空间。'), shot(2, 'medium', '人物抬手或回头，动作落在对白之前。', '中景，服装与标志性道具保持连续，冷色环境中的一处暖色反光。'), shot(3, 'close', '人物看向画外，留下下一镜头的悬念。', '近景，克制表情，背景虚化，只保留叙事关键物件。')]
  }
  const episode: StoryboardEpisode = { id: episodeId, adaptationId, number: 1, title: '第一集 · 开场', sourceChapterIds: [], targetDuration: 90, status: 'draft', scenes: [scene] }
  const adaptation: StoryboardAdaptation = { id: adaptationId, projectId, title: `《${projectFor(projectId).title}》· 竖屏漫剧`, format: 'comic_drama', aspectRatio: '9:16', styleProfile: { label: '冷峻叙事 · 半厚涂', description: '以人物动作和场景锚点保持镜头连续。' }, status: 'draft', episodes: [episode], visualProfiles: profile ? [profile] : [] }
  storyboardStates.set(projectId, adaptation)
  return adaptation
}

function codexEntriesFor(projectId: string): CodexEntry[] {
  if (projectId === state.project.id) return state.codex
  const existing = codexEntryStates.get(projectId)
  if (existing) return existing
  const rows: CodexEntry[] = []
  codexEntryStates.set(projectId, rows)
  return rows
}

function confirmedCodexEntry(projectId: string, entryId: string): CodexEntry {
  const entry = codexEntriesFor(projectId).find((item) => item.id === entryId && item.status === 'confirmed')
  if (!entry) throw new Error('codex_state_not_found')
  return entry
}

function projectedCodexEntries(projectId: string): CodexEntry[] {
  const rows = codexEntriesFor(projectId)
  for (const source of rows) {
    source.relations?.forEach((relation, index) => {
      relation.id ??= `cr_mock_${source.id}_${index}`
      relation.direction ??= 'outgoing'
      relation.targetKind ??= rows.find((item) => item.id === relation.targetId)?.kind
    })
  }
  const projected = structuredClone(rows)
  projected.filter((entry) => entry.status !== 'confirmed').forEach((entry) => { entry.relations = [] })
  for (const source of rows) {
    if (source.status !== 'confirmed') continue
    for (const relation of source.relations ?? []) {
      if (relation.direction === 'incoming' || !relation.targetId || !relation.id) continue
      const target = projected.find((item) => item.id === relation.targetId)
      if (!target || target.status !== 'confirmed') continue
      target.relations ??= []
      target.relations.push({
        id: relation.id,
        targetId: source.id,
        targetKind: source.kind,
        direction: 'incoming',
        name: source.name,
        relation: relation.relation,
        note: relation.note
      })
    }
  }
  return projected
}

function codexStateHistoryFor(projectId: string, entryId: string): CodexStateHistoryItem[] {
  const key = `${projectId}:${entryId}`
  const existing = codexStateHistoryStates.get(key)
  if (existing) return existing
  const rows: CodexStateHistoryItem[] = projectId === 'p1' && entryId === 'c-shenyan' ? [
    {
      id: 'cs_mock_1', source: 'author', editable: true, stateKey: '兵器状态', value: '残锋已经折断，只剩半截',
      polarity: 'positive', note: '后续动作描写不得把它当完整长剑使用。', chapterId: 'ch85', chapterIndex: 85,
      chapterTitle: '雪夜叩关', revision: 1, createdAt: '2026-08-12T09:30:00Z'
    },
    {
      id: 'claim:mock-1', source: 'extracted', editable: false, stateKey: '所在地点', value: '雁回关城南兵器坊',
      polarity: 'positive', chapterId: 'ch87', chapterIndex: 87, chapterTitle: '断刃', bodyRevision: 3,
      paragraphId: 'p-4', confidence: 0.94, createdAt: '2026-08-14T10:20:00Z'
    }
  ] : []
  codexStateHistoryStates.set(key, rows)
  return rows
}

function validateStateDraft(projectId: string, entryId: string, draft: CodexStateDraft): Chapter {
  confirmedCodexEntry(projectId, entryId)
  const chapter = chaptersFor(projectId).find((item) => item.id === draft.chapterId)
  if (!chapter || !draft.stateKey.trim() || !draft.value.trim()) throw new Error('invalid_codex_state')
  return chapter
}

function timelineEntriesFor(projectId: string): TimelineEntry[] {
  const existing = timelineEntryStates.get(projectId)
  if (existing) return existing
  const rows: TimelineEntry[] = []
  timelineEntryStates.set(projectId, rows)
  return rows
}

function temporalReviewsFor(projectId: string): TemporalReviewItem[] {
  const existing = temporalReviewStates.get(projectId)
  if (existing) return existing
  const rows: TemporalReviewItem[] = projectId === 'p1' ? [
    {
      claimId: 901,
      chapterId: 'p1-ch05',
      chapterIndex: 5,
      chapterTitle: '县城初雪',
      eventRef: '冬集开市',
      relation: 'after',
      relationRef: '许知微启程',
      original: '过几日后的清晨',
      normalized: '2-7日后+清晨',
      offsetMinSeconds: 2 * 86400 + 4 * 3600,
      offsetMaxSeconds: 7 * 86400 + 8 * 3600,
      dependencyStatus: 'unresolved',
      overrideVersion: 0
    }
  ] : []
  temporalReviewStates.set(projectId, rows)
  return rows
}

function projectFor(id: string): Project {
  if (id === seed.project.id) return state.project
  const existing = projectStates.get(id)
  if (existing) return existing
  const book = findShelfBook(id)
  const created = book && 'draftVolumes' in book ? book as CreatedBook : undefined
  const volumes = created?.draftVolumes?.length
    ? created.draftVolumes.map((volume, index) => ({ id: `${id}-v${index + 1}`, index: index + 1, title: volume.title || `第 ${index + 1} 卷` }))
    : [{ id: `${id}-v1`, index: 1, title: book?.status === 'planning' ? '故事构思' : '第一卷' }]
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
    volumes
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
  const created = book && 'draftChapters' in book ? book as CreatedBook : undefined
  if (created?.draftChapters?.length) {
    const volumes = projectFor(id).volumes
    const rows = created.draftChapters.map((draft, index): Chapter => {
      // CreateBookInput.volumeIndex follows the API contract and is zero-based.
      const volumeIndex = Math.max(0, Math.min(volumes.length - 1, draft.volumeIndex ?? 0))
      const title = draft.title.trim() || `第 ${index + 1} 章`
      const outline = draft.outline.filter((beat) => beat.trim())
      return {
        id: `${id}-ch${index + 1}`,
        volumeId: volumes[volumeIndex]?.id ?? volumes[0]!.id,
        index: index + 1,
        title,
        words: 0,
        status: 'outlined',
        outline,
        outlineNote: outline.join('；')
      }
    })
    projectDrafts.set(id, rows)
    return rows
  }
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

function projectIdForChapter(chapter: Chapter): string | undefined {
  if (state.chapters.includes(chapter)) return state.project.id
  return [...projectDrafts.entries()].find(([, chapters]) => chapters.includes(chapter))?.[0]
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

  async getWritingProgress(projectId: string, days = 30): Promise<WritingProgressDay[]> {
    await delay(40)
    const project = projectFor(projectId)
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    return Array.from({ length: days }, (_, index) => {
      const day = new Date(today)
      day.setDate(today.getDate() - (days - index - 1))
      const isToday = index === days - 1
      return {
        date: day.toISOString().slice(0, 10),
        wordsAdded: isToday ? project.dailyWords : 0,
        saves: isToday && project.dailyWords > 0 ? 1 : 0,
        targetWordsDaily: project.dailyGoal,
        targetMet: isToday && project.dailyWords >= project.dailyGoal
      }
    })
  },

  async listProjectNotes(projectId: string): Promise<ProjectNote[]> {
    await delay(80)
    projectFor(projectId)
    return structuredClone(projectNoteStates.get(projectId) ?? [])
  },

  async createProjectNote(projectId: string, content: string, chapterId?: string): Promise<ProjectNote> {
    await delay(100)
    projectFor(projectId)
    const clean = content.trim()
    const chapter = chapterId ? chaptersFor(projectId).find((item) => item.id === chapterId) : undefined
    if (!clean || (chapterId && !chapter)) throw new Error('invalid_project_note')
    const note: ProjectNote = {
      id: `pn_mock_${projectNoteSequence++}`,
      projectId,
      chapterId: chapter?.id,
      chapterIndex: chapter?.index,
      chapterTitle: chapter?.title,
      content: clean,
      createdAt: new Date().toISOString()
    }
    const rows = projectNoteStates.get(projectId) ?? []
    rows.unshift(note)
    projectNoteStates.set(projectId, rows)
    return structuredClone(note)
  },

  async deleteProjectNote(projectId: string, noteId: string): Promise<void> {
    await delay(80)
    const rows = projectNoteStates.get(projectId) ?? []
    const index = rows.findIndex((item) => item.id === noteId)
    if (index < 0) throw new Error('project_note_not_found')
    rows.splice(index, 1)
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

  async updateChapterPov(
    id: string,
    entryId: string | undefined,
    expectedRevision: number
  ): Promise<{ entryId?: string; revision: number }> {
    await delay(100)
    const chapter = findChapter(id)
    if (!chapter) throw new Error('chapter_not_found')
    if ((chapter.povRevision ?? 0) !== expectedRevision) throw new Error('pov_revision_conflict')
    if (entryId) {
      const entry = state.codex.find((item) => item.id === entryId)
      if (projectIdForChapter(chapter) !== state.project.id || !entry || entry.kind !== 'character' || entry.status !== 'confirmed') {
        throw new Error('invalid_pov_character')
      }
    }
    chapter.povEntryId = entryId
    chapter.povRevision = (chapter.povRevision ?? 0) + 1
    return { entryId: chapter.povEntryId, revision: chapter.povRevision }
  },

  async getCharacterStatistics(projectId: string, entryId: string): Promise<CharacterStatistics> {
    await delay(100)
    const entry = projectId === state.project.id
      ? state.codex.find((item) => item.id === entryId && item.kind === 'character' && item.status === 'confirmed')
      : undefined
    if (!entry) throw new Error('character_statistics_not_found')
    const rows = chaptersFor(projectId)
    const points = rows.flatMap((chapter) => {
      const referenced = entry.refChapters.includes(chapter.index)
      const isPov = chapter.povEntryId === entryId
      if (!referenced && !isPov) return []
      return [{
        chapterId: chapter.id,
        chapterIndex: chapter.index,
        chapterTitle: chapter.title,
        words: chapter.words,
        explicitReferences: referenced ? 1 : 0,
        extractedClaims: 0,
        isPov
      }]
    })
    const first = points[0]
    const last = points.at(-1)
    const latestIndex = rows.at(-1)?.index ?? 0
    return structuredClone({
      entryId: entry.id,
      name: entry.name,
      appearanceChapters: points.length,
      explicitReferences: points.reduce((sum, point) => sum + point.explicitReferences, 0),
      extractedClaims: 0,
      povChapters: points.filter((point) => point.isPov).length,
      povWords: points.filter((point) => point.isPov).reduce((sum, point) => sum + point.words, 0),
      firstAppearance: first?.chapterIndex,
      lastAppearance: last?.chapterIndex,
      hiatusChapters: last ? Math.max(0, latestIndex - last.chapterIndex) : undefined,
      chapters: points
    })
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
    // The array position is authoritative after a move. Sorting by the old
    // chapter index here would immediately undo a same-volume reorder.
    rows.forEach((item, index) => { item.index = index + 1 })
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
    return projectedCodexEntries(projectId)
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
    codexEntriesFor(projectId).push(entry)
    return structuredClone(entry)
  },

  async updateCodexEntry(id: string, draft: CodexEntryDraft): Promise<CodexEntry> {
    await delay(140)
    const entry = [state.codex, ...codexEntryStates.values()]
      .flatMap((rows) => rows)
      .find((item) => item.id === id)
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
    const e = [state.codex, ...codexEntryStates.values()]
      .flatMap((rows) => rows)
      .find((item) => item.id === id)
    if (e) e.status = 'confirmed'
  },

  async dropCodexEntry(id: string): Promise<void> {
    await delay(120)
    const primaryIndex = state.codex.findIndex((item) => item.id === id)
    if (primaryIndex >= 0) {
      state.codex.splice(primaryIndex, 1)
      state.codex.forEach((entry) => {
        entry.relations = entry.relations?.filter((relation) => relation.targetId !== id)
      })
      return
    }
    for (const rows of codexEntryStates.values()) {
      const index = rows.findIndex((item) => item.id === id)
      if (index >= 0) {
        rows.splice(index, 1)
        rows.forEach((entry) => {
          entry.relations = entry.relations?.filter((relation) => relation.targetId !== id)
        })
        return
      }
    }
  },

  async getStoryboard(projectId = 'p1'): Promise<StoryboardAdaptation> {
    await delay(140)
    return structuredClone(storyboardFor(projectId))
  },

  async createStoryboardEpisode(projectId: string, input: Pick<StoryboardEpisode, 'title' | 'sourceChapterIds' | 'targetDuration'>): Promise<StoryboardEpisode> {
    await delay(100)
    const adaptation = storyboardFor(projectId)
    const episode: StoryboardEpisode = { id: `${projectId}-ep-${Date.now().toString(36)}`, adaptationId: adaptation.id, number: adaptation.episodes.length + 1, title: input.title, sourceChapterIds: [...input.sourceChapterIds], targetDuration: input.targetDuration, status: 'draft', scenes: [] }
    adaptation.episodes.push(episode)
    return structuredClone(episode)
  },

  async updateStoryboardEpisode(projectId: string, episodeId: string, patch: Partial<StoryboardEpisode>): Promise<StoryboardEpisode> {
    await delay(80)
    const episode = storyboardFor(projectId).episodes.find((item) => item.id === episodeId)
    if (!episode) throw new Error('episode_not_found')
    Object.assign(episode, patch)
    return structuredClone(episode)
  },

  async createStoryboardScene(projectId: string, episodeId: string, input: Pick<StoryboardScene, 'purpose' | 'summary' | 'timeAnchor' | 'locationEntryId' | 'characterEntryIds'>): Promise<StoryboardScene> {
    await delay(100)
    const episode = storyboardFor(projectId).episodes.find((item) => item.id === episodeId)
    if (!episode) throw new Error('episode_not_found')
    const scene: StoryboardScene = { id: `${projectId}-scene-${Date.now().toString(36)}`, episodeId, order: episode.scenes.length + 1, purpose: input.purpose, summary: input.summary, timeAnchor: input.timeAnchor, locationEntryId: input.locationEntryId, characterEntryIds: [...input.characterEntryIds], shots: [] }
    episode.scenes.push(scene)
    return structuredClone(scene)
  },

  async updateStoryboardScene(projectId: string, sceneId: string, patch: Partial<StoryboardScene>): Promise<StoryboardScene> {
    await delay(80)
    const scene = storyboardFor(projectId).episodes.flatMap((episode) => episode.scenes).find((item) => item.id === sceneId)
    if (!scene) throw new Error('scene_not_found')
    Object.assign(scene, patch)
    return structuredClone(scene)
  },

  async createStoryboardShot(projectId: string, sceneId: string, input: Partial<StoryboardShot>): Promise<StoryboardShot> {
    await delay(80)
    const scene = storyboardFor(projectId).episodes.flatMap((episode) => episode.scenes).find((item) => item.id === sceneId)
    if (!scene) throw new Error('scene_not_found')
    const shot: StoryboardShot = { id: `${projectId}-shot-${Date.now().toString(36)}`, sceneId, order: scene.shots.length + 1, shotType: 'medium', camera: 'static', durationTarget: 4, action: '', dialogue: '', narration: '', visualPrompt: '', referenceAssetIds: [], status: 'draft', ...input }
    scene.shots.push(shot)
    return structuredClone(shot)
  },

  async updateStoryboardShot(projectId: string, shotId: string, patch: Partial<StoryboardShot>): Promise<StoryboardShot> {
    await delay(80)
    const shot = storyboardFor(projectId).episodes.flatMap((episode) => episode.scenes).flatMap((scene) => scene.shots).find((item) => item.id === shotId)
    if (!shot) throw new Error('shot_not_found')
    Object.assign(shot, patch)
    return structuredClone(shot)
  },

  async updateVisualProfile(projectId: string, profileId: string, patch: Partial<VisualProfile>): Promise<VisualProfile> {
    await delay(80)
    const profile = storyboardFor(projectId).visualProfiles.find((item) => item.id === profileId)
    if (!profile) throw new Error('visual_profile_not_found')
    Object.assign(profile, patch, { version: profile.version + 1 })
    return structuredClone(profile)
  },

  async createVisualProfile(projectId: string, input: Pick<VisualProfile, 'codexEntryId' | 'displayName'> & Partial<VisualProfile>): Promise<VisualProfile> {
    await delay(80)
    const adaptation = storyboardFor(projectId)
    if (adaptation.visualProfiles.some((item) => item.codexEntryId === input.codexEntryId)) throw new Error('visual_profile_already_exists')
    const profile: VisualProfile = {
      id: `${projectId}-vp-${Date.now().toString(36)}`,
      adaptationId: adaptation.id,
      codexEntryId: input.codexEntryId,
      displayName: input.displayName,
      style: input.style ?? '',
      appearance: input.appearance ?? '',
      costume: input.costume ?? '',
      palette: input.palette ?? [],
      referenceAssetIds: input.referenceAssetIds ?? [],
      version: 1,
      locked: false,
      notes: input.notes ?? ''
    }
    adaptation.visualProfiles.push(profile)
    return structuredClone(profile)
  },

  async createCodexRelation(projectId: string, entryId: string, draft: CodexRelationDraft): Promise<CodexRelation> {
    await delay(120)
    const source = confirmedCodexEntry(projectId, entryId)
    const target = confirmedCodexEntry(projectId, draft.targetId)
    const relationName = draft.relation.trim()
    if (source.id === target.id || !relationName) throw new Error('invalid_codex_relation')
    if (source.relations?.some((item) => item.direction !== 'incoming' && item.targetId === target.id && item.relation === relationName)) {
      throw new Error('codex_relation_duplicate')
    }
    const relation: CodexRelation = {
      id: `cr_mock_${Date.now().toString(36)}_${codexRelationSequence++}`,
      targetId: target.id,
      targetKind: target.kind,
      direction: 'outgoing',
      name: target.name,
      relation: relationName,
      note: draft.note?.trim() || undefined
    }
    source.relations ??= []
    source.relations.push(relation)
    return structuredClone(relation)
  },

  async updateCodexRelation(projectId: string, entryId: string, relationId: string, draft: CodexRelationDraft): Promise<CodexRelation> {
    await delay(120)
    const source = confirmedCodexEntry(projectId, entryId)
    const target = confirmedCodexEntry(projectId, draft.targetId)
    const relation = source.relations?.find((item) => item.id === relationId && item.direction !== 'incoming')
    const relationName = draft.relation.trim()
    if (!relation || source.id === target.id || !relationName) throw new Error('invalid_codex_relation')
    if (source.relations?.some((item) => item.id !== relationId && item.direction !== 'incoming' && item.targetId === target.id && item.relation === relationName)) {
      throw new Error('codex_relation_duplicate')
    }
    Object.assign(relation, {
      targetId: target.id,
      targetKind: target.kind,
      name: target.name,
      relation: relationName,
      note: draft.note?.trim() || undefined
    })
    return structuredClone(relation)
  },

  async deleteCodexRelation(projectId: string, entryId: string, relationId: string): Promise<void> {
    await delay(120)
    const source = confirmedCodexEntry(projectId, entryId)
    const index = source.relations?.findIndex((item) => item.id === relationId && item.direction !== 'incoming') ?? -1
    if (index < 0) throw new Error('invalid_codex_relation')
    source.relations!.splice(index, 1)
  },

  async listCodexStateHistory(projectId: string, entryId: string): Promise<CodexStateHistoryItem[]> {
    await delay(100)
    confirmedCodexEntry(projectId, entryId)
    return structuredClone(codexStateHistoryFor(projectId, entryId))
  },

  async createCodexStateChange(projectId: string, entryId: string, draft: CodexStateDraft): Promise<CodexStateHistoryItem> {
    await delay(120)
    const chapter = validateStateDraft(projectId, entryId, draft)
    const rows = codexStateHistoryFor(projectId, entryId)
    const stateKey = draft.stateKey.trim()
    if (rows.some((item) => item.editable && item.chapterId === chapter.id && item.stateKey === stateKey)) {
      throw new Error('invalid_codex_state')
    }
    const item: CodexStateHistoryItem = {
      id: `cs_mock_${Date.now().toString(36)}_${codexStateSequence++}`,
      source: 'author',
      editable: true,
      stateKey,
      value: draft.value.trim(),
      polarity: 'positive',
      note: draft.note?.trim() || undefined,
      chapterId: chapter.id,
      chapterIndex: chapter.index,
      chapterTitle: chapter.title,
      revision: 1,
      createdAt: new Date().toISOString()
    }
    rows.push(item)
    rows.sort((left, right) => left.chapterIndex - right.chapterIndex || left.stateKey.localeCompare(right.stateKey))
    return structuredClone(item)
  },

  async updateCodexStateChange(projectId: string, entryId: string, changeId: string, expectedRevision: number, draft: CodexStateDraft): Promise<CodexStateHistoryItem> {
    await delay(120)
    const chapter = validateStateDraft(projectId, entryId, draft)
    const rows = codexStateHistoryFor(projectId, entryId)
    const item = rows.find((row) => row.id === changeId && row.editable)
    if (!item) throw new Error('codex_state_not_found')
    if (item.revision !== expectedRevision) throw new Error('codex_state_revision_conflict')
    const stateKey = draft.stateKey.trim()
    if (rows.some((row) => row.id !== changeId && row.editable && row.chapterId === chapter.id && row.stateKey === stateKey)) {
      throw new Error('invalid_codex_state')
    }
    Object.assign(item, {
      chapterId: chapter.id,
      chapterIndex: chapter.index,
      chapterTitle: chapter.title,
      stateKey,
      value: draft.value.trim(),
      note: draft.note?.trim() || undefined,
      revision: expectedRevision + 1
    })
    rows.sort((left, right) => left.chapterIndex - right.chapterIndex || left.stateKey.localeCompare(right.stateKey))
    return structuredClone(item)
  },

  async deleteCodexStateChange(projectId: string, entryId: string, changeId: string, expectedRevision: number): Promise<void> {
    await delay(100)
    confirmedCodexEntry(projectId, entryId)
    const rows = codexStateHistoryFor(projectId, entryId)
    const index = rows.findIndex((item) => item.id === changeId && item.editable)
    if (index < 0) throw new Error('codex_state_not_found')
    if (rows[index]!.revision !== expectedRevision) throw new Error('codex_state_revision_conflict')
    rows.splice(index, 1)
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
  async getTimelineBoard(projectId = 'p1'): Promise<TimelineBoard> {
    await delay(100)
    if (projectId !== 'p1') {
      const entries = timelineEntriesFor(projectId).filter((entry) => entry.status === 'active')
      const lanes = [...new Set(entries.map((entry) => entry.timelineId))].map((timelineId) => {
        const laneEntries = entries.filter((entry) => entry.timelineId === timelineId)
        const events = laneEntries.map((entry) => ({
          eventId: `entry:${entry.id}`, source: 'planned' as const, entryId: entry.id,
          timelineId, eventRef: entry.title, detail: entry.detail, chapterId: entry.chapterId,
          timeText: entry.timeText, storyOrder: entry.storyOrder,
          placementStatus: entry.storyOrder == null ? 'unplaced' as const : 'placed' as const,
          dependencyStatus: 'author', resolutionSource: 'author', timeStart: entry.timeStart,
          timeEnd: entry.timeEnd, editable: true, revision: entry.rev
        }))
        return {
          timelineId, label: timelineId === 'main' ? '主线' : timelineId,
          eventCount: events.length, placedCount: events.filter((event) => event.placementStatus === 'placed').length,
          reviewCount: 0, events
        }
      })
      const orders = entries.flatMap((entry) => entry.storyOrder == null ? [] : [entry.storyOrder])
      return {
        lanes, eventCount: entries.length, placedCount: orders.length, reviewCount: 0,
        unplacedCount: entries.length - orders.length,
        storyOrderMin: orders.length ? Math.min(...orders) : undefined,
        storyOrderMax: orders.length ? Math.max(...orders) : undefined
      }
    }
    const result: TimelineBoard = {
      lanes: [
        {
          timelineId: 'main', label: '主线', eventCount: 5, placedCount: 4, reviewCount: 1,
          events: [
            { eventId: 'claim:801', source: 'extracted', claimId: 801, timelineId: 'main', eventRef: '许知微醒在周家偏房', chapterId: 'p1-ch01', chapterIndex: 1, chapterTitle: '醒来', timeText: '腊月初三', storyOrder: 10, placementStatus: 'placed', dependencyStatus: 'resolved', confidence: 0.96, editable: false },
            { eventId: 'claim:802', source: 'extracted', claimId: 802, timelineId: 'main', eventRef: '拿出第一批腌菜换粮', chapterId: 'p1-ch02', chapterIndex: 2, chapterTitle: '灶间试味', timeText: '三日后', storyOrder: 13, placementStatus: 'placed', dependencyStatus: 'resolved', relation: 'after', relationRef: '许知微醒在周家偏房', confidence: 0.91, editable: false },
            { eventId: 'claim:803', source: 'extracted', claimId: 803, timelineId: 'main', eventRef: '县衙重查荒田契', chapterId: 'p1-ch04', chapterIndex: 4, chapterTitle: '旧契', timeText: '腊月初一', storyOrder: 8, placementStatus: 'placed', dependencyStatus: 'resolved', confidence: 0.94, editable: false },
            { eventId: 'claim:804', source: 'extracted', claimId: 804, timelineId: 'main', eventRef: '许知微启程去县城', chapterId: 'p1-ch05', chapterIndex: 5, chapterTitle: '县城初雪', timeText: '腊月初八', storyOrder: 18, placementStatus: 'placed', dependencyStatus: 'resolved', confidence: 0.95, editable: false },
            { eventId: 'claim:901', source: 'extracted', claimId: 901, timelineId: 'main', eventRef: '冬集开市', chapterId: 'p1-ch05', chapterIndex: 5, chapterTitle: '县城初雪', timeText: '过几日后的清晨', placementStatus: 'review', dependencyStatus: 'unresolved', relation: 'after', relationRef: '许知微启程去县城', confidence: 0.71, editable: false }
          ]
        },
        {
          timelineId: '周何氏支线', label: '周何氏支线', eventCount: 3, placedCount: 2, reviewCount: 0,
          events: [
            { eventId: 'claim:821', source: 'extracted', claimId: 821, timelineId: '周何氏支线', eventRef: '周何氏藏起旧账册', chapterId: 'p1-ch03', chapterIndex: 3, chapterTitle: '夜半旧账', timeText: '上月廿七', storyOrder: 4, placementStatus: 'placed', dependencyStatus: 'resolved', confidence: 0.88, editable: false },
            { eventId: 'claim:822', source: 'extracted', claimId: 822, timelineId: '周何氏支线', eventRef: '周何氏交出钥匙', chapterId: 'p1-ch05', chapterIndex: 5, chapterTitle: '县城初雪', timeText: '启程前一夜', storyOrder: 17, placementStatus: 'placed', dependencyStatus: 'resolved', relation: 'before', relationRef: '许知微启程去县城', confidence: 0.87, editable: false },
            { eventId: 'claim:823', source: 'extracted', claimId: 823, timelineId: '周何氏支线', eventRef: '账册缺页被发现', chapterId: 'p1-ch05', chapterIndex: 5, chapterTitle: '县城初雪', timeText: '此前不久', placementStatus: 'ambiguous', dependencyStatus: 'ambiguous', confidence: 0.63, editable: false }
          ]
        }
      ],
      eventCount: 8, placedCount: 6, reviewCount: 1, unplacedCount: 2,
      storyOrderMin: 4, storyOrderMax: 18
    }
    for (const entry of timelineEntriesFor(projectId).filter((item) => item.status === 'active')) {
      let lane = result.lanes.find((item) => item.timelineId === entry.timelineId)
      if (!lane) {
        lane = { timelineId: entry.timelineId, label: entry.timelineId === 'main' ? '主线' : entry.timelineId, eventCount: 0, placedCount: 0, reviewCount: 0, events: [] }
        result.lanes.push(lane)
      }
      lane.events.push({
        eventId: `entry:${entry.id}`, source: 'planned', entryId: entry.id,
        timelineId: entry.timelineId, eventRef: entry.title, detail: entry.detail,
        chapterId: entry.chapterId, chapterIndex: chaptersFor(projectId).find((chapter) => chapter.id === entry.chapterId)?.index,
        chapterTitle: chaptersFor(projectId).find((chapter) => chapter.id === entry.chapterId)?.title,
        timeText: entry.timeText, storyOrder: entry.storyOrder,
        placementStatus: entry.storyOrder == null ? 'unplaced' : 'placed', dependencyStatus: 'author',
        resolutionSource: 'author', timeStart: entry.timeStart, timeEnd: entry.timeEnd,
        editable: true, revision: entry.rev
      })
      lane.eventCount += 1
      if (entry.storyOrder == null) result.unplacedCount += 1
      else lane.placedCount += 1
    }
    const events = result.lanes.flatMap((lane) => lane.events)
    const orders = events.flatMap((event) => event.storyOrder == null ? [] : [event.storyOrder])
    result.eventCount = events.length
    result.placedCount = orders.length
    result.storyOrderMin = orders.length ? Math.min(...orders) : undefined
    result.storyOrderMax = orders.length ? Math.max(...orders) : undefined
    return structuredClone(result)
  },
  async createTimelineEntry(projectId: string, draft: TimelineEntryDraft): Promise<TimelineEntry> {
    await delay(100)
    const now = new Date().toISOString()
    const entry: TimelineEntry = {
      ...structuredClone(draft), id: `te_mock_${timelineEntrySequence++}`, projectId,
      storyOrder: draft.timeStart ? new Date(draft.timeStart).getTime() / 1000 : draft.storyOrder,
      status: 'active', rev: 1, createdAt: now, updatedAt: now
    }
    timelineEntriesFor(projectId).push(entry)
    return structuredClone(entry)
  },
  async updateTimelineEntry(projectId: string, entryId: string, expectedRev: number, draft: TimelineEntryDraft): Promise<TimelineEntry> {
    await delay(100)
    const entry = timelineEntriesFor(projectId).find((item) => item.id === entryId && item.status === 'active')
    if (!entry) throw new Error('timeline_entry_not_found')
    if (entry.rev !== expectedRev) throw new Error('timeline_entry_conflict')
    Object.assign(entry, structuredClone(draft), {
      storyOrder: draft.timeStart ? new Date(draft.timeStart).getTime() / 1000 : draft.storyOrder,
      rev: entry.rev + 1, updatedAt: new Date().toISOString()
    })
    return structuredClone(entry)
  },
  async archiveTimelineEntry(projectId: string, entryId: string, expectedRev: number): Promise<TimelineEntry> {
    await delay(100)
    const entry = timelineEntriesFor(projectId).find((item) => item.id === entryId && item.status === 'active')
    if (!entry) throw new Error('timeline_entry_not_found')
    if (entry.rev !== expectedRev) throw new Error('timeline_entry_conflict')
    entry.status = 'archived'
    entry.rev += 1
    entry.updatedAt = new Date().toISOString()
    return structuredClone(entry)
  },
  async listTemporalReviews(projectId = 'p1'): Promise<TemporalReviewItem[]> {
    await delay(80)
    return structuredClone(temporalReviewsFor(projectId))
  },
  async decideTemporalReview(
    projectId: string,
    claimId: number,
    action: 'confirm' | 'clear',
    expectedVersion: number,
    offsetSeconds?: number
  ): Promise<TemporalDecisionResult> {
    await delay(120)
    const item = temporalReviewsFor(projectId).find((row) => row.claimId === claimId)
    if (!item) throw new Error('temporal_review_not_found')
    if (item.overrideVersion !== expectedVersion) throw new Error('temporal_review_conflict')
    item.overrideVersion += 1
    item.overrideSeconds = action === 'confirm' ? offsetSeconds : undefined
    item.dependencyStatus = action === 'confirm' ? 'resolved' : 'unresolved'
    return {
      item: structuredClone(item),
      reflow: {
        claimsExamined: 38,
        claimsChanged: 1,
        affectedChapterIds: [item.chapterId ?? ''],
        resolved: action === 'confirm' ? 1 : 0,
        unresolved: action === 'clear' ? 1 : 0,
        ambiguous: 0,
        cyclic: 0,
        cycles: [],
        rescansQueued: 1,
        rescanRunIds: [12]
      }
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

  async getContextLayers(
    _projectId?: string,
    _chapterId?: string,
    _contextMode?: string
  ): Promise<ContextLayer[]> {
    await delay(150)
    return structuredClone(seed.contextLayers)
  },

  draftParagraphs: seed.draftParagraphs
}
