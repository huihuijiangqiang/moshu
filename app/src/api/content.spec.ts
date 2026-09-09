import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import { ApiError } from './http'
import { bodyConflictFromError, characterStatisticsFromDto, codexDraftAttrs, codexFromDto, codexStateFromDto, guardIssueFromDto, guardOverviewFromDto, guardResolutionAction, htmlToDocument, temporalReviewFromDto, timelineBoardFromDto, timelineReflowFromDto } from './content'

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

  it('prefers real relation rows and maps both directions', () => {
    const entry = codexFromDto({
      id: 'cx-1', project_id: 'p1', kind: 'character', name: '许知微', description: '女主',
      aliases: [], attrs: {
        relations: [{ target_id: 'legacy', name: '旧关系', relation: '旧类型', note: '不应采用' }]
      }, resident: true, status: 'confirmed', ref_chapters: [], conflicts: [],
      planted_at: null, expected_by: null,
      relations: [
        {
          id: 'rel-out', target_id: 'cx-2', target_name: '青河村', target_kind: 'location',
          relation_type: '居住于', description: '村东小院', direction: 'outgoing'
        },
        {
          id: 'rel-in', target_id: 'cx-3', target_name: '周何氏', target_kind: 'character',
          relation_type: '共同持家', description: null, direction: 'incoming'
        }
      ]
    })

    expect(entry.relations).toEqual([
      {
        id: 'rel-out', targetId: 'cx-2', targetKind: 'place', direction: 'outgoing',
        name: '青河村', relation: '居住于', note: '村东小院'
      },
      {
        id: 'rel-in', targetId: 'cx-3', targetKind: 'character', direction: 'incoming',
        name: '周何氏', relation: '共同持家', note: undefined
      }
    ])
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

describe('codexStateFromDto', () => {
  it('maps chapter anchors and optional extraction evidence', () => {
    expect(codexStateFromDto({
      id: 'claim:91',
      source: 'extracted',
      editable: false,
      state_key: '所在地点',
      value: '青石村东院',
      polarity: 'positive',
      note: null,
      chapter_id: 'p1-ch05',
      chapter_index: 5,
      chapter_title: '县城初雪',
      body_revision: 4,
      paragraph_id: 'p-7',
      confidence: 0.93,
      revision: null,
      created_at: '2026-08-14T10:20:00Z'
    })).toEqual({
      id: 'claim:91',
      source: 'extracted',
      editable: false,
      stateKey: '所在地点',
      value: '青石村东院',
      polarity: 'positive',
      chapterId: 'p1-ch05',
      chapterIndex: 5,
      chapterTitle: '县城初雪',
      bodyRevision: 4,
      paragraphId: 'p-7',
      confidence: 0.93,
      createdAt: '2026-08-14T10:20:00Z'
    })
  })
})

describe('characterStatisticsFromDto', () => {
  it('maps auditable character presence and POV fields', () => {
    const statistics = characterStatisticsFromDto({
      entry_id: 'cx-1', name: '许知微', appearance_chapters: 3,
      explicit_references: 2, extracted_claims: 4, pov_chapters: 1, pov_words: 3260,
      first_appearance: 2, last_appearance: 9, hiatus_chapters: 5,
      chapters: [{
        chapter_id: 'ch-9', chapter_index: 9, chapter_title: '秋收前夜', words: 3260,
        explicit_references: 1, extracted_claims: 2, is_pov: true
      }]
    })

    expect(statistics).toMatchObject({
      entryId: 'cx-1', appearanceChapters: 3, povChapters: 1, povWords: 3260,
      firstAppearance: 2, lastAppearance: 9, hiatusChapters: 5
    })
    expect(statistics.chapters[0]).toMatchObject({
      chapterId: 'ch-9', chapterIndex: 9, extractedClaims: 2, isPov: true
    })
  })
})

describe('Guard DTO mapping', () => {
  it('maps legacy Chinese actions and preserves evidence anchors', () => {
    const issue = guardIssueFromDto({
      id: 'g-anchor', chapter_id: 'ch4', issue_type: 'alive_conflict', severity: 'high',
      description: '测试问题', status: 'open', resolved: false, issue_rev: 1,
      confidence: 0.9, chapter_index: 4, chapter_title: '第四章',
      evidence: [{
        label: '本次正文', text: '她推开门。', accent: true,
        chapter_id: 'ch4', paragraph_id: 'p-7', start_offset: 2, end_offset: 7, source_anchor: 'claim:42'
      }],
      actions: ['以本章为准，更新设定', '改写第 4 章', '有意为之，忽略'],
      arbitration_status: 'not_requested', arbitration_confidence: null,
      arbitration_rationale: null, updated_at: '2026-09-03T10:00:00Z'
    })

    expect(issue.actionCodes).toEqual(['accept_new_fact', 'fixed_in_body', 'intentional_exception'])
    expect(issue.evidence[0]).toMatchObject({
      chapterId: 'ch4', paragraphId: 'p-7', startOffset: 2, endOffset: 7, sourceAnchor: 'claim:42'
    })
    expect(guardResolutionAction('入库')).toBe('accept_new_fact')
    expect(guardResolutionAction('这是误报')).toBe('false_positive')
    expect(guardResolutionAction('改写第 4 章')).toBe('fixed_in_body')
    expect(guardResolutionAction('回到正文确认')).toBe('fixed_in_body')
    expect(guardResolutionAction('补一段说明')).toBe('fixed_in_body')
    expect(guardResolutionAction('下一章提及')).toBe('fixed_in_body')
  })

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

  it('maps project timeline reflow diagnostics and rescan state', () => {
    const result = timelineReflowFromDto({
      claims_examined: 38,
      claims_changed: 6,
      affected_chapter_ids: ['ch1', 'ch2'],
      resolved: 4,
      unresolved: 1,
      ambiguous: 1,
      cyclic: 0,
      cycles: [],
      rescans_queued: 2,
      rescan_run_ids: [7, 8]
    })

    expect(result.claimsChanged).toBe(6)
    expect(result.affectedChapterIds).toEqual(['ch1', 'ch2'])
    expect(result.ambiguous).toBe(1)
    expect(result.rescansQueued).toBe(2)
  })

  it('maps a fuzzy temporal review without losing range precision', () => {
    const review = temporalReviewFromDto({
      claim_id: 19,
      chapter_id: 'ch5',
      chapter_index: 5,
      chapter_title: '县城初雪',
      event_ref: '冬集开市',
      relation: 'after',
      relation_ref: '许知微启程',
      original: '过几日后的清晨',
      normalized: '2-7日后+清晨',
      offset_min_seconds: 187200,
      offset_max_seconds: 633600,
      dependency_status: 'unresolved',
      override_seconds: null,
      override_version: 0
    })

    expect(review.claimId).toBe(19)
    expect(review.chapterIndex).toBe(5)
    expect(review.offsetMinSeconds).toBe(187200)
    expect(review.overrideSeconds).toBeUndefined()
  })

  it('maps multi-lane timeline events and preserves unresolved placement state', () => {
    const board = timelineBoardFromDto({
      lanes: [{
        timeline_id: 'main', label: '主线', event_count: 2, placed_count: 1, review_count: 1,
        events: [
          {
            event_id: 'claim:11', source: 'extracted', claim_id: 11, entry_id: null,
            timeline_id: 'main', event_ref: '启程', detail: null, chapter_id: 'ch1',
            chapter_index: 1, chapter_title: '离村', time_text: '2026-01-01', story_order: 10,
            placement_status: 'placed', dependency_status: 'resolved', relation: null,
            relation_ref: null, source_anchor: '第一章', confidence: 0.95, resolution_source: 'parser',
            time_start: null, time_end: null, editable: false, revision: null
          },
          {
            event_id: 'claim:12', source: 'extracted', claim_id: 12, entry_id: null,
            timeline_id: 'main', event_ref: '开市', detail: null, chapter_id: 'ch5',
            chapter_index: 5, chapter_title: '县城初雪', time_text: '过几日后', story_order: null,
            placement_status: 'review', dependency_status: 'unresolved', relation: 'after',
            relation_ref: '启程', source_anchor: null, confidence: 0.72, resolution_source: null,
            time_start: null, time_end: null, editable: false, revision: null
          }
        ]
      }],
      event_count: 2, placed_count: 1, review_count: 1, unplaced_count: 1,
      story_order_min: 10, story_order_max: 10
    })

    expect(board.lanes[0]?.events[0]).toMatchObject({ eventId: 'claim:11', source: 'extracted', eventRef: '启程', storyOrder: 10 })
    expect(board.lanes[0]?.events[1]).toMatchObject({ eventRef: '开市', placementStatus: 'review' })
    expect(board.lanes[0]?.events[1]?.storyOrder).toBeUndefined()
    expect(board.storyOrderMin).toBe(10)
  })

  it('preserves author-managed timeline identity and revision metadata', () => {
    const board = timelineBoardFromDto({
      lanes: [{
        timeline_id: 'harvest', label: '秋收线', event_count: 1, placed_count: 1, review_count: 0,
        events: [{
          event_id: 'entry:entry-7', source: 'planned', claim_id: null, entry_id: 'entry-7',
          timeline_id: 'harvest', event_ref: '返乡取得稻种', detail: '赶在霜降前育种',
          chapter_id: null, chapter_index: null, chapter_title: null, time_text: '景和三年八月',
          story_order: 18, placement_status: 'placed', dependency_status: 'manual', relation: null,
          relation_ref: null, source_anchor: null, confidence: null, resolution_source: 'author',
          time_start: null, time_end: null, editable: true, revision: 4
        }]
      }],
      event_count: 1, placed_count: 1, review_count: 0, unplaced_count: 0,
      story_order_min: 18, story_order_max: 18
    })

    expect(board.lanes[0]?.events[0]).toMatchObject({
      eventId: 'entry:entry-7', source: 'planned', entryId: 'entry-7', editable: true, revision: 4
    })
    expect(board.lanes[0]?.events[0]?.claimId).toBeUndefined()
  })
})
