import { beforeEach, describe, expect, it, vi } from 'vitest'
import { orgApi } from './orgs'

describe('studio production api', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: vi.fn(),
      removeItem: vi.fn()
    })
  })

  it('uses project-scoped assignment endpoints and sends status transitions', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 7 }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 7, status: 'claimed' }), { status: 200 }))

    await orgApi.assignChapter('org-1', 'project-1', 'chapter-1', 'writer-1', '完成开篇')
    await orgApi.updateAssignment('org-1', 'project-1', 7, 'claimed')

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/orgs/org-1/projects/project-1/assignments')
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: 'POST',
      body: JSON.stringify({ chapter_id: 'chapter-1', assigned_to: 'writer-1', notes: '完成开篇' })
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/orgs/org-1/projects/project-1/assignments/7')
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'PATCH', body: JSON.stringify({ status: 'claimed' }) })
  })
})
