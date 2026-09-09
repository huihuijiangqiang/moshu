import { describe, expect, it } from 'vitest'
import { buildMockPlan } from './wizard'

describe('buildMockPlan', () => {
  it('keeps the writer choices in the generated mock plan', () => {
    const plan = buildMockPlan({
      inspiration: '她穿越到荒年农家，靠一亩薄田带全家翻身',
      audience: '女频',
      genre: '穿越种田',
      tags: ['经营', '感情线'],
      template: '事业感情双线'
    })

    expect(plan.title).toContain('穿越种田')
    expect(plan.protagonist).toContain('女频')
    expect(plan.synopsis).toContain('她穿越到荒年农家')
    expect(plan.synopsis).toContain('经营、感情线')
    expect(plan.coreHook).toContain('事业感情双线')
    expect(plan.volumes).toHaveLength(4)
    expect(plan.chapters).toHaveLength(3)
    expect(plan.chapters.map((chapter) => chapter.volumeIndex)).toEqual([0, 0, 1])
    expect(plan.chapters[0]?.outline.join(' ')).toContain('荒年农家')
  })

  it('uses safe neutral fallbacks when optional fields are empty', () => {
    const plan = buildMockPlan({ inspiration: '', audience: '', genre: '', tags: [], template: '' })

    expect(plan.title).toContain('未定题材')
    expect(plan.protagonist).toContain('通用')
    expect(plan.synopsis).toContain('无额外标签')
  })
})
