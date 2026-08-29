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

  it('@ 搜索命中名称与别名，且排除待确认条目', async () => {
    const s = useCodexStore()
    await s.load()
    expect(s.search('沈').map((e) => e.name)).toContain('沈砚')
    expect(s.search('砚哥').map((e) => e.name)).toContain('沈砚')
    expect(s.search('师父').every((e) => e.status === 'confirmed')).toBe(true)
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
})
