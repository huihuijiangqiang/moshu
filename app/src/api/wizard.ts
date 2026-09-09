import { request, USE_MOCK } from './http'

export interface WizardPlan {
  title: string
  protagonist: string
  coreHook: string
  synopsis: string
  volumes: Array<{ title: string; summary: string }>
  chapters: Array<{ title: string; outline: string[]; volumeIndex?: number }>
}

export interface WizardPlanInput {
  inspiration: string
  audience: string
  genre: string
  tags: string[]
  template: string
}

/**
 * Mock 规划也必须遵守真实规划的输入契约。
 * 这样开发/演示模式不会把作者选的题材悄悄替换成另一部固定样书。
 */
export function buildMockPlan(input: WizardPlanInput): WizardPlan {
  const inspiration = input.inspiration.trim() || '一个人决定重新开始自己的生活'
  const audience = input.audience.trim() || '通用'
  const genre = input.genre.trim() || '未定题材'
  const template = input.template.trim() || '自由推进'
  const tags = input.tags.filter(Boolean).join('、') || '无额外标签'
  const seed = inspiration.replace(/[。！？.!?]+$/u, '').slice(0, 18)
  const leadName = audience.includes('女') ? '林知微' : audience.includes('男') ? '顾行舟' : '沈知行'

  return {
    title: `${genre}·${template}：${seed}`,
    protagonist: `${audience}主角 ${leadName} · 灵感起点「${seed}」\n她/他必须在「${genre}」的规则中完成选择，初始目标来自：${inspiration}`,
    coreHook: `${template}的核心钩子\n围绕「${inspiration}」展开，重点兑现${tags}；每次推进都要付出会改变人物关系的代价。`,
    synopsis: `这是一个${audience}向的${genre}故事，采用“${template}”推进。灵感起点：${inspiration}。内容标签：${tags}。主角先处理眼前的具体困境，再逐层发现个人选择与更大秩序之间的联系。`,
    volumes: [
      { title: '第一卷 · 起因', summary: `从「${seed}」切入，建立${audience}读者期待和${genre}规则，抛出第一道必须回应的难题。` },
      { title: '第二卷 · 试错', summary: `沿${template}推进，兑现${tags}带来的第一个阶段回报，同时让主角承担选择的直接后果。` },
      { title: '第三卷 · 变局', summary: `把灵感中的未知数扩大到关系与世界层面，${genre}的核心矛盾迫使主角改变原有目标。` },
      { title: '第四卷 · 回响', summary: `回收「${seed}」埋下的主线问题，完成${template}阶段闭环，并留下可继续发展的新承诺。` }
    ],
    chapters: [
      { title: '第 1 章 · 灵感落地', outline: [`呈现${audience}主角面对「${seed}」的具体处境`, `用${genre}规则制造第一处反常`, `主角作出不可撤回的第一次选择`], volumeIndex: 0 },
      { title: '第 2 章 · 代价出现', outline: [`让${template}开始推动行动`, `把${tags}中的一个标签转化为可见冲突`, '主角发现原先的解决方案并不完整'], volumeIndex: 0 },
      { title: '第 3 章 · 新的承诺', outline: [`补足「${inspiration}」背后的关键线索`, `让主要关系因选择发生变化`, `以一个指向下一章的未解问题收束`], volumeIndex: 1 }
    ]
  }
}

export async function planWizard(input: WizardPlanInput): Promise<WizardPlan> {
  if (USE_MOCK) return buildMockPlan(input)
  return request<WizardPlan>('/projects/wizard/plan', {
    method: 'POST',
    body: JSON.stringify(input)
  })
}
