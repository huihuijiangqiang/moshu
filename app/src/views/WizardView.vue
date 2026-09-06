<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { projectPath } from '@/router/project-route'
import { shelfApi } from '@/api/shelf'
import { planWizard, type WizardPlan } from '@/api/wizard'

type Audience = 'male' | 'female' | 'general'

interface Choice {
  id: string
  label: string
  note: string
  cue: string
}

interface GenreOption {
  id: string
  label: string
  note: string
}

interface GenreGroup {
  id: string
  label: string
  genres: GenreOption[]
}

interface VolumeDraft {
  title: string
  summary: string
}

interface ChapterDraft {
  title: string
  outline: string
}

const DRAFT_KEY = 'moshu:new-project-draft'
const router = useRouter()
const step = ref(1)
const highestStep = ref(1)
const inspiration = ref('')
const audience = ref<Audience>('male')
const genreGroupId = ref('fantasy')
const genreId = ref('')
const tagIds = ref<string[]>([])
const customGenre = ref('')
const templateId = ref('')
const bookTitle = ref('')
const protagonist = ref('')
const coreHook = ref('')
const synopsis = ref('')
const volumes = ref<VolumeDraft[]>([])
const expandedOutline = ref(false)
const variant = ref(0)
const creating = ref(false)
const createError = ref('')
const planning = ref(false)
const planningError = ref('')
const chapters = ref<ChapterDraft[]>([])

const inspirations = [
  '一个守关将军能听见兵器记忆，却发现佩剑一直在替师父撒谎。',
  '所有人都忘记了昨夜，只有停尸房里的一具尸体记得。',
  '她替姐姐嫁进敌国，婚书上却写着自己十年前用过的名字。',
  '末日后的城市按记忆缴税，欠税的人会忘掉最重要的人。'
]

const genreGroups: GenreGroup[] = [
  {
    id: 'fantasy', label: '玄幻仙侠', genres: [
      { id: 'oriental-fantasy', label: '东方玄幻', note: '境界、血脉与宏大世界' },
      { id: 'xianxia', label: '传统仙侠', note: '修行、宗门与问道长生' },
      { id: 'cultivation', label: '现代修真', note: '现代社会中的修行秩序' },
      { id: 'high-martial', label: '高武世界', note: '武道体系与强者成长' },
      { id: 'western-fantasy', label: '西方奇幻', note: '魔法、种族与异域文明' },
      { id: 'aura-revival', label: '灵气复苏', note: '旧秩序下的超凡觉醒' }
    ]
  },
  {
    id: 'urban', label: '都市现实', genres: [
      { id: 'urban-superpower', label: '都市异能', note: '现代生活中的异常能力' },
      { id: 'urban-life', label: '都市生活', note: '职业、家庭与城市关系' },
      { id: 'business', label: '商战职场', note: '利益竞争与职业成长' },
      { id: 'entertainment', label: '娱乐明星', note: '行业生态与公众身份' },
      { id: 'realism', label: '现实题材', note: '真实处境与社会切面' },
      { id: 'medical', label: '医道', note: '诊疗现场与生命抉择' }
    ]
  },
  {
    id: 'history', label: '历史军事', genres: [
      { id: 'alternate-history', label: '架空历史', note: '重构时代与制度走向' },
      { id: 'historical-fiction', label: '历史正剧', note: '真实时代中的人物命运' },
      { id: 'court-strategy', label: '古代权谋', note: '朝堂、身份与政治博弈' },
      { id: 'military', label: '战争军事', note: '战场决策与阵营冲突' },
      { id: 'espionage', label: '谍战特工', note: '潜伏、情报与身份危机' },
      { id: 'wuxia', label: '传统武侠', note: '江湖秩序与侠义抉择' }
    ]
  },
  {
    id: 'suspense-scifi', label: '悬疑科幻', genres: [
      { id: 'mystery', label: '悬疑推理', note: '线索、误导与真相还原' },
      { id: 'folk-horror', label: '民俗怪谈', note: '地方传说与未知禁忌' },
      { id: 'future-scifi', label: '科幻未来', note: '技术变革与文明选择' },
      { id: 'apocalypse', label: '末世危机', note: '灾变生存与秩序重建' },
      { id: 'infinite-flow', label: '无限流', note: '多世界任务与规则求生' },
      { id: 'cyberpunk', label: '赛博朋克', note: '技术垄断与身份边界' }
    ]
  },
  {
    id: 'romance', label: '言情情感', genres: [
      { id: 'ancient-romance', label: '古代言情', note: '古代关系与身份约束' },
      { id: 'modern-romance', label: '现代言情', note: '当代关系与个人选择' },
      { id: 'campus', label: '青春校园', note: '成长阶段与青春关系' },
      { id: 'wealthy-family', label: '豪门世家', note: '家族利益与亲密关系' },
      { id: 'workplace-romance', label: '婚恋职场', note: '事业压力与情感协商' },
      { id: 'fantasy-romance', label: '幻想言情', note: '幻想规则中的情感命题' }
    ]
  },
  {
    id: 'game-light', label: '游戏轻小说', genres: [
      { id: 'gaming', label: '游戏竞技', note: '玩法规则与团队对抗' },
      { id: 'sports', label: '体育竞技', note: '训练、赛场与职业生涯' },
      { id: 'light-novel', label: '轻小说', note: '轻快表达与角色驱动' },
      { id: 'anime', label: '二次元', note: '亚文化语境与幻想冒险' },
      { id: 'fanfiction', label: '同人衍生', note: '基于既有世界的再创作' }
    ]
  },
  { id: 'custom', label: '自定义', genres: [] }
]

const contentTags = [
  { id: 'case-solving', label: '探案' },
  { id: 'ensemble', label: '群像' },
  { id: 'growth', label: '成长' },
  { id: 'revenge', label: '复仇' },
  { id: 'management', label: '经营' },
  { id: 'system', label: '系统' },
  { id: 'transmigration', label: '穿越' },
  { id: 'rebirth', label: '重生' },
  { id: 'survival', label: '求生' },
  { id: 'romance-line', label: '感情线' },
  { id: 'politics', label: '权谋' },
  { id: 'war', label: '战争' }
]

const templates: Choice[] = [
  { id: 'growth', label: '升级闯关', note: '目标逐级抬高，每卷解决一个更大的阻碍', cue: '强推进' },
  { id: 'mystery', label: '谜团追索', note: '答案不断改写问题，真相分层揭露', cue: '强悬念' },
  { id: 'reversal', label: '身份翻转', note: '隐藏身份持续改变人物关系和选择', cue: '强反转' },
  { id: 'ensemble', label: '群像经营', note: '多人物目标交叉，靠关系变化推动剧情', cue: '强关系' }
]

const leadVariants = [
  ['沈砚 · 三十二岁 · 北境守将', '寡言，认死理，不肯托人情。左肩旧伤遇寒即痛，越想独自承担，越会把身边人推远。'],
  ['陆沉舟 · 二十七岁 · 兵器坊弃徒', '擅长识器却拒绝佩剑，凡事先算退路。真正的缺陷不是胆小，而是不相信任何承诺能长久。'],
  ['谢照微 · 二十九岁 · 失籍校尉', '判断快、记仇，也愿意替弱者担责。她最怕身份被揭穿，因此每次立功都让自己更接近暴露。']
]

const hookVariants = [
  ['淬铁 · 以器听人', '能听见兵器残留的持有者情绪，但每次倾听都会承受同样的伤痛，听得越深，记忆越容易混入自己。'],
  ['断铭 · 改写一件兵器的旧主', '可以抹去兵器对旧主的认定，代价是自己也会永久失去一段与旧主有关的记忆。'],
  ['同断 · 人器共担', '能让持有者与兵器分担致命损伤；兵器一旦彻底折断，所有积压的伤会同时回到人身上。']
]

const selectedGenreGroup = computed(() => genreGroups.find((group) => group.id === genreGroupId.value) ?? genreGroups[0])
const visibleGenres = computed(() => selectedGenreGroup.value?.genres ?? [])
const selectedGenre = computed<GenreOption | null>(() => {
  if (genreGroupId.value === 'custom') {
    const label = customGenre.value.trim()
    return label ? { id: 'custom', label, note: '作者自定义题材' } : null
  }
  return genreGroups.flatMap((group) => group.genres).find((item) => item.id === genreId.value) ?? null
})
const selectedTags = computed(() => contentTags.filter((tag) => tagIds.value.includes(tag.id)))
const selectedTemplate = computed(() => templates.find((item) => item.id === templateId.value) ?? null)
const audienceLabel = computed(() => audience.value === 'male' ? '男频' : audience.value === 'female' ? '女频' : '通用')
const inspirationValid = computed(() => inspiration.value.trim().length >= 8)
const choicesValid = computed(() => !!selectedGenre.value && !!templateId.value)
const skeletonValid = computed(() =>
  !!bookTitle.value.trim() && !!protagonist.value.trim() && !!coreHook.value.trim() && !!synopsis.value.trim() && chapters.value.length === 3
)

const steps = computed(() => [
  { n: 1, code: '01', title: '一句灵感', note: inspirationValid.value ? '已填写' : '等待输入' },
  { n: 2, code: '02', title: '题材与模板', note: choicesValid.value ? `${selectedGenre.value?.label} · ${selectedTemplate.value?.label}` : '等待选择' },
  { n: 3, code: '03', title: '设定与总纲', note: skeletonValid.value ? '可继续修改' : '等待生成' },
  { n: 4, code: '04', title: '创建作品', note: step.value === 4 ? '准备创建' : '待开始' }
])

const guidance = computed(() => {
  if (step.value === 1) return [
    ['只写冲突，不写设定集', '一句话里最好同时出现“谁”“想做什么”和“哪里不对劲”。'],
    ['保留一个未知数', '不要在灵感阶段解释全部原因，未知数会成为后续总纲的发动机。']
  ]
  if (step.value === 2) return [
    ['题材决定读者预期', '题材控制世界材料和常见爽点，不会限制具体故事。'],
    ['模板决定推进方式', '同一句灵感用不同模板，会得到完全不同的卷结构和章节钩子。']
  ]
  if (step.value === 3) return [
    ['主角必须有挡路的缺陷', '能力解决外部问题，缺陷负责制造人物自己的问题。'],
    ['限制比能力更重要', '每次使用都要付出可感知的代价，战斗和选择才有张力。'],
    ['每卷只回答一层问题', '卷末给出阶段答案，同时把更大的问题推到下一卷。']
  ]
  return [
    ['创建后仍可修改', '书名、设定和总纲会进入对应页面，不会在这一步锁死。'],
    ['先核对故事方向', '确认灵感、题材和推进模板一致，再进入正式写作。']
  ]
})

function chooseInspiration(value: string) {
  inspiration.value = value
}

function chooseGenreGroup(groupId: string) {
  genreGroupId.value = groupId
  genreId.value = ''
}

async function buildSkeleton(force = false) {
  if (!force && skeletonValid.value) return
  if (planning.value || !selectedGenre.value || !selectedTemplate.value) return
  planning.value = true
  planningError.value = ''
  try {
    const plan = await planWizard({
      inspiration: inspiration.value.trim(),
      audience: audienceLabel.value,
      genre: selectedGenre.value.label,
      tags: selectedTags.value.map((tag) => tag.label),
      template: selectedTemplate.value.label
    })
    applyPlan(plan)
  } catch {
    planningError.value = '故事骨架生成失败，选择“重试生成”再试一次。'
  } finally {
    planning.value = false
  }
}

function applyPlan(plan: WizardPlan) {
  bookTitle.value = plan.title
  protagonist.value = plan.protagonist
  coreHook.value = plan.coreHook
  synopsis.value = plan.synopsis
  volumes.value = plan.volumes.map((volume) => ({ ...volume }))
  chapters.value = plan.chapters.map((chapter) => ({ title: chapter.title, outline: chapter.outline.join('\n') }))
}

function buildLocalSkeleton() {
  const lead = leadVariants[variant.value % leadVariants.length]
  const hook = hookVariants[variant.value % hookVariants.length]
  const genre = selectedGenre.value?.label ?? '幻想'
  const structure = selectedTemplate.value?.label ?? '谜团追索'
  const tagDescription = selectedTags.value.length ? `，并重点强化${selectedTags.value.map((tag) => tag.label).join('、')}` : ''

  bookTitle.value = ['残锋照雪', '旧盟无声', '雁回长夜'][variant.value % 3] ?? '未命名作品'
  protagonist.value = `${lead?.[0]}\n${lead?.[1]}`
  coreHook.value = `${hook?.[0]}\n${hook?.[1]}`
  synopsis.value = `${inspiration.value.trim()} 故事采用“${structure}”推进${tagDescription}：主角先因一次无法回避的选择被卷入冲突，再发现个人困境与更大的秩序有关。${genre}题材的规则会服务于人物选择，而不是单独堆砌说明。`
  volumes.value = [
    { title: '第一卷 · 入局', summary: '用一次具体失败立住人物缺陷，抛出核心谜面，并让主角失去原本的退路。' },
    { title: '第二卷 · 试锋', summary: '外部对手开始主动施压，能力代价第一次造成不可逆后果，旧线索出现矛盾。' },
    { title: '第三卷 · 旧盟', summary: '阶段真相揭开，主角发现自己一直相信的因果并不完整，被迫作出违背旧原则的选择。' },
    { title: '第四卷 · 同断', summary: '人物缺陷与核心冲突正面碰撞，付清能力代价，完成这一阶段的关系与谜团回收。' }
  ]
  chapters.value = [
    { title: '第 1 章 · 入局', outline: '建立主角处境\n日常被事件打破\n主角做出第一次选择' },
    { title: '第 2 章 · 试探', outline: '追查第一条线索\n遭遇具体阻力\n留下新的疑问' },
    { title: '第 3 章 · 旧痕', outline: '发现关键证据\n关系发生变化\n以未解钩子收束' }
  ]
}

function regenerateAll() {
  variant.value += 1
  void buildSkeleton(true)
}

function regenerateLead() {
  variant.value += 1
  const lead = leadVariants[variant.value % leadVariants.length]
  protagonist.value = `${lead?.[0]}\n${lead?.[1]}`
}

function regenerateHook() {
  variant.value += 1
  const hook = hookVariants[variant.value % hookVariants.length]
  coreHook.value = `${hook?.[0]}\n${hook?.[1]}`
}

async function next() {
  if (step.value === 1 && !inspirationValid.value) return
  if (step.value === 2 && !choicesValid.value) return
  if (step.value === 2) {
    await buildSkeleton()
    if (planningError.value || !skeletonValid.value) return
  }
  if (step.value === 3 && !skeletonValid.value) return
  step.value = Math.min(4, step.value + 1)
  highestStep.value = Math.max(highestStep.value, step.value)
}

function previous() {
  step.value = Math.max(1, step.value - 1)
}

function goToStep(target: number) {
  if (target <= highestStep.value) step.value = target
}

function resetDraft() {
  step.value = 1
  highestStep.value = 1
  inspiration.value = ''
  audience.value = 'male'
  genreGroupId.value = 'fantasy'
  genreId.value = ''
  tagIds.value = []
  customGenre.value = ''
  templateId.value = ''
  bookTitle.value = ''
  protagonist.value = ''
  coreHook.value = ''
  synopsis.value = ''
  volumes.value = []
  chapters.value = []
  expandedOutline.value = false
  variant.value = 0
  localStorage.removeItem(DRAFT_KEY)
}

async function createProject() {
  if (creating.value || !skeletonValid.value || !selectedGenre.value) return
  creating.value = true
  createError.value = ''
  try {
    const book = await shelfApi.createBook({
      title: bookTitle.value,
      genre: [audienceLabel.value, selectedGenre.value.label, ...selectedTags.value.map((tag) => tag.label)].join(' · '),
      inspiration: inspiration.value,
      synopsis: synopsis.value,
      protagonist: protagonist.value,
      coreHook: coreHook.value,
      audience: audienceLabel.value,
      template: selectedTemplate.value?.label ?? '',
      tags: selectedTags.value.map((tag) => tag.label),
      volumes: volumes.value,
      chapters: chapters.value.map((chapter) => ({
        title: chapter.title,
        outline: chapter.outline.split(/\n+/).map((beat) => beat.trim()).filter(Boolean)
      }))
    })
    localStorage.removeItem(DRAFT_KEY)
    await router.push(projectPath(book.id, 'outline'))
  } catch {
    createError.value = '创建失败，请保留当前草稿后重试。'
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  const saved = localStorage.getItem(DRAFT_KEY)
  if (!saved) return
  try {
    const draft = JSON.parse(saved)
    step.value = Math.min(4, Math.max(1, Number(draft.step) || 1))
    highestStep.value = Math.min(4, Math.max(step.value, Number(draft.highestStep) || 1))
    inspiration.value = typeof draft.inspiration === 'string' ? draft.inspiration : ''
    audience.value = ['male', 'female', 'general'].includes(draft.audience) ? draft.audience : 'male'
    const legacyGenres: Record<string, [string, string]> = {
      fantasy: ['fantasy', 'oriental-fantasy'],
      urban: ['urban', 'urban-superpower'],
      suspense: ['suspense-scifi', 'mystery'],
      court: ['history', 'court-strategy'],
      romance: ['romance', 'modern-romance'],
      scifi: ['suspense-scifi', 'apocalypse']
    }
    const legacyGenre = typeof draft.genreId === 'string' ? legacyGenres[draft.genreId] : undefined
    genreGroupId.value = legacyGenre?.[0] ?? (typeof draft.genreGroupId === 'string' ? draft.genreGroupId : 'fantasy')
    genreId.value = legacyGenre?.[1] ?? (typeof draft.genreId === 'string' ? draft.genreId : '')
    tagIds.value = Array.isArray(draft.tagIds)
      ? draft.tagIds.filter((id: unknown): id is string => typeof id === 'string').slice(0, 3)
      : []
    customGenre.value = typeof draft.customGenre === 'string' ? draft.customGenre : ''
    templateId.value = typeof draft.templateId === 'string' ? draft.templateId : ''
    bookTitle.value = typeof draft.bookTitle === 'string' ? draft.bookTitle : ''
    protagonist.value = typeof draft.protagonist === 'string' ? draft.protagonist : ''
    coreHook.value = typeof draft.coreHook === 'string' ? draft.coreHook : ''
    synopsis.value = typeof draft.synopsis === 'string' ? draft.synopsis : ''
    volumes.value = Array.isArray(draft.volumes) ? draft.volumes : []
    chapters.value = Array.isArray(draft.chapters)
      ? draft.chapters.filter((item: unknown): item is ChapterDraft => {
        if (!item || typeof item !== 'object') return false
        const value = item as { title?: unknown; outline?: unknown }
        return typeof value.title === 'string' && Array.isArray(value.outline)
          && value.outline.every((beat: unknown) => typeof beat === 'string')
      }).slice(0, 3).map((item: { title: string; outline: string[] }) => ({ title: item.title, outline: item.outline.join('\n') }))
      : []
    variant.value = Number(draft.variant) || 0
  } catch {
    localStorage.removeItem(DRAFT_KEY)
  }
})

watch(
  () => ({
    step: step.value,
    highestStep: highestStep.value,
    inspiration: inspiration.value,
    audience: audience.value,
    genreGroupId: genreGroupId.value,
    genreId: genreId.value,
    tagIds: tagIds.value,
    customGenre: customGenre.value,
    templateId: templateId.value,
    bookTitle: bookTitle.value,
    protagonist: protagonist.value,
    coreHook: coreHook.value,
    synopsis: synopsis.value,
    volumes: volumes.value,
    chapters: chapters.value,
    variant: variant.value
  }),
  (draft) => localStorage.setItem(DRAFT_KEY, JSON.stringify(draft)),
  { deep: true }
)
</script>

<template>
  <div class="wizard-page">
    <header class="wizard-header">
      <button class="wizard-brand" type="button" @click="router.push('/')">墨枢 · 开新书</button>
      <span>草稿已自动保留</span>
      <button class="wk-btn wk-btn-xs" type="button" @click="resetDraft">重新开始</button>
    </header>

    <nav class="wizard-steps" aria-label="创建作品步骤">
      <button
        v-for="item in steps"
        :key="item.n"
        type="button"
        :disabled="item.n > highestStep"
        :aria-current="item.n === step ? 'step' : undefined"
        :data-complete="item.n < highestStep"
        @click="goToStep(item.n)"
      >
        <span class="wizard-step-number">{{ item.code }}</span>
        <span><strong>{{ item.title }}</strong><small>{{ item.note }}</small></span>
      </button>
    </nav>

    <div class="wizard-workspace">
      <main class="wizard-main">
        <section v-if="step === 1" class="wizard-stage" aria-labelledby="inspiration-title">
          <header class="wizard-stage-head">
            <span class="wk-label">故事起点</span>
            <h1 id="inspiration-title">先写下一句话灵感</h1>
            <p>不用完整，只要能看见人物、冲突和一个反常之处。</p>
          </header>

          <label class="wizard-field">
            <span>一句话灵感</span>
            <textarea
              v-model="inspiration"
              rows="5"
              maxlength="180"
              autofocus
              placeholder="例如：一个守关将军能听见兵器记忆，却发现佩剑一直在替师父撒谎。"
            />
            <small :data-alert="inspiration.length > 0 && !inspirationValid">{{ inspiration.length }} / 180 字{{ inspiration.length > 0 && !inspirationValid ? ' · 至少写 8 个字' : '' }}</small>
          </label>

          <div class="wizard-examples">
            <span class="wk-label">或者从一句开始改</span>
            <button v-for="item in inspirations" :key="item" type="button" @click="chooseInspiration(item)">{{ item }}</button>
          </div>
        </section>

        <section v-else-if="step === 2" class="wizard-stage" aria-labelledby="genre-title">
          <header class="wizard-stage-head">
            <span class="wk-label">读者预期</span>
            <h1 id="genre-title">先定题材，再补充故事元素</h1>
            <p>主题材决定读者的第一预期；内容标签可以叠加，表达一本书真正的混合味道。</p>
          </header>

          <fieldset class="wizard-choice-group wizard-audience">
            <legend>读者方向</legend>
            <label v-for="item in ([['male', '男频'], ['female', '女频'], ['general', '通用']] as const)" :key="item[0]">
              <input v-model="audience" type="radio" name="audience" :value="item[0]">
              <span>{{ item[1] }}</span>
            </label>
          </fieldset>

          <fieldset class="wizard-choice-group wizard-genre-picker">
            <legend>题材分类</legend>
            <div class="wizard-genre-tabs" role="group" aria-label="题材分类">
              <button
                v-for="group in genreGroups"
                :key="group.id"
                type="button"
                :aria-pressed="genreGroupId === group.id"
                @click="chooseGenreGroup(group.id)"
              >{{ group.label }}</button>
            </div>

            <div class="wizard-subgenre-head">
              <strong>{{ genreGroupId === 'custom' ? '写下你的题材' : '选择细分题材' }}</strong>
              <span v-if="selectedGenre">已选：{{ selectedGenre.label }}</span>
            </div>
            <div class="wizard-genre-grid">
              <label v-for="item in visibleGenres" :key="item.id" :data-selected="genreId === item.id">
                <input v-model="genreId" type="radio" name="genre" :value="item.id">
                <strong>{{ item.label }}</strong><span>{{ item.note }}</span>
              </label>
            </div>
            <label v-if="genreGroupId === 'custom'" class="wizard-custom-genre">
              <span>自定义题材</span>
              <input v-model="customGenre" type="text" maxlength="20" placeholder="例如：民国工业探险">
              <small>{{ customGenre.trim().length }} / 20 字</small>
            </label>
          </fieldset>

          <fieldset class="wizard-choice-group wizard-tag-picker">
            <legend>内容标签 <small>可选，最多 3 个</small></legend>
            <div class="wizard-tag-list">
              <label v-for="item in contentTags" :key="item.id" :data-selected="tagIds.includes(item.id)">
                <input
                  v-model="tagIds"
                  type="checkbox"
                  :value="item.id"
                  :disabled="tagIds.length >= 3 && !tagIds.includes(item.id)"
                >
                <span>{{ item.label }}</span>
              </label>
            </div>
          </fieldset>

          <fieldset class="wizard-choice-group">
            <legend>故事模板</legend>
            <div class="wizard-template-list">
              <label v-for="item in templates" :key="item.id" :data-selected="templateId === item.id">
                <input v-model="templateId" type="radio" name="template" :value="item.id">
                <span><strong>{{ item.label }}</strong><small>{{ item.cue }}</small></span>
                <p>{{ item.note }}</p>
              </label>
            </div>
          </fieldset>
        </section>

        <section v-else-if="step === 3" class="wizard-stage" aria-labelledby="skeleton-title">
          <header class="wizard-stage-head">
            <span class="wk-label">{{ selectedGenre?.label }} · {{ selectedTemplate?.label }}</span>
            <h1 id="skeleton-title">这是根据你的选择搭出的骨架</h1>
            <p>这些内容会进入设定库和大纲，现在都可以直接修改。</p>
          </header>

          <div v-if="planning" class="wizard-plan-status" role="status">正在根据你的选择生成故事骨架…</div>
          <div v-else-if="planningError" class="wizard-plan-status" data-error role="alert">
            {{ planningError }}
            <button type="button" @click="void buildSkeleton(true)">重试生成</button>
          </div>

          <label class="wizard-field wizard-field-short"><span>暂定书名</span><input v-model="bookTitle" type="text"></label>

          <div class="wizard-edit-section">
            <div class="wizard-edit-head"><span>主角</span><button type="button" @click="regenerateLead">换一个</button></div>
            <textarea v-model="protagonist" rows="4" aria-label="主角设定" />
          </div>

          <div class="wizard-edit-section wizard-chapter-plans">
            <div class="wizard-edit-head"><span>前三章章纲</span><small>创建后可在大纲页持续修改</small></div>
            <label v-for="(chapter, index) in chapters" :key="index" class="wizard-chapter-plan">
              <span>{{ String(index + 1).padStart(2, '0') }}</span>
              <input v-model="chapter.title" :aria-label="`第 ${index + 1} 章标题`">
              <textarea v-model="chapter.outline" rows="3" :aria-label="`第 ${index + 1} 章章纲`" />
            </label>
          </div>

          <div class="wizard-edit-section">
            <div class="wizard-edit-head"><span>核心机制</span><button type="button" @click="regenerateHook">换一个</button></div>
            <textarea v-model="coreHook" rows="4" aria-label="核心机制" />
          </div>

          <label class="wizard-field"><span>故事总述</span><textarea v-model="synopsis" rows="5" /></label>

          <div class="wizard-edit-section">
            <div class="wizard-edit-head">
              <span>四卷总纲</span>
              <button type="button" @click="expandedOutline = !expandedOutline">{{ expandedOutline ? '收起细纲' : '展开细纲' }}</button>
            </div>
            <div class="wizard-volumes">
              <label v-for="(volume, index) in volumes" :key="index">
                <span>{{ String(index + 1).padStart(2, '0') }}</span>
                <input v-model="volume.title" :aria-label="`第 ${index + 1} 卷标题`">
                <textarea v-if="expandedOutline" v-model="volume.summary" rows="2" :aria-label="`第 ${index + 1} 卷细纲`" />
                <p v-else>{{ volume.summary }}</p>
              </label>
            </div>
          </div>
        </section>

        <section v-else class="wizard-stage wizard-review" aria-labelledby="review-title">
          <header class="wizard-stage-head">
            <span class="wk-label">创建前核对</span>
            <h1 id="review-title">{{ bookTitle }}</h1>
            <p>{{ inspiration }}</p>
          </header>

          <dl>
            <div><dt>读者方向</dt><dd>{{ audienceLabel }}</dd></div>
            <div><dt>题材</dt><dd>{{ selectedGenre?.label }}</dd></div>
            <div><dt>故事模板</dt><dd>{{ selectedTemplate?.label }}</dd></div>
            <div><dt>卷数</dt><dd>{{ volumes.length }} 卷</dd></div>
          </dl>

          <div v-if="selectedTags.length" class="wizard-review-tags">
            <strong>内容标签</strong>
            <span v-for="tag in selectedTags" :key="tag.id">{{ tag.label }}</span>
          </div>

          <div class="wizard-review-copy">
            <strong>故事总述</strong><p>{{ synopsis }}</p>
          </div>
          <p v-if="createError" class="wizard-create-error">{{ createError }}</p>
          <button class="btn btn-primary wizard-create" type="button" :disabled="creating" @click="createProject">
            {{ creating ? '创建中…' : '创建作品并进入大纲' }}
          </button>
        </section>

        <footer class="wizard-actions">
          <button v-if="step > 1" class="btn btn-secondary" type="button" @click="previous">上一步</button>
          <button v-if="step === 3" class="btn btn-secondary" type="button" @click="regenerateAll">重新生成骨架</button>
          <button
            v-if="step < 4"
            class="btn btn-primary"
            type="button"
            :disabled="planning || (step === 1 ? !inspirationValid : step === 2 ? !choicesValid : !skeletonValid)"
            @click="next"
          >{{ step === 3 ? '确认故事骨架' : '继续' }}</button>
        </footer>
      </main>

      <aside class="wizard-aside">
        <span class="wk-label">当前选择</span>
        <blockquote v-if="inspiration">{{ inspiration }}</blockquote>
        <p v-else class="wizard-aside-empty">写下一句话后，它会一直留在这里作为后续选择的判断标准。</p>

        <div v-if="selectedGenre || selectedTemplate" class="wizard-selection-summary">
          <span v-if="selectedGenre">{{ selectedGenre.label }}</span>
          <span v-if="selectedTemplate">{{ selectedTemplate.label }}</span>
          <span v-for="tag in selectedTags" :key="tag.id" class="is-tag">{{ tag.label }}</span>
        </div>

        <div class="wizard-guidance">
          <article v-for="([title, body], index) in guidance" :key="title" :data-index="String(index + 1).padStart(2, '0')">
            <strong>{{ title }}</strong><p>{{ body }}</p>
          </article>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.wizard-page { height: 100%; display: grid; grid-template-rows: 52px auto minmax(0, 1fr); overflow: hidden; color: var(--ink); background: var(--canvas); }
.wizard-header { display: flex; align-items: center; gap: var(--u3); padding: 0 var(--u4); border-bottom: var(--hair) solid var(--line-strong); background: var(--chrome-bg); color: var(--chrome-ink-dim); font-size: var(--fs-sm); }
.wizard-brand { margin-right: auto; border: 0; background: none; color: var(--chrome-ink); font-weight: 700; cursor: pointer; }
.wizard-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: var(--hair) solid var(--line-strong); background: var(--line); gap: var(--hair); }
.wizard-steps button { min-width: 0; min-height: 72px; display: grid; grid-template-columns: 34px minmax(0, 1fr); align-items: center; gap: var(--u3); padding: var(--u3) var(--u4); border: 0; text-align: left; background: var(--panel-sunken); cursor: pointer; }
.wizard-steps button[aria-current='step'] { background: var(--paper); box-shadow: inset 0 -3px 0 var(--primary); }
.wizard-steps button:disabled { cursor: default; opacity: .58; }
.wizard-step-number { color: var(--ink-4); font-family: var(--font-mono); font-size: 20px; }
.wizard-steps button[aria-current='step'] .wizard-step-number { color: var(--primary); }
.wizard-steps strong, .wizard-steps small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wizard-steps strong { font-size: var(--fs-sm); }
.wizard-steps small { margin-top: 4px; color: var(--ink-4); font-size: var(--fs-xs); font-weight: 400; }
.wizard-workspace { min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 320px; }
.wizard-main, .wizard-aside { min-height: 0; overflow: auto; }
.wizard-main { padding: 38px clamp(28px, 5vw, 72px) 48px; background: var(--paper); }
.wizard-stage { width: min(820px, 100%); margin: 0 auto; }
.wizard-stage-head { margin-bottom: 30px; }
.wizard-stage-head h1 { margin: 7px 0 8px; font-family: var(--font-prose); font-size: 30px; font-weight: 600; letter-spacing: 0; }
.wizard-stage-head p { max-width: 66ch; margin: 0; color: var(--ink-3); line-height: 1.7; }
.wizard-field { display: grid; gap: 8px; }
.wizard-field > span, .wizard-choice-group legend, .wizard-edit-head > span { color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.wizard-field textarea, .wizard-field input, .wizard-edit-section textarea, .wizard-volumes input, .wizard-volumes textarea { width: 100%; border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--paper); color: var(--ink); font: inherit; line-height: 1.75; resize: vertical; }
.wizard-field textarea { min-height: 130px; padding: var(--u4); font-family: var(--font-prose); font-size: 18px; }
.wizard-field input { height: 42px; padding: 0 var(--u3); }
.wizard-field small { justify-self: end; color: var(--ink-4); font-family: var(--font-mono); }
.wizard-field small[data-alert='true'] { color: var(--alert); }
.wizard-field-short { max-width: 440px; margin-bottom: var(--u5); }
.wizard-examples { display: grid; gap: 0; margin-top: 32px; border-top: var(--hair) solid var(--line); }
.wizard-examples > span { padding: var(--u3) 0; }
.wizard-examples button { padding: 11px 0; border: 0; border-top: var(--hair) solid var(--line); background: none; color: var(--ink-2); text-align: left; line-height: 1.65; cursor: pointer; }
.wizard-examples button:hover { color: var(--primary); }
.wizard-choice-group { margin: 0 0 30px; padding: 0; border: 0; }
.wizard-choice-group legend { margin-bottom: var(--u3); }
.wizard-audience { display: flex; align-items: center; gap: 0; }
.wizard-audience legend { margin: 0 var(--u4) 0 0; }
.wizard-audience label { position: relative; }
.wizard-audience input { position: absolute; opacity: 0; }
.wizard-audience span { min-width: 76px; height: 32px; display: grid; place-items: center; padding: 0 var(--u3); border: var(--hair) solid var(--line-strong); border-left: 0; background: var(--panel); font-size: var(--fs-sm); cursor: pointer; }
.wizard-audience label:first-of-type span { border-left: var(--hair) solid var(--line-strong); border-radius: 4px 0 0 4px; }
.wizard-audience label:last-of-type span { border-radius: 0 4px 4px 0; }
.wizard-audience input:checked + span { color: var(--paper); background: var(--primary); }
.wizard-genre-picker { padding-top: var(--u4); border-top: var(--hair) solid var(--line); }
.wizard-genre-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: var(--u4); }
.wizard-genre-tabs button { min-height: 34px; padding: 0 12px; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink-3); font: inherit; font-size: var(--fs-sm); cursor: pointer; }
.wizard-genre-tabs button:hover { border-color: var(--ink-4); color: var(--ink); }
.wizard-genre-tabs button[aria-pressed='true'] { border-color: var(--primary); background: var(--primary); color: var(--paper); font-weight: 700; }
.wizard-genre-tabs button:focus-visible, .wizard-genre-grid input:focus-visible + strong, .wizard-tag-list input:focus-visible + span { outline: 2px solid var(--primary); outline-offset: 2px; }
.wizard-subgenre-head { min-height: 28px; display: flex; align-items: start; justify-content: space-between; gap: var(--u3); margin-bottom: var(--u2); }
.wizard-subgenre-head strong { font-size: var(--fs-sm); }
.wizard-subgenre-head span { color: var(--primary); font-size: var(--fs-xs); }
.wizard-genre-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.wizard-genre-grid:empty { display: none; }
.wizard-genre-grid label { position: relative; min-height: 68px; display: grid; align-content: start; gap: 4px; padding: 11px var(--u3); border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--panel); cursor: pointer; }
.wizard-genre-grid label[data-selected='true'] { border-color: var(--primary); background: var(--primary-soft); box-shadow: inset 3px 0 0 var(--primary); }
.wizard-genre-grid input, .wizard-template-list input { position: absolute; opacity: 0; pointer-events: none; }
.wizard-genre-grid strong { font-size: var(--fs); }
.wizard-genre-grid span { color: var(--ink-3); font-size: var(--fs-sm); }
.wizard-custom-genre { max-width: 520px; display: grid; grid-template-columns: 92px minmax(0, 1fr) auto; align-items: center; gap: var(--u3); }
.wizard-custom-genre > span { color: var(--ink-3); font-size: var(--fs-sm); font-weight: 700; }
.wizard-custom-genre input { min-width: 0; height: 40px; padding: 0 var(--u3); border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--paper); color: var(--ink); font: inherit; }
.wizard-custom-genre small { color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-xs); }
.wizard-tag-picker legend { display: flex; align-items: baseline; gap: var(--u2); }
.wizard-tag-picker legend small { color: var(--ink-4); font-weight: 400; }
.wizard-tag-list { display: flex; flex-wrap: wrap; gap: 7px; }
.wizard-tag-list label { position: relative; }
.wizard-tag-list input { position: absolute; opacity: 0; }
.wizard-tag-list span { min-height: 30px; display: grid; place-items: center; padding: 0 11px; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink-3); font-size: var(--fs-sm); cursor: pointer; }
.wizard-tag-list label[data-selected='true'] span { border-color: var(--primary); background: var(--primary-soft); color: var(--primary); font-weight: 700; }
.wizard-tag-list input:disabled + span { cursor: not-allowed; opacity: .38; }
.wizard-template-list { border-top: var(--hair) solid var(--line-strong); }
.wizard-template-list label { position: relative; display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: var(--u4); align-items: center; min-height: 62px; padding: var(--u2) var(--u3); border-bottom: var(--hair) solid var(--line); cursor: pointer; }
.wizard-template-list label[data-selected='true'] { background: var(--primary-soft); box-shadow: inset 3px 0 0 var(--primary); }
.wizard-template-list span { display: flex; align-items: center; gap: var(--u2); }
.wizard-template-list small { color: var(--primary); font-size: 10px; }
.wizard-template-list p { margin: 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.55; }
.wizard-edit-section { margin-top: var(--u4); border-top: var(--hair) solid var(--line-strong); }
.wizard-plan-status { margin: -10px 0 var(--u4); padding: 10px 12px; border-left: 3px solid var(--primary); color: var(--primary); background: var(--primary-soft); font-size: var(--fs-sm); }
.wizard-plan-status[data-error] { display: flex; align-items: center; justify-content: space-between; gap: var(--u3); color: var(--alert-ink); border-left-color: var(--alert); background: var(--alert-soft); }
.wizard-plan-status button { flex: none; padding: 0; color: inherit; border: 0; background: transparent; font-weight: 700; cursor: pointer; }
.wizard-chapter-plans { margin-top: var(--u5); }
.wizard-chapter-plans .wizard-edit-head small { color: var(--ink-4); font-size: var(--fs-xs); font-weight: 400; }
.wizard-chapter-plan { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: var(--u2); padding: var(--u3) 0; border-bottom: var(--hair) solid var(--line); }
.wizard-chapter-plan > span { padding-top: 8px; color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-sm); }
.wizard-chapter-plan input, .wizard-chapter-plan textarea { grid-column: 2; width: 100%; box-sizing: border-box; border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--paper); color: var(--ink); font: inherit; }
.wizard-chapter-plan input { height: 34px; padding: 0 var(--u2); font-weight: 700; }
.wizard-chapter-plan textarea { min-height: 72px; padding: var(--u2); line-height: 1.65; resize: vertical; }
.wizard-edit-head { min-height: 42px; display: flex; align-items: center; justify-content: space-between; }
.wizard-edit-head button { border: 0; background: none; color: var(--primary); font-size: var(--fs-sm); font-weight: 700; cursor: pointer; }
.wizard-edit-section > textarea { padding: var(--u3); }
.wizard-volumes { border-top: var(--hair) solid var(--line); }
.wizard-volumes label { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: var(--u2); padding: var(--u3) 0; border-bottom: var(--hair) solid var(--line); }
.wizard-volumes label > span { padding-top: 7px; color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-sm); }
.wizard-volumes input { height: 34px; padding: 0 var(--u2); font-weight: 700; }
.wizard-volumes textarea, .wizard-volumes p { grid-column: 2; margin: 0; }
.wizard-volumes textarea { padding: var(--u2); }
.wizard-volumes p { color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.65; }
.wizard-actions { width: min(820px, 100%); display: flex; justify-content: flex-end; gap: var(--u2); margin: 34px auto 0; padding-top: var(--u4); border-top: var(--hair) solid var(--line); }
.wizard-actions .btn, .wizard-create { min-width: 112px; height: 40px; }
.wizard-actions .btn:disabled { cursor: not-allowed; opacity: .45; }
.wizard-aside { padding: 34px var(--u5); border-left: var(--hair) solid var(--line-strong); background: var(--panel-sunken); }
.wizard-aside blockquote { margin: var(--u4) 0 0; padding: 0 0 0 var(--u3); border-left: 3px solid var(--primary); font-family: var(--font-prose); font-size: 16px; line-height: 1.8; }
.wizard-aside-empty { margin: var(--u4) 0 0; color: var(--ink-4); line-height: 1.7; }
.wizard-selection-summary { display: flex; flex-wrap: wrap; gap: 5px; margin-top: var(--u4); }
.wizard-selection-summary span { padding: 3px 7px; border: var(--hair) solid var(--line-strong); color: var(--ink-2); background: var(--panel); font-size: var(--fs-xs); }
.wizard-selection-summary .is-tag { border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }
.wizard-guidance { margin-top: 32px; border-top: var(--hair) solid var(--line-strong); }
.wizard-guidance article { position: relative; padding: var(--u4) 0 var(--u4) 34px; border-bottom: var(--hair) solid var(--line); }
.wizard-guidance article::before { content: attr(data-index); position: absolute; top: var(--u4); left: 0; color: var(--ink-4); font-family: var(--font-mono); font-size: var(--fs-xs); }
.wizard-guidance strong { font-size: var(--fs-sm); }
.wizard-guidance p { margin: 6px 0 0; color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.65; }
.wizard-review dl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; border-block: var(--hair) solid var(--line-strong); }
.wizard-review dl > div { padding: var(--u3); border-left: var(--hair) solid var(--line); }
.wizard-review dl > div:first-child { border-left: 0; padding-left: 0; }
.wizard-review dt { color: var(--ink-4); font-size: var(--fs-xs); }
.wizard-review dd { margin: 5px 0 0; font-weight: 700; }
.wizard-review-tags { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; padding: var(--u3) 0; border-bottom: var(--hair) solid var(--line); }
.wizard-review-tags strong { margin-right: var(--u2); color: var(--ink-3); font-size: var(--fs-xs); }
.wizard-review-tags span { padding: 3px 8px; border: var(--hair) solid var(--primary); color: var(--primary); background: var(--primary-soft); font-size: var(--fs-xs); }
.wizard-review-copy { margin: var(--u5) 0; }
.wizard-review-copy p { color: var(--ink-2); line-height: 1.8; }
.wizard-create-error { color: var(--alert); font-size: var(--fs-sm); }

@media (max-width: 900px) {
  .wizard-workspace { grid-template-columns: minmax(0, 1fr); }
  .wizard-aside { display: none; }
  .wizard-steps button { grid-template-columns: 28px minmax(0, 1fr); padding-inline: var(--u3); }
}

@media (max-width: 620px) {
  .wizard-page { grid-template-rows: 48px auto minmax(0, 1fr); }
  .wizard-header > span { display: none; }
  .wizard-steps button { min-height: 52px; display: grid; place-items: center; padding: var(--u2); }
  .wizard-steps button > span:last-child { display: none; }
  .wizard-step-number { font-size: 16px; }
  .wizard-main { padding: 28px var(--u4) 40px; }
  .wizard-stage-head h1 { font-size: 25px; }
  .wizard-genre-tabs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .wizard-genre-tabs button { padding-inline: var(--u2); }
  .wizard-genre-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wizard-custom-genre { grid-template-columns: minmax(0, 1fr) auto; }
  .wizard-custom-genre > span { grid-column: 1 / -1; }
  .wizard-template-list label { grid-template-columns: minmax(0, 1fr); gap: 4px; padding-block: var(--u3); }
  .wizard-review dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wizard-review dl > div:nth-child(3) { border-top: var(--hair) solid var(--line); border-left: 0; padding-left: 0; }
  .wizard-review dl > div:nth-child(4) { border-top: var(--hair) solid var(--line); }
  .wizard-actions { flex-wrap: wrap; }
}
</style>
