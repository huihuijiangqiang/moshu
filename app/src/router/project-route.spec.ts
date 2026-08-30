import { describe, expect, it } from 'vitest'
import { projectPath } from './project-route'

describe('project routes', () => {
  it('keeps every project tool inside its project context', () => {
    expect(projectPath('p3', 'write')).toBe('/projects/p3/write')
    expect(projectPath('p3', 'guard')).toBe('/projects/p3/guard')
    expect(projectPath('p3', 'export')).toBe('/projects/p3/export')
  })

  it('encodes project identifiers used in URLs', () => {
    expect(projectPath('draft 01', 'outline')).toBe('/projects/draft%2001/outline')
  })
})
