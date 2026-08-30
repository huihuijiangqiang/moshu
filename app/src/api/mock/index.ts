import { delay } from '../http'
import * as seed from './seed'
import { findShelfBook } from './shelf'
import type { Chapter, CodexEntry, GuardIssue, Project, ContextLayer } from '@/types'

/** 内存态副本：mock 下的写操作要真的改变数据，否则界面行为是假的。 */
const state = {
  project: structuredClone(seed.project) as Project,
  chapters: structuredClone(seed.chapters) as Chapter[],
  codex: structuredClone(seed.codex) as CodexEntry[],
  issues: structuredClone(seed.guardIssues) as GuardIssue[]
}

const projectDrafts = new Map<string, Chapter[]>()

function projectFor(id: string): Project {
  if (id === seed.project.id) return state.project
  const book = findShelfBook(id)
  return {
    id,
    title: book?.title ?? '未命名作品',
    wordCount: book?.words ?? 0,
    chapterCount: book?.chapters ?? 0,
    dailyGoal: 3000,
    dailyWords: book?.todayWords ?? 0,
    styleProfile: null,
    volumes: [{ id: `${id}-v1`, index: 1, title: book?.status === 'planning' ? '故事构思' : '第一卷' }]
  }
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
    const c = [...state.chapters, ...projectDrafts.values()].flat().find((x) => x.id === id)
    return c ? structuredClone(c) : undefined
  },

  async saveChapter(id: string, patch: Partial<Chapter>): Promise<void> {
    await delay(120)
    const c = [...state.chapters, ...projectDrafts.values()].flat().find((x) => x.id === id)
    if (c) Object.assign(c, patch)
  },

  async listCodex(projectId = 'p1'): Promise<CodexEntry[]> {
    await delay()
    return projectId === 'p1' ? structuredClone(state.codex) : []
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
      resolved: false
    }))
  },

  async resolveGuardIssue(id: string): Promise<void> {
    await delay(120)
    const i = state.issues.find((x) => x.id === id)
    if (i) i.resolved = true
  },

  async getContextLayers(): Promise<ContextLayer[]> {
    await delay(150)
    return structuredClone(seed.contextLayers)
  },

  draftParagraphs: seed.draftParagraphs
}
