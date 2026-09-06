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
  plantedChapterId?: string
  expectedChapterId?: string
  foreshadowResolved?: boolean
  resolvedChapterId?: string
  /** 后端完整结构化属性；编辑已知字段时用于保留关系等尚未开放的扩展数据。 */
  rawAttrs?: Record<string, unknown>
}

export interface CodexEntryDraft {
  kind: CodexKind
  name: string
  aliases: string[]
  summary: string
  resident: boolean
  status: 'confirmed' | 'pending'
  character?: CharacterProfile
  facts?: CodexFact[]
  plantedChapterId?: string
  expectedChapterId?: string
  foreshadowResolved?: boolean
  resolvedChapterId?: string
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
  /** 服务端正文乐观锁版本。 */
  rev?: number
}

export interface ChapterVersionSummary {
  id: number
  rev: number
  trigger: string
  words: number
  excerpt: string
  createdAt: string
  isCurrent: boolean
}

export interface ChapterVersionDetail extends ChapterVersionSummary {
  content: string
  contentJson: Record<string, unknown>
}

export interface ChapterVersionRestoreResult {
  rev: number
  restoredFromRev: number
  content: string
  contentJson: Record<string, unknown>
  words: number
  consistencyStatus: string
}

export type TextReplacementScope = 'chapter' | 'volume' | 'project'

export interface TextReplacementSpec {
  query: string
  replacement: string
  scope: TextReplacementScope
  chapterId?: string
  volumeId?: string
  caseSensitive: boolean
}

export interface TextReplacementMatch {
  id: string
  chapterId: string
  chapterTitle: string
  chapterIndex: number
  paragraphId?: string
  before: string
  matched: string
  after: string
  replacement: string
}

export interface TextReplacementWarning {
  entryId: string
  name: string
  kind: string
  matchedTerm: string
  referencedChapters: number
}

export interface TextReplacementPreview {
  previewToken: string
  totalMatches: number
  chapters: Array<{
    id: string
    title: string
    index: number
    volumeId?: string
    rev: number
    matchCount: number
  }>
  matches: TextReplacementMatch[]
  warnings: TextReplacementWarning[]
}

export interface TextReplacementRun {
  id: string
  status: 'applied' | 'undone'
  totalMatches: number
  affectedChapters: Array<{
    chapterId: string
    chapterTitle: string
    beforeRev: number
    afterRev: number
    matchCount: number
  }>
}

export interface ChapterPlanPatch {
  title: string
  outline: string[]
  outlineNote: string
  bodyNeedsRevision: boolean
  baseRevision: number
}

export interface Volume { id: string; index: number; title: string; summary?: string }

export interface ProjectTrash {
  volumes: Array<{ id: string; title: string; deletedAt: string }>
  chapters: Array<{
    id: string
    title: string
    words: number
    volumeId: string | null
    volumeTitle: string | null
    deletedAt: string
  }>
}

export interface ProjectPatch {
  title?: string
  genre?: string | null
  status?: 'ongoing' | 'finished' | 'archived'
  dailyGoal?: number
}

export interface Project {
  id: string
  title: string
  genre?: string | null
  status?: 'ongoing' | 'finished' | 'archived'
  volumes: Volume[]
  /** 全书统计来自项目摘要，不等于当前已加载到内存的章节列表。 */
  wordCount?: number
  chapterCount?: number
  dailyGoal: number
  dailyWords: number
  styleProfile: string | null
}

export type GuardKind = 'conflict' | 'foreshadow' | 'pending-entry'
export type GuardResolutionAction = 'accept_old_fact' | 'accept_new_fact' | 'intentional_exception' | 'false_positive' | 'fixed_in_body' | 'defer'
export type GuardArbitrationStatus = 'not_requested' | 'pending' | 'supported' | 'unsupported' | 'uncertain' | 'failed'

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
  actionCodes?: GuardResolutionAction[]
  issueRev?: number
  chapterId?: string
  resolved: boolean
  arbitrationStatus: GuardArbitrationStatus
  arbitrationConfidence?: number
  arbitrationRationale?: string
}

export interface ConsistencyRunOverview {
  chapterId: string
  chapterIndex: number
  chapterTitle: string
  bodyRev: number
  status: string
  phases: { extract: string; summary: string; scan: string }
  errorCode?: string
  errorDetail?: string
  updatedAt: string
}

export interface GuardOverview {
  status: 'idle' | 'queued' | 'running' | 'completed' | 'failed'
  queued: number
  running: number
  completed: number
  failed: number
  outboxPending: number
  outboxDeadLetter: number
  latestActivityAt?: string
  runs: ConsistencyRunOverview[]
}

export interface TimelineReflowResult {
  claimsExamined: number
  claimsChanged: number
  affectedChapterIds: string[]
  resolved: number
  unresolved: number
  ambiguous: number
  cyclic: number
  cycles: string[][]
  rescansQueued: number
  rescanRunIds: number[]
}

export type TimelinePlacementStatus = 'placed' | 'review' | 'ambiguous' | 'cyclic' | 'unplaced'

export interface TimelineBoardEvent {
  claimId: number
  timelineId: string
  eventRef: string
  chapterId: string
  chapterIndex: number
  chapterTitle: string
  timeText?: string
  storyOrder?: number
  placementStatus: TimelinePlacementStatus
  dependencyStatus: string
  relation?: string
  relationRef?: string
  sourceAnchor?: string
  confidence?: number
  resolutionSource?: string
}

export interface TimelineBoardLane {
  timelineId: string
  label: string
  eventCount: number
  placedCount: number
  reviewCount: number
  events: TimelineBoardEvent[]
}

export interface TimelineBoard {
  lanes: TimelineBoardLane[]
  eventCount: number
  placedCount: number
  reviewCount: number
  unplacedCount: number
  storyOrderMin?: number
  storyOrderMax?: number
}

export interface TemporalReviewItem {
  claimId: number
  chapterId?: string
  chapterIndex?: number
  chapterTitle?: string
  eventRef?: string
  relation?: 'before' | 'after' | 'simultaneous'
  relationRef?: string
  original: string
  normalized: string
  offsetMinSeconds: number
  offsetMaxSeconds: number
  dependencyStatus: string
  overrideSeconds?: number
  overrideVersion: number
}

export interface TemporalDecisionResult {
  item: TemporalReviewItem
  reflow: TimelineReflowResult
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
  instruction?: string
}

export interface InlineGenerateOptions extends GenerateOptions {
  action: '续写' | '扩写' | '润色' | '改写语气' | '按我的风格'
  selectedText: string
  nearbyText: string
}

export type GenerationControls = Omit<GenerateOptions, 'chapterId'>

export interface GenerationMeta {
  draftId?: string
  skills: string[]
  scene: string
  layers: Record<string, unknown>
  promptTokens: number
  model: string
}

export type GenerationDraftStatus = 'streaming' | 'ready' | 'failed' | 'accepted' | 'rejected'

export interface GenerationDraftSummary {
  id: string
  runId: string | null
  projectId: string
  chapterId: string
  kind: 'chapter' | 'inline'
  status: GenerationDraftStatus
  generatedWords: number
  excerpt: string
  requestSummary: {
    action?: InlineGenerateOptions['action']
    instruction?: string
    model?: GenerateOptions['model']
    targetWords?: number
    useStyleProfile?: boolean
    dialogueDensity?: GenerateOptions['dialogueDensity']
  }
  errorCode: string | null
  createdAt: string
  updatedAt: string
  acceptedAt: string | null
}

export interface GenerationDraftDetail extends GenerationDraftSummary {
  content: string
}
