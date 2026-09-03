import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useCodexStore } from './codex'

describe('codex store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('加载后按类型分组计数', async () => {
    const s = useCodexStore()
    await s.load()
    expect(s.entries.length).toBeGreaterThan(0)
    expect(s.counts.character).toBeGreaterThan(0)
  })

  it('@ 搜索命中名称、别名与人物约束，且排除待确认条目', async () => {
    const s = useCodexStore()
    await s.load()
    expect(s.search('沈').map((e) => e.name)).toContain('沈砚')
    expect(s.search('砚哥').map((e) => e.name)).toContain('沈砚')
    expect(s.search('境界不可越级').map((e) => e.name)).toContain('沈砚')
    expect(s.search('不肯托人情').map((e) => e.name)).toContain('沈砚')
    expect(s.search('师父').every((e) => e.status === 'confirmed')).toBe(true)
  })

  it('人物条目包含可直接用于写作的人物档案', async () => {
    const s = useCodexStore()
    await s.load()
    const shenYan = s.byId.get('c-shenyan')

    expect(shenYan?.character?.desire).toContain('师父')
    expect(shenYan?.character?.limitation).toContain('不可越级')
    expect(shenYan?.character?.speech).toContain('短句')
    expect(shenYan?.character?.arc?.next).toContain('北上')
    expect(shenYan?.relations?.some((relation) => relation.targetId === 'c-zhoutou')).toBe(true)
  })

  it('确认待定条目后离开 pending 列表', async () => {
    const s = useCodexStore()
    await s.load()
    const target = s.pending[0]
    expect(target).toBeTruthy()
    await s.confirm(target.id)
    expect(s.pending.find((e) => e.id === target.id)).toBeUndefined()
  })

  it('常驻条目参与第 1 层上下文预算', async () => {
    const s = useCodexStore()
    await s.load()
    expect(s.resident.length).toBeGreaterThan(0)
    expect(s.resident.every((e) => e.resident)).toBe(true)
  })

  it('新建、编辑和删除会更新同一份设定状态', async () => {
    const s = useCodexStore()
    await s.load('p1')
    const created = await s.create('p1', {
      kind: 'place', name: '白鹭洲', aliases: ['鹭洲'], summary: '青河下游的沙洲。',
      resident: false, status: 'confirmed', facts: [{ label: '通行', value: '枯水期可步行抵达' }]
    })

    expect(s.byId.get(created.id)?.name).toBe('白鹭洲')
    const updated = await s.update(created.id, {
      kind: 'place', name: '白鹭洲渡口', aliases: ['鹭洲渡'], summary: '青河下游渡口。',
      resident: true, status: 'confirmed', facts: [{ label: '通行', value: '仅白日摆渡' }]
    })
    expect(updated.name).toBe('白鹭洲渡口')
    expect(s.byId.get(created.id)?.resident).toBe(true)

    await s.drop(created.id)
    expect(s.byId.has(created.id)).toBe(false)
  })
})
