import { request, USE_MOCK } from './http'

export interface WizardPlan {
  title: string
  protagonist: string
  coreHook: string
  synopsis: string
  volumes: Array<{ title: string; summary: string }>
  chapters: Array<{ title: string; outline: string[] }>
}

export interface WizardPlanInput {
  inspiration: string
  audience: string
  genre: string
  tags: string[]
  template: string
}

const mockPlan: WizardPlan = {
  title: '残锋照雪',
  protagonist: '沈砚 · 三十二岁 · 北境守将\n寡言，认死理，不肯托人情。',
  coreHook: '淬铁 · 以器听人\n能听见兵器残留的持有者情绪，但每次倾听都会承受同样的伤痛。',
  synopsis: '一个守关将军发现自己的佩剑一直在替师父撒谎。故事采用“谜团追索”推进：主角先因一次无法回避的选择被卷入冲突，再发现个人困境与更大的秩序有关。',
  volumes: [
    { title: '第一卷 · 入局', summary: '用一次具体失败立住人物缺陷，抛出核心谜面。' },
    { title: '第二卷 · 试锋', summary: '外部对手开始主动施压，能力代价第一次造成后果。' },
    { title: '第三卷 · 旧盟', summary: '阶段真相揭开，主角被迫作出违背旧原则的选择。' },
    { title: '第四卷 · 同断', summary: '人物缺陷与核心冲突正面碰撞，完成谜团回收。' }
  ],
  chapters: [
    { title: '第 1 章 · 入局', outline: ['确认主角处境', '日常被事件打破', '主角做出第一次选择'] },
    { title: '第 2 章 · 试探', outline: ['追查事件线索', '遭遇具体阻力', '留下新的疑问'] },
    { title: '第 3 章 · 旧痕', outline: ['发现关键证据', '关系发生变化', '以未解钩子收束'] }
  ]
}

export async function planWizard(input: WizardPlanInput): Promise<WizardPlan> {
  if (USE_MOCK) return JSON.parse(JSON.stringify(mockPlan)) as WizardPlan
  return request<WizardPlan>('/projects/wizard/plan', {
    method: 'POST',
    body: JSON.stringify(input)
  })
}
