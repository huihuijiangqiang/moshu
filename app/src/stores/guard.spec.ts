import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { contentApi } from '@/api/content'
import type { GuardOverview } from '@/types'
import { useGuardStore } from './guard'

function overview(status: GuardOverview['status'], running: number): GuardOverview {
  return {
    status,
    queued: 0,
    running,
    completed: status === 'completed' ? 1 : 0,
    failed: 0,
    outboxPending: 0,
    outboxDeadLetter: 0,
    runs: []
  }
}

describe('guard store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('resumes polling when an existing project scan is active', async () => {
    vi.useFakeTimers()
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview')
      .mockResolvedValueOnce(overview('running', 1))
      .mockResolvedValueOnce(overview('completed', 0))

    const store = useGuardStore()
    await store.load('p1')

    expect(store.scanning).toBe(true)
    expect(store.overview.running).toBe(1)

    await vi.advanceTimersByTimeAsync(250)

    expect(store.overview.status).toBe('completed')
    expect(store.scanning).toBe(false)
    expect(contentApi.getGuardOverview).toHaveBeenCalledTimes(2)
  })

  it('shares one poller between repeated loads', async () => {
    vi.useFakeTimers()
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview')
      .mockResolvedValueOnce(overview('running', 1))
      .mockResolvedValueOnce(overview('completed', 0))

    const store = useGuardStore()
    await store.load('p1')
    await store.load('p1')
    await vi.advanceTimersByTimeAsync(250)

    expect(contentApi.getGuardOverview).toHaveBeenCalledTimes(2)
  })

  it('allows an explicit rescan while stale work is being polled', async () => {
    vi.useFakeTimers()
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview').mockResolvedValue(overview('running', 1))
    const scan = vi.spyOn(contentApi, 'scanProject').mockResolvedValue({ queued: 1, run_ids: [7] })

    const store = useGuardStore()
    await store.load('p1')
    expect(store.scanning).toBe(true)

    await store.rescan()

    expect(scan).toHaveBeenCalledWith('p1')
    expect(store.scanRequestPending).toBe(false)
    expect(store.scanning).toBe(true)
  })

  it('replaces an old project poller when the active project changes', async () => {
    vi.useFakeTimers()
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview')
      .mockResolvedValueOnce(overview('running', 1))
      .mockResolvedValueOnce(overview('running', 1))
      .mockResolvedValueOnce(overview('completed', 0))

    const store = useGuardStore()
    await store.load('p1')
    await store.load('p2', true)
    await vi.advanceTimersByTimeAsync(250)

    expect(store.loadedProjectId).toBe('p2')
    expect(store.overview.status).toBe('completed')
    expect(store.scanning).toBe(false)
    expect(contentApi.getGuardOverview).toHaveBeenCalledTimes(3)
  })
})
