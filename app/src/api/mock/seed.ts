import type { Chapter, CodexEntry, GuardIssue, Project, ContextLayer } from '@/types'

export const project: Project = {
  id: 'p1',
  title: '剑起山河',
  wordCount: 783000,
  chapterCount: 89,
  dailyGoal: 6000,
  dailyWords: 2780,
  styleProfile: '沈氏白描',
  volumes: [
    { id: 'v1', index: 1, title: '少年出山' },
    { id: 'v2', index: 2, title: '北境风雪' }
  ]
}

const CH87 = `<h1>第八十七章　断刃</h1>
<p>风雪压着城墙走了三日，第四日清晨忽然停了。<span data-codex-ref="c-shenyan"></span>站在垛口上，望着远处那片被踩得发黑的雪原，手里的<span data-codex-ref="c-canfeng"></span>还在渗血——不是敌人的。</p>
<p>他知道<span data-codex-ref="c-beidi"></span>不会就这么退。三日前那一场叩关，对方丢下四百具尸首，连一句话都没留。这不像撤军，更像是在称他这道关的斤两。</p>
<p>「将军，兵器坊那边说……」亲卫的声音在身后停住了。</p>
<p>沈砚没有回头。他把残锋横过来，指腹从断口上一路抹到剑柄。二十年前师父把这把剑交到他手上时说过，剑断了不必换，人断了才要紧。那时他不懂。</p>`

export const chapters: Chapter[] = [
  { id: 'ch85', volumeId: 'v2', index: 85, title: '雪夜叩关', words: 3200, status: 'done', outline: [], outlineNote: '', summary: '北狄夜袭雁回关，沈砚以残锋接战，敌军丢下四百尸首后退去。' },
  { id: 'ch86', volumeId: 'v2', index: 86, title: '旧盟之约', words: 3050, status: 'done', outline: [], outlineNote: '', summary: '沈砚清点伤亡，副将提及二十年前的旧盟，暗示朝中有人不愿北境立功。' },
  {
    id: 'ch87', volumeId: 'v2', index: 87, title: '断刃', words: 1412, status: 'drafting',
    outline: ['沈砚察觉北狄退兵是试探', '残锋断裂引出师父旧事', '兵器坊提及玄铁令'],
    outlineNote: '沈砚察觉北狄退兵是试探；残锋断裂引出师父旧事；卷末伏笔「玄铁令」在兵器坊被提及。',
    content: CH87
  },
  {
    id: 'ch88', volumeId: 'v2', index: 88, title: '玄铁令', words: 0, status: 'outlined',
    outline: ['沈砚入兵器坊', '老周头拒修残锋', '玄铁令来历揭示', '沈砚决意北上', '章末钩子：城头信号'],
    outlineNote: '老周头点出玄铁令来历，沈砚决意北上。章末留城头信号的钩子。'
  },
  {
    id: 'ch89', volumeId: 'v2', index: 89, title: '', words: 0, status: 'outlined',
    outline: ['北上途中遇伏', '第一次动用听器之力', '代价显现'],
    outlineNote: '北上途中遇伏，沈砚第一次主动动用金手指并付出代价。'
  }
]

export const codex: CodexEntry[] = [
  { id: 'c-shenyan', kind: 'character', name: '沈砚', aliases: ['沈将军', '砚哥'], summary: '北境守将，三十二岁。寡言，动手快于开口。左肩旧伤遇寒即痛。', resident: true, refChapters: Array.from({ length: 87 }, (_, i) => i + 1), status: 'confirmed', conflicts: 0 },
  { id: 'c-zhoutou', kind: 'character', name: '老周头', aliases: ['周铁匠'], summary: '城南兵器坊铁匠，六十余。知晓玄铁令来历。与沈砚师父有旧。', resident: false, refChapters: [12, 33, 52, 61, 74, 80, 84, 86, 88], status: 'confirmed', conflicts: 0 },
  { id: 'c-canfeng', kind: 'item', name: '残锋', aliases: [], summary: '沈砚佩剑，师父所赠。第 41 章断于北狄叩关，此后未修复。', resident: true, refChapters: [1, 41, 85, 87], status: 'confirmed', conflicts: 1 },
  { id: 'c-xuantie', kind: 'foreshadow', name: '玄铁令', aliases: [], summary: '埋于第 52 章，半块在老周头处。预计回收：第二卷末。', resident: false, refChapters: [52], status: 'confirmed', conflicts: 0, plantedAt: 52, expectedBy: '第二卷末' },
  { id: 'c-beidi', kind: 'faction', name: '北狄', aliases: ['关外八部'], summary: '关外游牧联盟，八部共主。惯用试探性叩关，不留使者。', resident: false, refChapters: [3, 41, 85, 87], status: 'confirmed', conflicts: 0 },
  { id: 'c-shifu', kind: 'character', name: '师父', aliases: [], summary: '守卫从第 87 章正文抽取：赠剑者，二十年前，曾言「人断了才要紧」。尚无独立条目。', resident: false, refChapters: [87], status: 'pending', conflicts: 0 },
  { id: 'c-yanhui', kind: 'place', name: '雁回关', aliases: [], summary: '北境第一关，城南有洼地兵器坊。终年风雪，四月始融。', resident: false, refChapters: [1, 79, 85, 87, 88], status: 'confirmed', conflicts: 0 },
  { id: 'c-cuitie', kind: 'system', name: '淬铁九境', aliases: [], summary: '以兵器与人相淬，境界不可越级。沈砚在第六境「同断」，已停滞四年。', resident: true, refChapters: [2, 41, 84], status: 'confirmed', conflicts: 1 }
]

export const guardIssues: GuardIssue[] = [
  {
    id: 'g1', kind: 'conflict', severity: 'high', category: '器物状态',
    title: '残锋在第 41 章已断，此处描写为完整可用', chapterRef: '第 87 章 · 第 3 段',
    evidence: [
      { label: '第 41 章', text: '「……剑身自中段裂开，半截没入雪里。」' },
      { label: '第 87 章 · 本次', text: '「手里的残锋还在渗血——不是敌人的。」', accent: true }
    ],
    actions: ['以第 41 章为准，改写本段', '以本章为准，更新设定', '有意为之，忽略'],
    resolved: false
  },
  {
    id: 'g2', kind: 'conflict', severity: 'high', category: '人物能力',
    title: '沈砚越级使用第七境手段，与「境界不可越级」规则冲突', chapterRef: '第 84 章 · 第 11 段',
    evidence: [
      { label: '设定 · 淬铁九境', text: '「境界不可越级。沈砚在第六境『同断』，已停滞四年。」' },
      { label: '第 84 章', text: '「他抬手一引，雪中三十步内的铁器齐齐震鸣。」（第七境「共鸣」的表现）', accent: true }
    ],
    actions: ['改写第 84 章', '补一段破境情节', '有意为之，忽略'],
    resolved: false
  },
  {
    id: 'g3', kind: 'conflict', severity: 'mid', category: '时间线',
    title: '「三日后」与「雪停于四月」在时序上无法同时成立', chapterRef: '第 79 - 86 章',
    detail: '第 79 章交代雁回关四月始融雪，第 86 章「风雪压着城墙走了三日」，按前后章推算此时应为五月中。二者需调其一。',
    evidence: [],
    actions: ['查看时间线视图', '有意为之，忽略'],
    resolved: false
  },
  {
    id: 'g4', kind: 'foreshadow', severity: 'mid', category: '伏笔',
    title: '玄铁令已 35 章未提', chapterRef: '埋于第 52 章',
    detail: '预计回收点为第二卷末，当前已写至第 87 章。超过 30 章未提及会持续提醒。',
    evidence: [], actions: ['在下一章提及', '调整预计回收点', '忽略'], resolved: false
  },
  {
    id: 'g5', kind: 'pending-entry', severity: 'mid', category: '新设定',
    title: '「师父」尚无条目，是否入库？', chapterRef: '第 87 章 · 第 4 段',
    detail: '守卫从正文抽取到一个反复出现但未建条目的人物。入库后会进入常驻上下文候选。',
    evidence: [], actions: ['入库', '忽略'], resolved: false
  }
]

export const contextLayers: ContextLayer[] = [
  { key: 'resident', label: '稳定设定 · 常驻', detail: '3 条常驻条目 + 风格档', tokens: 5800 },
  { key: 'retrieved', label: '相关条目', detail: '按章纲实体检索命中 6 项', tokens: 4100 },
  { key: 'summary', label: '前情摘要', detail: '章摘要至 86 章 + 卷摘要', tokens: 3200 },
  { key: 'adjacent', label: '相邻原文', detail: '第 85-86 章，优先章末', tokens: 8300 }
]

/** 一键成章的 mock 输出。真实实现由后端 SSE 推送。 */
export const draftParagraphs: string[] = [
  '兵器坊在城南最低的那片洼地里，终年不见太阳。沈砚推门进去时，炉火正旺，老周头背对着他，手里那块铁被砸得发白。',
  '「你那把剑，我修不了。」老周头没抬头，「断口是从里头裂开的，不是砍崩的。这铁本来就不该拿去砍人。」沈砚把残锋放在案上，铁与木相触，发出很轻的一声。',
  '「二十年前送你师父这块料子的人，」老周头终于转过身，火光把他半张脸照得通红，「留了半块在我这儿。他说，等哪天有人拿着断了的那半来问，就把这个给他。」',
  '沈砚没有接。他看着那半块铁在老人掌心里，边缘被磨得发亮，显然被摸了二十年。「他为什么不自己来。」「因为他知道你会来。」'
]
