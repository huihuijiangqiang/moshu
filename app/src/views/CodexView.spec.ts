import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CodexView from './CodexView.vue'
import { contentApi } from '@/api/content'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import type { CharacterStatistics, CodexStateHistoryItem } from '@/types'

function statistics(entryId: string, appearanceChapters: number): CharacterStatistics {
  return {
    entryId,
    name: entryId === 'c-shenyan' ? '沈砚' : '老周头',
    appearanceChapters,
    explicitReferences: 1,
    extractedClaims: 2,
    povChapters: 1,
    povWords: 3200,
    firstAppearance: 12,
    lastAppearance: 87,
    hiatusChapters: 2,
    chapters: [{
      chapterId: 'ch87', chapterIndex: 87, chapterTitle: '断刃', words: 1412,
      explicitReferences: 1, extractedClaims: 2, isPov: true
    }]
  }
}

function stateItem(entryId: string, value: string): CodexStateHistoryItem {
  return {
    id: `cs-${entryId}`,
    source: 'author',
    editable: true,
    stateKey: '所在地点',
    value,
    polarity: 'positive',
    note: '后续章节按此状态续写。',
    chapterId: 'ch87',
    chapterIndex: 87,
    chapterTitle: '断刃',
    revision: 1,
    createdAt: '2026-08-14T10:20:00Z'
  }
}

async function mountCodex() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/codex', component: CodexView },
      { path: '/projects/:projectId/write', component: { template: '<div />' } },
      { path: '/projects/:projectId/guard', component: { template: '<div />' } }
    ]
  })
  await router.push('/projects/p1/codex')
  await router.isReady()
  await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
  const wrapper = mount(CodexView, { attachTo: document.body, global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('character presence and POV tracking', () => {
  beforeEach(() => { document.body.innerHTML = '' })
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('shows auditable sources and opens the tracked chapter', async () => {
    vi.spyOn(contentApi, 'getCharacterStatistics').mockResolvedValue(statistics('c-shenyan', 3))
    const { wrapper, router } = await mountCodex()

    expect(wrapper.get('.codex-character-stats').text()).toContain('3出场章数')
    expect(wrapper.get('.codex-character-track').text()).toContain('POV')
    expect(wrapper.get('.codex-character-track').text()).toContain('正文引用 1')
    expect(wrapper.get('.codex-character-track').text()).toContain('抽取事实 2')

    await wrapper.get('.codex-character-track button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value).toMatchObject({
      path: '/projects/p1/write', query: { chapter: 'ch87' }
    })
    wrapper.unmount()
  })

  it('does not let a slow previous response replace the newly selected character', async () => {
    let resolveFirst!: (value: CharacterStatistics) => void
    let resolveSecond!: (value: CharacterStatistics) => void
    vi.spyOn(contentApi, 'getCharacterStatistics').mockImplementation((_projectId, entryId) => new Promise((resolve) => {
      if (entryId === 'c-shenyan') resolveFirst = resolve
      else resolveSecond = resolve
    }))
    const { wrapper } = await mountCodex()

    await wrapper.get('[data-entry-id="c-zhoutou"]').trigger('click')
    resolveSecond(statistics('c-zhoutou', 2))
    await flushPromises()
    expect(wrapper.get('.codex-character-stats').text()).toContain('2出场章数')

    resolveFirst(statistics('c-shenyan', 91))
    await flushPromises()
    expect(wrapper.get('.codex-character-stats').text()).toContain('2出场章数')
    expect(wrapper.get('.codex-character-stats').text()).not.toContain('91出场章数')
    wrapper.unmount()
  })
})

describe('chapter-aware Codex state history', () => {
  beforeEach(() => { document.body.innerHTML = '' })
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('shows sources, opens chapters and supports author CRUD', async () => {
    const item = stateItem('c-shenyan', '雁回关城南兵器坊')
    vi.spyOn(contentApi, 'listCodexStateHistory').mockResolvedValue([item])
    const create = vi.spyOn(contentApi, 'createCodexStateChange').mockResolvedValue(item)
    const update = vi.spyOn(contentApi, 'updateCodexStateChange').mockResolvedValue({ ...item, value: '雁回关北门' })
    const remove = vi.spyOn(contentApi, 'deleteCodexStateChange').mockResolvedValue()
    const { wrapper, router } = await mountCodex()

    expect(wrapper.get('.codex-state-row').text()).toContain('作者记录')
    expect(wrapper.get('.codex-state-row').text()).toContain('雁回关城南兵器坊')
    await wrapper.get('.codex-state-chapter').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value).toMatchObject({ path: '/projects/p1/write', query: { chapter: 'ch87' } })

    await wrapper.get('.codex-state-heading .wk-btn').trigger('click')
    const createInputs = wrapper.findAll('.codex-state-dialog input')
    await createInputs[0]!.setValue('身份')
    await createInputs[1]!.setValue('守关将领')
    await wrapper.get('.codex-state-dialog form').trigger('submit')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('p1', 'c-shenyan', expect.objectContaining({ stateKey: '身份', value: '守关将领' }))

    await wrapper.get('.codex-state-actions button').trigger('click')
    const editInputs = wrapper.findAll('.codex-state-dialog input')
    await editInputs[1]!.setValue('雁回关北门')
    await wrapper.get('.codex-state-dialog form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('p1', 'c-shenyan', item.id, 1, expect.objectContaining({ value: '雁回关北门' }))

    await wrapper.findAll('.codex-state-actions button')[1]!.trigger('click')
    await wrapper.get('.codex-delete-dialog .codex-danger-button').trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith('p1', 'c-shenyan', item.id, 1)
    wrapper.unmount()
  })

  it('does not let a slow state request overwrite the newly selected entry', async () => {
    let resolveFirst!: (value: CodexStateHistoryItem[]) => void
    let resolveSecond!: (value: CodexStateHistoryItem[]) => void
    vi.spyOn(contentApi, 'listCodexStateHistory').mockImplementation((_projectId, entryId) => new Promise((resolve) => {
      if (entryId === 'c-shenyan') resolveFirst = resolve
      else resolveSecond = resolve
    }))
    const { wrapper } = await mountCodex()

    await wrapper.get('[data-entry-id="c-zhoutou"]').trigger('click')
    resolveSecond([stateItem('c-zhoutou', '北境驿站')])
    await flushPromises()
    expect(wrapper.get('.codex-state-timeline').text()).toContain('北境驿站')

    resolveFirst([stateItem('c-shenyan', '不应出现的旧响应')])
    await flushPromises()
    expect(wrapper.get('.codex-state-timeline').text()).toContain('北境驿站')
    expect(wrapper.get('.codex-state-timeline').text()).not.toContain('不应出现的旧响应')
    wrapper.unmount()
  })
})

describe('editable Codex relations', () => {
  beforeEach(() => { document.body.innerHTML = '' })
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('creates a relation from an always-visible section', async () => {
    const create = vi.spyOn(contentApi, 'createCodexRelation').mockResolvedValue({
      id: 'rel-new', targetId: 'c-zhoutou', targetKind: 'character', direction: 'outgoing',
      name: '老周头', relation: '旧盟线索', note: '共同追查玄铁令'
    })
    const { wrapper } = await mountCodex()

    await wrapper.get('.codex-relation-heading .wk-btn').trigger('click')
    await wrapper.get('.codex-relation-dialog select').setValue('c-zhoutou')
    await wrapper.get('.codex-relation-dialog input').setValue('旧盟线索')
    await wrapper.get('.codex-relation-dialog textarea').setValue('共同追查玄铁令')
    await wrapper.get('.codex-relation-dialog form').trigger('submit')
    await flushPromises()

    expect(create).toHaveBeenCalledWith('p1', 'c-shenyan', {
      targetId: 'c-zhoutou', relation: '旧盟线索', note: '共同追查玄铁令'
    })
    wrapper.unmount()
  })

  it('keeps incoming relations read-only and opens their source entry', async () => {
    const { wrapper } = await mountCodex()
    const incoming = wrapper.get('.codex-relation-row[data-direction="incoming"]')

    expect(incoming.text()).toContain('来自')
    expect(incoming.find('.codex-relation-actions').exists()).toBe(false)
    const sourceName = incoming.get('.codex-relation-target strong').text()
    await incoming.get('.codex-relation-target').trigger('click')
    await flushPromises()
    expect(wrapper.get('.codex-identity h2').text()).toBe(sourceName)
    wrapper.unmount()
  })

  it('edits and confirms deletion of an outgoing relation', async () => {
    const update = vi.spyOn(contentApi, 'updateCodexRelation').mockResolvedValue({
      id: 'rel-updated', targetId: 'c-zhoutou', targetKind: 'character', direction: 'outgoing',
      name: '老周头', relation: '知情者', note: '补充后的说明'
    })
    const remove = vi.spyOn(contentApi, 'deleteCodexRelation').mockResolvedValue()
    const { wrapper } = await mountCodex()
    const outgoing = wrapper.get('.codex-relation-row[data-direction="outgoing"]')

    await outgoing.findAll('.codex-relation-actions button')[0]!.trigger('click')
    await wrapper.get('.codex-relation-dialog textarea').setValue('补充后的说明')
    await wrapper.get('.codex-relation-dialog form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('p1', 'c-shenyan', expect.any(String), expect.objectContaining({ note: '补充后的说明' }))

    await wrapper.get('.codex-relation-row[data-direction="outgoing"] .codex-relation-actions button:last-child').trigger('click')
    await wrapper.get('.codex-delete-dialog .codex-danger-button').trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith('p1', 'c-shenyan', expect.any(String))
    wrapper.unmount()
  })
})
