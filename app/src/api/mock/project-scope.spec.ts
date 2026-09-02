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
})
