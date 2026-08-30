export type CodexKind = 'character' | 'faction' | 'place' | 'item' | 'system' | 'foreshadow'

export const CODEX_KIND_LABEL: Record<CodexKind, string> = {
  character: '人物',
  faction: '势力',
  place: '地点',
  item: '物品',
  system: '力量体系',
  foreshadow: '伏笔'
}

export interface CodexEntry {
  id: string
  kind: CodexKind
  name: string
  aliases: string[]
  summary: string
  /** 是否常驻上下文第 1 层（稳定设定）。常驻项会计入每次生成的固定预算。 */
  resident: boolean
  /** 引用过本条目的章节序号 */
  refChapters: number[]
  /** 'pending' = 守卫自动抽取、等作者确认 */
  status: 'confirmed' | 'pending'
  conflicts: number
  /** 仅 foreshadow：埋设章与预计回收点 */
  plantedAt?: number
  expectedBy?: string
}

export type ChapterStatus = 'outlined' | 'drafting' | 'done'

export interface Chapter {
  id: string
  volumeId: string
  index: number
  title: string
  words: number
  status: ChapterStatus
  /** 章纲节点，一键成章按它逐点推进 */
  outline: string[]
  outlineNote: string
  /** 正文 HTML（TipTap 序列化）。列表接口不返回，按需拉取。 */
  content?: string
  /** 写完后异步生成的 200 字摘要，供第 3 层滚动记忆使用 */
  summary?: string
}

export interface Volume { id: string; index: number; title: string }

export interface Project {
  id: string
  title: string
  volumes: Volume[]
  /** 全书统计来自项目摘要，不等于当前已加载到内存的章节列表。 */
  wordCount?: number
  chapterCount?: number
  dailyGoal: number
  dailyWords: number
  styleProfile: string | null
}

export type GuardKind = 'conflict' | 'foreshadow' | 'pending-entry'

export interface GuardIssue {
  id: string
  kind: GuardKind
  severity: 'high' | 'mid'
  category: string
  title: string
  chapterRef: string
  detail?: string
  evidence: { label: string; text: string; accent?: boolean }[]
  actions: string[]
  resolved: boolean
}

export interface ContextLayer {
  key: 'resident' | 'retrieved' | 'summary' | 'adjacent'
  label: string
  detail: string
  tokens: number
}

export interface GenerateOptions {
  chapterId: string
  targetWords: number
  model: 'basic' | 'advanced'
  useStyleProfile: boolean
  dialogueDensity: 'low' | 'mid' | 'high'
}
