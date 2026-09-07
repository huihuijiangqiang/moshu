import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usageApi, type UsageSummary } from '@/api/usage'
import { useUsageStore } from './usage'

vi.mock('@/api/usage', () => ({ usageApi: { summary: vi.fn() } }))

const sample: UsageSummary = {
  plan: 'author',
  plan_label: '作者版',
  remaining: 987,
  quota: 1000,
  purchased_remaining: 0,
  available: 987,
  spent: 13,
  period_start: '2026-09-01T00:00:00+00:00',
  resets_at: '2026-10-01T00:00:00+00:00',
  rates: { basic_input: 1, basic_output: 2, advanced_input: 4, advanced_output: 8, cached_percent: 20 },
  items: [],
  daily: [],
  recent: []
}

describe('usage store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(usageApi.summary).mockReset()
  })

  it('loads real quota values and caches them', async () => {
    vi.mocked(usageApi.summary).mockResolvedValue(sample)
    const store = useUsageStore()

    await store.load()
    await store.load()

    expect(store.remaining).toBe(987)
    expect(store.quota).toBe(1000)
    expect(usageApi.summary).toHaveBeenCalledTimes(1)
  })

  it('refreshes after a completed generation', async () => {
    vi.mocked(usageApi.summary)
      .mockResolvedValueOnce(sample)
      .mockResolvedValueOnce({ ...sample, remaining: 980, spent: 20 })
    const store = useUsageStore()

    await store.load()
    await store.load(true)

    expect(store.remaining).toBe(980)
    expect(store.summary?.spent).toBe(20)
  })
})
