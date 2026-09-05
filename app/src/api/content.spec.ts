import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import { ApiError } from './http'
import { bodyConflictFromError, codexDraftAttrs, codexFromDto, guardIssueFromDto, guardOverviewFromDto, htmlToDocument } from './content'

describe('htmlToDocument', () => {
  it('uses the backend paragraph pid contract', () => {
    const document = htmlToDocument(
      '<p data-paragraph-id="known-pid">第一段</p><p>第二段</p>'
    ) as { content: Array<{ attrs: { pid: string } }> }

    expect(document.content.map((node) => node.attrs.pid)).toEqual(['known-pid', 'p-1'])
  })

  it('preserves server-verifiable AI provenance attributes', () => {
    const document = htmlToDocument(
      '<p data-ai-run-id="run-1" data-ai-source-hash="00abc123">AI 草稿</p>'
    ) as { content: Array<{ attrs: Record<string, string> }> }

    expect(document.content[0]?.attrs).toMatchObject({
      aiRunId: 'run-1',
      aiSourceHash: '00abc123'
    })
  })
})

describe('bodyConflictFromError', () => {
  it('extracts both versions from a FastAPI 409 response', () => {
    const conflict = bodyConflictFromError('ch-1', new ApiError(409, JSON.stringify({
      detail: {
        conflict: true,
        server_content_html: '<p>server</p>',
        server_content_json: {},
        server_rev: 7,
        client_content_html: '<p>client</p>',
        client_content_json: {}
      }
    })))

    expect(conflict).toEqual({
      chapterId: 'ch-1',
      serverContentHtml: '<p>server</p>',
      serverRev: 7,
      clientContentHtml: '<p>client</p>'
    })
  })

  it('does not misclassify an idempotency-key 409 as a body conflict', () => {
    expect(bodyConflictFromError('ch-1', new ApiError(409, '{"detail":"Idempotency key conflict"}'))).toBeNull()
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
        arc: { past: '只求自保', current: '开始护住家人', next: '主动争取田契' },
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
    expect(entry.character?.arc?.next).toBe('主动争取田契')
    expect(entry.relations?.[0].targetId).toBe('p1-cx-char-02')
    expect(entry.rawAttrs?.relations).toBeTruthy()
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

  it('maps the complete foreshadow lifecycle', () => {
    const entry = codexFromDto({
      id: 'p1-cx-foreshadow-01', project_id: 'p1', kind: 'event', name: '旧井铜钥匙',
      description: '开启粮仓暗门', aliases: [], attrs: {}, resident: false,
      status: 'confirmed', ref_chapters: ['p1-ch01', 'p1-ch08'], conflicts: [],
      planted_at: 'p1-ch01', expected_by: 'p1-ch06', foreshadow_resolved: true,
      resolved_at: 'p1-ch08'
    })

    expect(entry.kind).toBe('foreshadow')
    expect(entry.plantedAt).toBe(1)
    expect(entry.expectedBy).toBe('第 6 章')
    expect(entry.plantedChapterId).toBe('p1-ch01')
    expect(entry.expectedChapterId).toBe('p1-ch06')
    expect(entry.foreshadowResolved).toBe(true)
    expect(entry.resolvedChapterId).toBe('p1-ch08')
  })

  it('preserves opaque relation attrs while replacing editable character fields', () => {
    const attrs = codexDraftAttrs({
      kind: 'character',
      name: '许知微',
      aliases: [],
      summary: '',
      resident: true,
      status: 'confirmed',
      character: { desire: '安稳立足', personality: ['谨慎', '务实'], arc: { next: '买下荒地' } }
    }, {
      id: 'cx_1', kind: 'character', name: '许知微', aliases: [], summary: '', resident: true,
      status: 'confirmed', refChapters: [], conflicts: 0,
      rawAttrs: {
        desire: '旧目标', relation_version: 3, relations: [{ name: '周何氏' }],
        character: { desire: '嵌套旧目标', custom_voice_id: 'voice-7' }
      }
    })

    expect(attrs.desire).toBe('安稳立足')
    expect(attrs.personality).toEqual(['谨慎', '务实'])
    expect(attrs.arc).toEqual({ next: '买下荒地' })
    expect(attrs.relation_version).toBe(3)
    expect(attrs.relations).toEqual([{ name: '周何氏' }])
    expect(attrs.character).toEqual({ custom_voice_id: 'voice-7' })
  })

  it('removes stale character fields when an entry changes to a generic kind', () => {
    const attrs = codexDraftAttrs({
      kind: 'place', name: '青河村', aliases: [], summary: '', resident: false,
      status: 'confirmed', facts: [{ label: '方位', value: '县城以东' }]
    }, {
      id: 'cx_1', kind: 'character', name: '旧人物', aliases: [], summary: '', resident: false,
      status: 'confirmed', refChapters: [], conflicts: 0,
      rawAttrs: { role: '配角', character: { fear: '失去亲人' }, relations: [] }
    })

    expect(attrs.role).toBeUndefined()
    expect(attrs.character).toBeUndefined()
    expect(attrs.facts).toEqual([{ label: '方位', value: '县城以东' }])
    expect(attrs.relations).toEqual([])
  })

  it('accepts the reactive entry object provided by Pinia', () => {
    const existing = reactive({
      id: 'cx_1', kind: 'place' as const, name: '白鹭洲', aliases: [], summary: '', resident: false,
      status: 'confirmed' as const, refChapters: [], conflicts: 0,
      rawAttrs: { relations: [{ name: '青河' }], facts: [{ label: '旧值', value: '待改' }] }
    })

    expect(() => codexDraftAttrs({
      kind: 'place', name: '白鹭洲', aliases: [], summary: '', resident: false,
      status: 'confirmed', facts: [{ label: '通行', value: '枯水期可达' }]
    }, existing)).not.toThrow()
  })
})

describe('Guard DTO mapping', () => {
  it('keeps issue revision, evidence and resolution actions for real handling', () => {
    const issue = guardIssueFromDto({
      id: 'g1', chapter_id: 'ch1', issue_type: 'alive_conflict', severity: 'high',
      description: '沈砚已经死亡，却在后文亲自开门。', status: 'open', resolved: false,
      issue_rev: 3, confidence: 0.94, chapter_index: 12, chapter_title: '雪夜归人',
      evidence: [{ label: '本次正文', text: '沈砚推门进来。', accent: true }],
      actions: ['accept_old_fact', 'accept_new_fact'], arbitration_status: 'unsupported',
      arbitration_confidence: 0.81, arbitration_rationale: '上下文暗示这是梦境。',
      updated_at: '2026-09-02T10:00:00Z'
    })

    expect(issue.category).toBe('生死状态冲突')
    expect(issue.chapterRef).toBe('第 12 章 · 雪夜归人')
    expect(issue.issueRev).toBe(3)
    expect(issue.actions).toEqual(['保留原设定', '采用新事实'])
    expect(issue.actionCodes).toEqual(['accept_old_fact', 'accept_new_fact'])
    expect(issue.evidence[0]?.accent).toBe(true)
    expect(issue.arbitrationStatus).toBe('unsupported')
    expect(issue.arbitrationConfidence).toBe(0.81)
    expect(issue.arbitrationRationale).toBe('上下文暗示这是梦境。')
  })

  it.each([
    ['timeline_conflict', '时间线冲突'],
    ['ability_boundary', '能力边界冲突'],
    ['location_conflict', '地点冲突'],
    ['foreshadow_overdue', '伏笔逾期']
  ])('maps %s to a specific guard category', (issueType, label) => {
    const issue = guardIssueFromDto({
      id: `g-${issueType}`, chapter_id: 'ch1', issue_type: issueType, severity: 'medium',
      description: '测试问题', status: 'open', resolved: false, issue_rev: 1,
      confidence: 0.9, chapter_index: 1, chapter_title: '第一章', evidence: [],
      actions: ['false_positive'], arbitration_status: 'not_requested',
      arbitration_confidence: null, arbitration_rationale: null,
      updated_at: '2026-09-03T10:00:00Z'
    })

    expect(issue.category).toBe(label)
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
