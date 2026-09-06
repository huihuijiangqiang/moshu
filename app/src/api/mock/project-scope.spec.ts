import { describe, expect, it } from 'vitest'
import { mockApi } from './index'

describe('mock project scoping', () => {
  it('loads metadata and chapters for the selected project', async () => {
    const [project, chapters] = await Promise.all([
      mockApi.getProject('p3'),
      mockApi.listChapters('p3')
    ])

    expect(project.title).toBe('长夜渡舟')
    expect(chapters).toHaveLength(16)
    expect(chapters[0]?.id).toMatch(/^p3-/)
    expect(chapters.at(-1)?.title).toBe('纸船灯影')
    expect(chapters.reduce((sum, chapter) => sum + chapter.words, 0)).toBe(126000)
  })

  it('does not leak Sword of Mountains codex and guard data into another project', async () => {
    const [foreignCodex, foreignIssues, primaryCodex] = await Promise.all([
      mockApi.listCodex('p3'),
      mockApi.listGuardIssues('p3'),
      mockApi.listCodex('p1')
    ])

    expect(foreignCodex).toEqual([])
    expect(foreignIssues).toHaveLength(1)
    expect(foreignIssues[0]?.title).toContain('长夜渡舟')
    expect(foreignIssues[0]?.title).not.toContain('剑起山河')
    expect(primaryCodex.length).toBeGreaterThan(0)
  })

  it('rejects assigning a character from another project as chapter POV', async () => {
    const [chapter] = await mockApi.listChapters('p3')
    await expect(mockApi.updateChapterPov(chapter!.id, 'c-shenyan', chapter!.povRevision ?? 0))
      .rejects.toThrow('invalid_pov_character')
  })

  it('keeps author timeline entries versioned and scoped to their project', async () => {
    const created = await mockApi.createTimelineEntry('p3', {
      title: '渡口相逢', timelineId: '主线', timeText: '子夜', storyOrder: 12
    })
    expect(created.rev).toBe(1)

    const p3Board = await mockApi.getTimelineBoard('p3')
    const p1Board = await mockApi.getTimelineBoard('p1')
    expect(p3Board.lanes[0]?.events[0]).toMatchObject({ entryId: created.id, source: 'planned' })
    expect(p1Board.lanes.flatMap((lane) => lane.events).some((event) => event.entryId === created.id)).toBe(false)

    const updated = await mockApi.updateTimelineEntry('p3', created.id, 1, {
      title: '渡口重逢', timelineId: '往事线', storyOrder: 8
    })
    expect(updated.rev).toBe(2)
    await expect(mockApi.archiveTimelineEntry('p3', created.id, 1)).rejects.toThrow('timeline_entry_conflict')

    const archived = await mockApi.archiveTimelineEntry('p3', created.id, 2)
    expect(archived.status).toBe('archived')
    expect((await mockApi.getTimelineBoard('p3')).eventCount).toBe(0)
  })
})
