import { describe, expect, it } from 'vitest'
import { codexFromDto, htmlToDocument } from './content'

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
