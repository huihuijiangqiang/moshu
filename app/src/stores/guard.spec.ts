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
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(contentApi, 'listTemporalReviews').mockResolvedValue([])
  })

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

  it('reflows the project timeline and exposes author-facing diagnostics', async () => {
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview').mockResolvedValue(overview('completed', 0))
    const reflow = vi.spyOn(contentApi, 'reflowProjectTimeline').mockResolvedValue({
      claimsExamined: 20,
      claimsChanged: 3,
      affectedChapterIds: ['ch1', 'ch2'],
      resolved: 2,
      unresolved: 1,
      ambiguous: 0,
      cyclic: 0,
      cycles: [],
      rescansQueued: 2,
      rescanRunIds: [3, 4]
    })
    const store = useGuardStore()
    await store.load('p1')

    await store.reflowTimeline()

    expect(reflow).toHaveBeenCalledWith('p1')
    expect(store.timelineReflowResult?.claimsChanged).toBe(3)
    expect(store.timelineReflowError).toBeNull()
    expect(store.timelineReflowPending).toBe(false)
  })

  it('keeps a clear error state when timeline reflow fails', async () => {
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview').mockResolvedValue(overview('completed', 0))
    vi.spyOn(contentApi, 'reflowProjectTimeline').mockRejectedValue(new Error('offline'))
    const store = useGuardStore()
    await store.load('p1')

    await store.reflowTimeline()

    expect(store.timelineReflowError).toBe('时间线重算失败，请稍后重试。')
    expect(store.timelineReflowPending).toBe(false)
  })

  it('clears timeline diagnostics when switching projects', async () => {
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview').mockResolvedValue(overview('completed', 0))
    vi.spyOn(contentApi, 'reflowProjectTimeline').mockResolvedValue({
      claimsExamined: 2,
      claimsChanged: 1,
      affectedChapterIds: ['ch1'],
      resolved: 1,
      unresolved: 0,
      ambiguous: 0,
      cyclic: 0,
      cycles: [],
      rescansQueued: 0,
      rescanRunIds: []
    })
    const store = useGuardStore()
    await store.load('p1')
    await store.reflowTimeline()

    await store.load('p2')

    expect(store.loadedProjectId).toBe('p2')
    expect(store.timelineReflowResult).toBeNull()
    expect(store.timelineReflowError).toBeNull()
  })

  it('ignores a previous project reflow that finishes after navigation', async () => {
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview').mockResolvedValue(overview('completed', 0))
    let finishReflow!: (value: Awaited<ReturnType<typeof contentApi.reflowProjectTimeline>>) => void
    vi.spyOn(contentApi, 'reflowProjectTimeline').mockImplementation(
      () => new Promise((resolve) => { finishReflow = resolve })
    )
    const store = useGuardStore()
    await store.load('p1')
    const oldRequest = store.reflowTimeline()
    expect(store.timelineReflowPending).toBe(true)

    await store.load('p2')
    finishReflow({
      claimsExamined: 2,
      claimsChanged: 1,
      affectedChapterIds: ['old-chapter'],
      resolved: 1,
      unresolved: 0,
      ambiguous: 0,
      cyclic: 0,
      cycles: [],
      rescansQueued: 0,
      rescanRunIds: []
    })
    await oldRequest

    expect(store.loadedProjectId).toBe('p2')
    expect(store.timelineReflowPending).toBe(false)
    expect(store.timelineReflowResult).toBeNull()
  })

  it('confirms a fuzzy time with its optimistic version and refreshes it', async () => {
    const review = {
      claimId: 9,
      original: '过几日后',
      normalized: '2-7日后',
      offsetMinSeconds: 2 * 86400,
      offsetMaxSeconds: 7 * 86400,
      dependencyStatus: 'unresolved',
      overrideVersion: 0
    }
    vi.mocked(contentApi.listTemporalReviews)
      .mockResolvedValueOnce([review])
      .mockResolvedValueOnce([{ ...review, overrideSeconds: 4 * 86400, overrideVersion: 1 }])
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview').mockResolvedValue(overview('completed', 0))
    const decide = vi.spyOn(contentApi, 'decideTemporalReview').mockResolvedValue({
      item: { ...review, overrideSeconds: 4 * 86400, overrideVersion: 1 },
      reflow: {
        claimsExamined: 2, claimsChanged: 1, affectedChapterIds: ['ch1'],
        resolved: 1, unresolved: 0, ambiguous: 0, cyclic: 0, cycles: [],
        rescansQueued: 1, rescanRunIds: [3]
      }
    })
    const store = useGuardStore()
    await store.load('p1')

    const saved = await store.decideTemporalReview(review, 'confirm', 4 * 86400)

    expect(saved).toBe(true)
    expect(decide).toHaveBeenCalledWith('p1', 9, 'confirm', 0, 4 * 86400)
    expect(store.temporalReviews[0]?.overrideVersion).toBe(1)
    expect(store.timelineReflowResult?.rescansQueued).toBe(1)
  })

  it('ignores a temporal decision that finishes after switching projects', async () => {
    const review = {
      claimId: 9,
      original: '过几日后',
      normalized: '2-7日后',
      offsetMinSeconds: 2 * 86400,
      offsetMaxSeconds: 7 * 86400,
      dependencyStatus: 'unresolved',
      overrideVersion: 0
    }
    vi.mocked(contentApi.listTemporalReviews).mockImplementation(async (projectId) =>
      projectId === 'p1' ? [review] : []
    )
    vi.spyOn(contentApi, 'listGuardIssues').mockResolvedValue([])
    vi.spyOn(contentApi, 'getGuardOverview').mockResolvedValue(overview('completed', 0))
    let finishDecision!: (value: Awaited<ReturnType<typeof contentApi.decideTemporalReview>>) => void
    vi.spyOn(contentApi, 'decideTemporalReview').mockImplementation(
      () => new Promise((resolve) => { finishDecision = resolve })
    )
    const store = useGuardStore()
    await store.load('p1')
    const oldRequest = store.decideTemporalReview(review, 'confirm', 4 * 86400)

    await store.load('p2')
    finishDecision({
      item: { ...review, overrideSeconds: 4 * 86400, overrideVersion: 1 },
      reflow: {
        claimsExamined: 2, claimsChanged: 1, affectedChapterIds: ['old-chapter'],
        resolved: 1, unresolved: 0, ambiguous: 0, cyclic: 0, cycles: [],
        rescansQueued: 0, rescanRunIds: []
      }
    })
    await oldRequest

    expect(store.loadedProjectId).toBe('p2')
    expect(store.temporalReviews).toEqual([])
    expect(store.timelineReflowResult).toBeNull()
    expect(store.temporalDecisionPendingId).toBeNull()
  })
})
