import { describe, expect, it } from 'vitest'
import { codexFromDto, guardIssueFromDto, guardOverviewFromDto, htmlToDocument } from './content'

describe('htmlToDocument', () => {
  it('uses the backend paragraph pid contract', () => {
    const document = htmlToDocument(
      '<p data-paragraph-id="known-pid">第一段</p><p>第二段</p>'
    ) as { content: Array<{ attrs: { pid: string } }> }

    expect(document.content.map((node) => node.attrs.pid)).toEqual(['known-pid', 'p-1'])
  })
})

describe('codexFromDto', () => {
  it('maps backend kinds, character details and chapter evidence', () => {
    const entry = codexFromDto({
      id: 'p1-cx-char-01',
      project_id: 'p1',
      kind: 'character',
      name: '许知微',
      description: '女主。谨慎务实。',
      aliases: ['知微'],
      attrs: {
        role: '女主',
        age: '二十四岁',
        personality: ['谨慎务实'],
        ability: '记账、议价',
        limitation: '不熟悉律例',
        relations: [{ target_id: 'p1-cx-char-02', name: '周何氏', relation: '人物关系', note: '共同持家' }]
      },
      resident: true,
      status: 'confirmed',
      ref_chapters: ['p1-ch01', 'p1-ch05'],
      conflicts: ['gi_1'],
      planted_at: null,
      expected_by: null
    })

    expect(entry.kind).toBe('character')
    expect(entry.refChapters).toEqual([1, 5])
    expect(entry.conflicts).toBe(1)
    expect(entry.character?.ability).toBe('记账、议价')
    expect(entry.relations?.[0].targetId).toBe('p1-cx-char-02')
  })

  it('maps backend rules to system facts', () => {
    const entry = codexFromDto({
      id: 'p1-cx-rule-01',
      project_id: 'p1',
      kind: 'rule',
      name: '经营与账目规则',
      description: '钱粮逐章继承',
      aliases: [],
      attrs: { facts: [{ label: '货币', value: '一贯等于一千文' }] },
      resident: false,
      status: 'confirmed',
      ref_chapters: [],
      conflicts: [],
      planted_at: null,
      expected_by: null
    })

    expect(entry.kind).toBe('system')
    expect(entry.facts).toEqual([{ label: '货币', value: '一贯等于一千文' }])
  })
})

describe('Guard DTO mapping', () => {
  it('keeps issue revision, evidence and resolution actions for real handling', () => {
    const issue = guardIssueFromDto({
      id: 'g1', chapter_id: 'ch1', issue_type: 'alive_conflict', severity: 'high',
      description: '沈砚已经死亡，却在后文亲自开门。', status: 'open', resolved: false,
      issue_rev: 3, confidence: 0.94, chapter_index: 12, chapter_title: '雪夜归人',
      evidence: [{ label: '本次正文', text: '沈砚推门进来。', accent: true }],
      actions: ['accept_old_fact', 'accept_new_fact'], updated_at: '2026-09-02T10:00:00Z'
    })

    expect(issue.category).toBe('生死状态冲突')
    expect(issue.chapterRef).toBe('第 12 章 · 雪夜归人')
    expect(issue.issueRev).toBe(3)
    expect(issue.actions).toEqual(['保留原设定', '采用新事实'])
    expect(issue.actionCodes).toEqual(['accept_old_fact', 'accept_new_fact'])
    expect(issue.evidence[0]?.accent).toBe(true)
  })

  it('maps outbox and phase state without inventing a completed scan', () => {
    const overview = guardOverviewFromDto({
      status: 'running', queued: 0, running: 1, completed: 2, failed: 0,
      outbox_pending: 1, outbox_dead_letter: 0, latest_activity_at: '2026-09-02T10:00:00Z',
      runs: [{
        chapter_id: 'ch1', chapter_index: 12, chapter_title: '雪夜归人', body_rev: 4,
        status: 'scanning', phases: { extract: 'succeeded', summary: 'running', scan: 'running' },
        error_code: null, error_detail: null, updated_at: '2026-09-02T10:00:00Z'
      }]
    })

    expect(overview.status).toBe('running')
    expect(overview.outboxPending).toBe(1)
    expect(overview.runs[0]?.phases.summary).toBe('running')
  })
})
