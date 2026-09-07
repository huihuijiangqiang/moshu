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

  it('keeps Codex state history project-scoped and rejects stale revisions', async () => {
    const [chapter] = await mockApi.listChapters('p3')
    const entry = await mockApi.createCodexEntry('p3', {
      kind: 'character', name: '摆渡人', aliases: [], summary: '只在长夜渡舟中出现。',
      resident: true, status: 'confirmed', character: { role: '引路人' }
    })
    const created = await mockApi.createCodexStateChange('p3', entry.id, {
      chapterId: chapter!.id, stateKey: '所在地点', value: '北岸渡口'
    })

    expect(created.revision).toBe(1)
    expect(await mockApi.listCodexStateHistory('p3', entry.id)).toHaveLength(1)
    await expect(mockApi.listCodexStateHistory('p1', entry.id)).rejects.toThrow('codex_state_not_found')

    const updated = await mockApi.updateCodexStateChange('p3', entry.id, created.id, 1, {
      chapterId: chapter!.id, stateKey: '所在地点', value: '渡船舱内'
    })
    expect(updated.revision).toBe(2)
    await expect(mockApi.deleteCodexStateChange('p3', entry.id, created.id, 1))
      .rejects.toThrow('codex_state_revision_conflict')
    await mockApi.deleteCodexStateChange('p3', entry.id, created.id, 2)
    expect(await mockApi.listCodexStateHistory('p3', entry.id)).toEqual([])
  })

  it('keeps quick notes scoped to one project and validates chapter anchors', async () => {
    const [chapter] = await mockApi.listChapters('p3')
    const note = await mockApi.createProjectNote('p3', '让摆渡人在这里认出旧灯。', chapter!.id)

    expect(await mockApi.listProjectNotes('p3')).toEqual([note])
    expect((await mockApi.listProjectNotes('p1')).some((item) => item.id === note.id)).toBe(false)
    await expect(mockApi.createProjectNote('p3', '越界章节', 'ch87')).rejects.toThrow('invalid_project_note')
    await mockApi.deleteProjectNote('p3', note.id)
    expect(await mockApi.listProjectNotes('p3')).toEqual([])
  })
})
