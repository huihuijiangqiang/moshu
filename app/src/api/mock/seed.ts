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
  {
    id: 'c-shenyan', kind: 'character', name: '沈砚', aliases: ['沈将军', '砚哥'],
    summary: '北境守将，三十二岁。寡言，动手快于开口。左肩旧伤遇寒即痛。',
    resident: true, refChapters: Array.from({ length: 87 }, (_, i) => i + 1), status: 'confirmed', conflicts: 0,
    character: {
      role: '主角 · 雁回关守将', age: '三十二岁',
      appearance: '常年着旧式边军甲，左肩因旧伤略低；站姿克制，极少显露疲态。',
      personality: ['寡言', '认死理', '动手快于开口', '不肯托人情'],
      desire: '查清师父二十年前为何弃剑，以及旧盟究竟隐瞒了什么。',
      motivation: '先守住雁回关与关内的人，再循玄铁令追查师父留下的线索。',
      flaw: '不肯求人，把责任全部揽在自己身上；因此在第六境停滞四年，也四年未升。',
      fear: '成为一个只会守规矩、最后却守不住身边人的人。',
      ability: '淬铁第六境「同断」；能感知兵器残留的情绪，并与手中兵器分担损伤。',
      limitation: '境界不可越级；每次「听器」都会反噬旧伤，残锋断裂后能力稳定性下降。',
      speech: '只说必要的短句，很少解释，不用感叹语气；常用动作代替回答。',
      background: '少年时由师父授剑，后长期镇守北境。残锋、旧盟与玄铁令都指向二十年前的一次决裂。',
      currentState: '第 87 章：残锋已断；判断北狄撤军只是试探；准备进兵器坊追问玄铁令。',
      arc: {
        past: '相信守令就能守住一切，把师父留下的问题压了二十年。',
        current: '旧有秩序开始失效，他必须在军令与真相之间作出选择。',
        next: '主动违令北上，从被动守关转向追查并承担自己的选择。'
      }
    },
    relations: [
      { targetId: 'c-zhoutou', name: '老周头', relation: '知情者', note: '兵器坊铁匠，知道师父与玄铁令的旧事。' },
      { targetId: 'c-shifu', name: '师父', relation: '授业者', note: '赠残锋之人，也是沈砚当前追查的核心谜团。' },
      { targetId: 'c-beidi', name: '北狄', relation: '敌对势力', note: '当前叩关并非单纯撤军，更像在试探他的底线。' },
      { targetId: 'c-canfeng', name: '残锋', relation: '佩剑', note: '师父所赠，是能力媒介，也是人物情感锚点。' }
    ]
  },
  {
    id: 'c-zhoutou', kind: 'character', name: '老周头', aliases: ['周铁匠'],
    summary: '城南兵器坊铁匠，六十余。知晓玄铁令来历。与沈砚师父有旧。',
    resident: false, refChapters: [12, 33, 52, 61, 74, 80, 84, 86, 88], status: 'confirmed', conflicts: 0,
    character: {
      role: '兵器坊铁匠 · 旧事知情者', age: '六十余岁',
      appearance: '常年守着低矮炉台，手背满是烫痕；说话时很少停下手里的活。',
      personality: ['嘴硬', '谨慎', '念旧', '善于试探'],
      desire: '把欠沈砚师父的旧债还清，又不让玄铁令重新招来灾祸。',
      motivation: '守了半块玄铁令二十年，只等真正能承担后果的人来问。',
      flaw: '用沉默保护别人，也让误会延续太久。',
      fear: '二十年前的决裂在沈砚身上重演。',
      ability: '能辨认兵器内伤与淬铁痕迹，熟悉玄铁令的材质和旧盟工艺。',
      limitation: '不会淬铁术，且掌握的信息不完整；他只知道交付玄铁令的一段经过。',
      speech: '先拒绝、后点破；句子带停顿，常借打铁和修剑说人。',
      background: '与沈砚师父有旧，受托保存半块玄铁令，在城南兵器坊等了二十年。',
      currentState: '第 88 章：拒绝修残锋，准备交出半块玄铁令并说明它的来历。',
      arc: { past: '替故人守口如瓶。', current: '沈砚带着断剑来问，旧约到期。', next: '交出线索，但仍保留一件不愿说透的旧事。' }
    },
    relations: [
      { targetId: 'c-shenyan', name: '沈砚', relation: '晚辈', note: '试探他是否已经能承受旧盟真相。' },
      { targetId: 'c-shifu', name: '师父', relation: '故交', note: '替他保管玄铁令，也对当年的决裂负有愧疚。' },
      { targetId: 'c-xuantie', name: '玄铁令', relation: '保管物', note: '半块在他手里，边缘已被摩挲得发亮。' }
    ]
  },
  {
    id: 'c-canfeng', kind: 'item', name: '残锋', aliases: [], summary: '沈砚佩剑，师父所赠。第 41 章断于北狄叩关，此后未修复。',
    resident: true, refChapters: [1, 41, 85, 87], status: 'confirmed', conflicts: 1,
    facts: [
      { label: '来历', value: '沈砚师父所赠，所用铁料与玄铁令同源。' },
      { label: '持有者', value: '沈砚' },
      { label: '当前状态', value: '第 41 章自中段断裂，断口由内向外，尚未修复。' },
      { label: '能力关联', value: '是沈砚施展第六境「同断」的主要媒介；断裂后听器不稳。' },
      { label: '硬约束', value: '修复前不可写成完整剑身，也不能稳定承受越境力量。' }
    ],
    relations: [
      { targetId: 'c-shenyan', name: '沈砚', relation: '持有者', note: '佩剑与情感锚点。' },
      { targetId: 'c-shifu', name: '师父', relation: '赠予者', note: '二十年前将剑交给沈砚。' },
      { targetId: 'c-xuantie', name: '玄铁令', relation: '同源物', note: '断口暴露了相同的玄铁纹理。' }
    ]
  },
  {
    id: 'c-xuantie', kind: 'foreshadow', name: '玄铁令', aliases: [], summary: '埋于第 52 章，半块在老周头处。预计回收：第二卷末。',
    resident: false, refChapters: [52], status: 'confirmed', conflicts: 0, plantedAt: 52, expectedBy: '第二卷末',
    facts: [
      { label: '已知线索', value: '只现身半块，边缘被人摩挲二十年；材质与残锋同源。' },
      { label: '保管位置', value: '老周头的城南兵器坊。' },
      { label: '尚未揭示', value: '另一半持有者、旧盟用途，以及沈砚师父为何留下它。' },
      { label: '回收任务', value: '第二卷末揭示它是通行凭证还是结盟信物，并推动沈砚北上。' }
    ],
    relations: [
      { targetId: 'c-zhoutou', name: '老周头', relation: '保管者', note: '替故人保管半块二十年。' },
      { targetId: 'c-canfeng', name: '残锋', relation: '同源物', note: '两者铁料与断面纹路一致。' }
    ]
  },
  {
    id: 'c-beidi', kind: 'faction', name: '北狄', aliases: ['关外八部'], summary: '关外游牧联盟，八部共主。惯用试探性叩关，不留使者。',
    resident: false, refChapters: [3, 41, 85, 87], status: 'confirmed', conflicts: 0,
    facts: [
      { label: '构成', value: '关外八部组成的松散联盟，由共主在战时统一调度。' },
      { label: '当前目标', value: '探明雁回关守备虚实，以及沈砚是否仍能使用残锋。' },
      { label: '惯用手段', value: '小规模叩关、主动弃尸、不留使者，以异常撤退诱使守军误判。' },
      { label: '内部张力', value: '八部并非一心，共主需要战果维持号令。' }
    ],
    relations: [{ targetId: 'c-shenyan', name: '沈砚', relation: '主要对手', note: '正在试探他的伤势与守关底线。' }]
  },
  {
    id: 'c-shifu', kind: 'character', name: '师父', aliases: [], summary: '守卫从第 87 章正文抽取：赠剑者，二十年前，曾言「人断了才要紧」。尚无独立条目。',
    resident: false, refChapters: [87], status: 'pending', conflicts: 0,
    character: {
      role: '沈砚的授业者 · 身份待确认',
      speech: '已知原话：「剑断了不必换，人断了才要紧。」',
      background: '二十年前将残锋交给沈砚，后来弃剑离开；原因不明。',
      currentState: '仅由第 87 章回忆抽取，姓名、生死、去向均待确认。',
      arc: { past: '赠剑并留下告诫。', current: '通过残锋与玄铁令影响沈砚的选择。' }
    },
    relations: [
      { targetId: 'c-shenyan', name: '沈砚', relation: '弟子', note: '沈砚正追查他当年弃剑的原因。' },
      { targetId: 'c-zhoutou', name: '老周头', relation: '故交', note: '曾托付半块玄铁令。' }
    ]
  },
  {
    id: 'c-yanhui', kind: 'place', name: '雁回关', aliases: [], summary: '北境第一关，城南有洼地兵器坊。终年风雪，四月始融。',
    resident: false, refChapters: [1, 79, 85, 87, 88], status: 'confirmed', conflicts: 0,
    facts: [
      { label: '地理', value: '北境第一关，城墙正对雪原，城南地势最低。' },
      { label: '气候', value: '风雪期漫长，通常四月始融；寒潮会引发沈砚左肩旧伤。' },
      { label: '战略价值', value: '关外八部南下的第一道门，也是北境补给线的咽喉。' },
      { label: '关键地点', value: '南城洼地兵器坊、北垛口、旧军械库。' },
      { label: '当前局势', value: '北狄留下四百具尸首后异常撤退，守军仍处于戒备。' }
    ],
    relations: [{ targetId: 'c-beidi', name: '北狄', relation: '关外威胁', note: '持续以叩关测试防线。' }]
  },
  {
    id: 'c-cuitie', kind: 'system', name: '淬铁九境', aliases: [], summary: '以兵器与人相淬，境界不可越级。沈砚在第六境「同断」，已停滞四年。',
    resident: true, refChapters: [2, 41, 84], status: 'confirmed', conflicts: 1,
    facts: [
      { label: '核心规则', value: '修行者与一件兵器长期相淬，每境都改变人与器的联系。' },
      { label: '当前已知', value: '第六境「同断」可共担损伤；第七境「共鸣」可引动一定范围内的铁器。' },
      { label: '代价', value: '听器必伤，感知越深，兵器残留情绪对使用者的反噬越重。' },
      { label: '硬约束', value: '境界不可越级；兵器损坏会直接削弱或扭曲对应能力。' },
      { label: '主角进度', value: '沈砚停在第六境四年，第 84 章疑似越级使用第七境手段。' }
    ],
    relations: [
      { targetId: 'c-shenyan', name: '沈砚', relation: '第六境修行者', note: '当前存在一处疑似越级冲突。' },
      { targetId: 'c-canfeng', name: '残锋', relation: '能力媒介', note: '断裂后「同断」的稳定性下降。' }
    ]
  }
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
