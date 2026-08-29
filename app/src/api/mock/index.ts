import { delay } from '../http'
import * as seed from './seed'
import type { Chapter, CodexEntry, GuardIssue, Project, ContextLayer } from '@/types'

/** 内存态副本：mock 下的写操作要真的改变数据，否则界面行为是假的。 */
const state = {
  project: structuredClone(seed.project) as Project,
  chapters: structuredClone(seed.chapters) as Chapter[],
  codex: structuredClone(seed.codex) as CodexEntry[],
  issues: structuredClone(seed.guardIssues) as GuardIssue[]
}

export const mockApi = {
  async getProject(): Promise<Project> {
    await delay()
    return structuredClone(state.project)
  },

  /** 列表不带正文——80 万字作品靠这个保证首屏 < 2s */
  async listChapters(): Promise<Chapter[]> {
    await delay()
    return state.chapters.map(({ content, ...rest }) => ({ ...rest }))
  },

  async getChapter(id: string): Promise<Chapter | undefined> {
    await delay(180)
    const c = state.chapters.find((x) => x.id === id)
    return c ? structuredClone(c) : undefined
  },

  async saveChapter(id: string, patch: Partial<Chapter>): Promise<void> {
    await delay(120)
    const c = state.chapters.find((x) => x.id === id)
    if (c) Object.assign(c, patch)
  },

  async listCodex(): Promise<CodexEntry[]> {
    await delay()
    return structuredClone(state.codex)
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

  async listGuardIssues(): Promise<GuardIssue[]> {
    await delay()
    return structuredClone(state.issues)
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
