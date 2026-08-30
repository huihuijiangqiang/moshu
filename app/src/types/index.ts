export type CodexKind = 'character' | 'faction' | 'place' | 'item' | 'system' | 'foreshadow'

export const CODEX_KIND_LABEL: Record<CodexKind, string> = {
  character: '人物',
  faction: '势力',
  place: '地点',
  item: '物品',
  system: '力量体系',
  foreshadow: '伏笔'
}

export interface CharacterArc {
  past?: string
  current?: string
  next?: string
}

export interface CharacterProfile {
  role?: string
  age?: string
  appearance?: string
  personality?: string[]
  desire?: string
  motivation?: string
  flaw?: string
  fear?: string
  ability?: string
  limitation?: string
  speech?: string
  background?: string
  currentState?: string
  arc?: CharacterArc
}

export interface CodexFact {
  label: string
  value: string
}

export interface CodexRelation {
  targetId?: string
  name: string
  relation: string
  note: string
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
  /** 人物专属档案；待确认人物允许只记录已从正文抽取到的字段。 */
  character?: CharacterProfile
  /** 非人物条目的类型化事实，按写作时的查阅优先级排列。 */
  facts?: CodexFact[]
  relations?: CodexRelation[]
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
  /** 章纲每次确认保存后递增，后端据此做乐观锁与历史快照。 */
  outlineRevision?: number
  outlineUpdatedAt?: string
  /** 作者明确选择“更新计划并标记正文待调整”后才会设为 true。 */
  bodyNeedsRevision?: boolean
  /** 正文 HTML（TipTap 序列化）。列表接口不返回，按需拉取。 */
  content?: string
  /** 写完后异步生成的 200 字摘要，供第 3 层滚动记忆使用 */
  summary?: string
}

export interface ChapterPlanPatch {
  title: string
  outline: string[]
  outlineNote: string
  bodyNeedsRevision: boolean
  baseRevision: number
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
