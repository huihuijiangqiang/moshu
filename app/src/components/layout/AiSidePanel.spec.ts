import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AiSidePanel from './AiSidePanel.vue'
import { contentApi } from '@/api/content'
import { generationDraftApi } from '@/api/generation'
import * as generationApi from '@/api/generation'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import type { CodexStateHistoryItem, GenerationDraftDetail, GenerationDraftSummary, ProjectNote } from '@/types'

function authorState(id: string, chapterIndex: number, value: string): CodexStateHistoryItem {
  return {
    id,
    source: 'author',
    editable: true,
    stateKey: '兵器状态',
    value,
    polarity: 'positive',
    chapterId: `ch${chapterIndex}`,
    chapterIndex,
    chapterTitle: `第 ${chapterIndex} 章`,
    revision: 1,
    createdAt: '2026-08-14T10:20:00Z'
  }
}

async function mountPanel() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/write', component: { template: '<div />' } },
      { path: '/projects/:projectId/codex', component: { template: '<div />' } },
      { path: '/projects/:projectId/guard', component: { template: '<div />' } }
    ]
  })
  await router.push('/projects/p1/write')
  await router.isReady()
  await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
  vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
  const wrapper = mount(AiSidePanel, {
    attachTo: document.body,
    props: { drafts: [], draftsLoading: false },
    global: { plugins: [pinia, router] }
  })
  await flushPromises()
  const referenceTab = wrapper.findAll('.wk-tab').find((button) => button.text() === '资料')
  if (!referenceTab) throw new Error('reference tab not found')
  await referenceTab.trigger('click')
  return { wrapper, router }
}

describe('writing reference side panel', () => {
  beforeEach(() => { document.body.innerHTML = '' })
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('keeps the manuscript visible while inspecting only states effective for the active chapter', async () => {
    vi.spyOn(contentApi, 'listCodexStateHistory').mockResolvedValue([
      authorState('state-85', 85, '残锋已经折断，只剩半截'),
      authorState('state-88', 88, '残锋已重铸')
    ])
    const { wrapper, router } = await mountPanel()

    await wrapper.get('[data-reference-id="c-shenyan"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.reference-detail').text()).toContain('沈砚')
    expect(wrapper.get('.reference-states').text()).toContain('残锋已经折断，只剩半截')
    expect(wrapper.get('.reference-states').text()).not.toContain('残锋已重铸')

    await wrapper.findAll('.reference-detail-bar button')[1]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/p1/codex')
    expect(useCodexStore().query).toBe('沈砚')
    wrapper.unmount()
  })

  it('ignores a slow state response after another reference is opened', async () => {
    let resolveFirst!: (value: CodexStateHistoryItem[]) => void
    let resolveSecond!: (value: CodexStateHistoryItem[]) => void
    vi.spyOn(contentApi, 'listCodexStateHistory').mockImplementation((_projectId, entryId) => new Promise((resolve) => {
      if (entryId === 'c-shenyan') resolveFirst = resolve
      else resolveSecond = resolve
    }))
    const { wrapper } = await mountPanel()

    await wrapper.get('[data-reference-id="c-shenyan"]').trigger('click')
    await wrapper.get('.reference-detail-bar button').trigger('click')
    await wrapper.findAll('.reference-scope button')[1]!.trigger('click')
    await wrapper.get('[data-reference-id="c-zhoutou"]').trigger('click')
    resolveSecond([authorState('state-zhou', 86, '守在城南兵器坊')])
    await flushPromises()
    expect(wrapper.get('.reference-detail').text()).toContain('守在城南兵器坊')

    resolveFirst([authorState('state-shen', 85, '不应覆盖新档案')])
    await flushPromises()
    expect(wrapper.get('.reference-detail').text()).toContain('守在城南兵器坊')
    expect(wrapper.get('.reference-detail').text()).not.toContain('不应覆盖新档案')
    wrapper.unmount()
  })

  it('previews the assembled prompt without starting generation', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: { template: '<div />' } }]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    const wrapper = mount(AiSidePanel, {
      attachTo: document.body,
      props: { drafts: [], draftsLoading: false },
      global: { plugins: [pinia, router] }
    })
    await flushPromises()

    const previewButton = wrapper.findAll('button').find((button) => button.text() === '预览提示词')
    if (!previewButton) throw new Error('prompt preview button not found')
    await previewButton.trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="region"]').text()).toContain('本次提示词')
    expect(wrapper.get('[role="region"]').text()).toContain('模拟模式不会调用模型')
    expect(wrapper.emitted('generate')).toBeUndefined()

    await wrapper.get('input[type="number"]').setValue('4500')
    expect(wrapper.get('[role="region"]').text()).toContain('生成参数已变化')
    wrapper.unmount()
  })

  it('sends the selected elastic context mode and explains preview usage', async () => {
    const basePreview = await generationApi.previewGeneration({
      chapterId: 'ch87',
      targetWords: 3000,
      model: 'basic',
      useStyleProfile: true,
      dialogueDensity: 'high',
      contextMode: 'smart'
    })
    const previewSpy = vi.spyOn(generationApi, 'previewGeneration').mockResolvedValue({
      ...basePreview,
      tokenBudget: {
        ...basePreview.tokenBudget,
        mode: 'deep',
        total: 208000,
        context: 156000,
        modelWindow: 256000,
        reservedOutput: 32000,
        safetyMargin: 16000,
        usableContext: 208000
      },
      sections: [{
        key: 'remote-evidence',
        label: '远距正文证据',
        source: '正文向量召回',
        includedTokens: 48000,
        availableTokens: 52000,
        trimReason: '低相关内容已省略'
      }]
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: { template: '<div />' } }]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    const wrapper = mount(AiSidePanel, {
      attachTo: document.body,
      props: { drafts: [], draftsLoading: false },
      global: { plugins: [pinia, router] }
    })
    await flushPromises()

    const deepMode = wrapper.findAll('.context-mode-control button').find((button) => button.text() === '深度')
    if (!deepMode) throw new Error('deep context mode not found')
    await deepMode.trigger('click')
    expect(deepMode.attributes('aria-checked')).toBe('true')

    const previewButton = wrapper.findAll('button').find((button) => button.text() === '预览提示词')
    if (!previewButton) throw new Error('prompt preview button not found')
    await previewButton.trigger('click')
    await flushPromises()

    expect(previewSpy).toHaveBeenLastCalledWith(expect.objectContaining({ contextMode: 'deep' }))
    const region = wrapper.get('[role="region"]')
    expect(region.text()).toContain('上下文用量 · 深度')
    expect(region.text()).toContain('156,000 / 208,000')
    expect(region.text()).toContain('正文向量召回')
    expect(region.text()).toContain('低相关内容已省略')

    await wrapper.get('button[data-primary="true"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('generate')?.[0]?.[0]).toMatchObject({ contextMode: 'deep' })
    wrapper.unmount()
  })

  it('keeps legacy prompt previews usable without elastic context metadata', async () => {
    const basePreview = await generationApi.previewGeneration({
      chapterId: 'ch87', targetWords: 3000, model: 'basic', useStyleProfile: true, dialogueDensity: 'high'
    })
    const { mode, modelWindow, reservedOutput, safetyMargin, usableContext, ...legacyBudget } = basePreview.tokenBudget
    vi.spyOn(generationApi, 'previewGeneration').mockResolvedValue({
      ...basePreview,
      tokenBudget: legacyBudget,
      sections: undefined,
      contextStats: undefined,
      context_stats: undefined
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: { template: '<div />' } }]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    const wrapper = mount(AiSidePanel, {
      attachTo: document.body,
      props: { drafts: [], draftsLoading: false },
      global: { plugins: [pinia, router] }
    })
    await flushPromises()

    const previewButton = wrapper.findAll('button').find((button) => button.text() === '预览提示词')
    if (!previewButton) throw new Error('prompt preview button not found')
    await previewButton.trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="region"]').text()).toContain('上下文用量 · 智能')
    expect(wrapper.get('[role="region"]').text()).toContain('0 / 128,000')
    wrapper.unmount()
  })

  it('blocks generation when the preflight reports a hard failure', async () => {
    const basePreview = await generationApi.previewGeneration({
      chapterId: 'ch87', targetWords: 3000, model: 'basic', useStyleProfile: true, dialogueDensity: 'high'
    })
    vi.spyOn(generationApi, 'previewGeneration').mockResolvedValue({
      ...basePreview,
      preflight: {
        ...basePreview.preflight,
        status: 'blocked',
        blocking: true,
        warningCount: 1,
        checks: [{ id: 'token_budget', status: 'blocked', message: '提示词超过模型硬预算。' }]
      }
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: { template: '<div />' } }]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    const wrapper = mount(AiSidePanel, {
      attachTo: document.body,
      props: { drafts: [], draftsLoading: false },
      global: { plugins: [pinia, router] }
    })
    await flushPromises()

    await wrapper.get('button[data-primary="true"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('generate')).toBeUndefined()
    expect(wrapper.get('[role="region"]').text()).toContain('提示词超过硬预算')
    expect(wrapper.get('[role="region"]').text()).toContain('提示词超过模型硬预算')
    wrapper.unmount()
  })

  it('offers retry and downgrade actions for a failed generation', async () => {
    const { wrapper } = await mountPanel()
    await wrapper.setProps({
      generationError: '积分不足：本次最多需要 35，当前剩余 2。',
      generationErrorCode: 'INSUFFICIENT_CREDITS',
      lastGenerationOptions: {
        targetWords: 3000,
        model: 'advanced',
        useStyleProfile: true,
        dialogueDensity: 'high'
      }
    })
    const aiTab = wrapper.findAll('.wk-tab').find((button) => button.text() === 'AI')
    if (!aiTab) throw new Error('AI tab not found')
    await aiTab.trigger('click')
    expect(wrapper.get('.generation-recovery').text()).toContain('积分不足')
    const actions = wrapper.findAll('.generation-recovery-actions button')
    expect(actions.map((button) => button.text())).toEqual(['重试', '切换基础档重试', '查看已保留片段'])

    await actions[0]!.trigger('click')
    await actions[1]!.trigger('click')
    expect(wrapper.emitted('retryGeneration')).toHaveLength(1)
    expect(wrapper.emitted('downgradeGeneration')).toHaveLength(1)

    await actions[2]!.trigger('click')
    expect(wrapper.find('.wk-tab[aria-selected="true"]').text()).toContain('候选')
    wrapper.unmount()
  })

  it('persists paragraph decisions and inserts only accepted draft text', async () => {
    const summary: GenerationDraftSummary = {
      id: 'draft-review', runId: 'run-review', projectId: 'p1', chapterId: 'ch87', kind: 'chapter',
      status: 'ready', generatedWords: 4, excerpt: '甲。乙。', requestSummary: {}, errorCode: null,
      createdAt: '2026-09-07T10:00:00Z', updatedAt: '2026-09-07T10:00:00Z', acceptedAt: null,
      reviewVersion: 0, review: { total: 2, pending: 2, accepted: 0, rejected: 0 }
    }
    let detail: GenerationDraftDetail = {
      ...summary,
      content: '甲。\n乙。',
      coverage: {
        stage: 'draft', blocking: false, status: 'needs_attention',
        summary: { total: 2, confirmed: 0, attention: 2, message: '2 项需要作者复核' },
        checks: [
          {
            id: 'quality.procedural_density', checkType: 'quality', sourceType: 'quality', sourceId: null,
            label: '戏剧张力', status: 'author_review', severity: 'warning',
            message: '流程说明可能挤占故事篇幅。', expected: [], evidence: ['每千字流程词约 22.0 次']
          },
          {
            id: 'quality.report_ending', checkType: 'quality', sourceType: 'quality', sourceId: null,
            label: '章末钩子', status: 'author_review', severity: 'warning',
            message: '章末可能没有落在人物行动上。', expected: [], evidence: []
          }
        ]
      },
      segments: [
        { id: 'p1', text: '甲。', decision: 'pending' },
        { id: 'p2', text: '乙。', decision: 'pending' }
      ]
    }
    vi.spyOn(generationDraftApi, 'get').mockImplementation(async () => detail)
    const review = vi.spyOn(generationDraftApi, 'review').mockImplementation(async (_id, segmentIds, decision) => {
      const segments = detail.segments.map((segment) => segmentIds.includes(segment.id) ? { ...segment, decision } : segment)
      detail = {
        ...detail,
        segments,
        reviewVersion: detail.reviewVersion + 1,
        review: {
          total: segments.length,
          pending: segments.filter((segment) => segment.decision === 'pending').length,
          accepted: segments.filter((segment) => segment.decision === 'accepted').length,
          rejected: segments.filter((segment) => segment.decision === 'rejected').length
        }
      }
      return detail
    })
    const { wrapper } = await mountPanel()
    await wrapper.setProps({ drafts: [summary] })
    const draftsTab = wrapper.findAll('.wk-tab').find((button) => button.text().includes('候选'))
    if (!draftsTab) throw new Error('draft tab not found')
    await draftsTab.trigger('click')
    await wrapper.get('.draft-row').trigger('click')
    await flushPromises()

    expect(wrapper.get('.draft-quality').text()).toContain('2 项需复核')
    expect(wrapper.get('.draft-quality').text()).toContain('戏剧张力')
    expect(wrapper.get('.draft-quality').text()).toContain('每千字流程词约 22.0 次')
    expect(wrapper.get('.draft-quality').text()).toContain('章末钩子')

    const firstAccept = wrapper.findAll('.draft-segment')[0]!.findAll('button').find((button) => button.text() === '接受')
    if (!firstAccept) throw new Error('accept paragraph button not found')
    await firstAccept.trigger('click')
    await flushPromises()
    const secondReject = wrapper.findAll('.draft-segment')[1]!.findAll('button').find((button) => button.text() === '拒绝')
    if (!secondReject) throw new Error('reject paragraph button not found')
    await secondReject.trigger('click')
    await flushPromises()

    expect(review.mock.calls.map((call) => call.slice(1))).toEqual([
      [['p1'], 'accepted', 0],
      [['p2'], 'rejected', 1]
    ])
    expect(wrapper.text()).toContain('1 接受 / 1 拒绝 / 0 待定')
    const insert = wrapper.findAll('.draft-actions button').find((button) => button.text() === '放入正文检查')
    if (!insert) throw new Error('insert reviewed draft button not found')
    await insert.trigger('click')
    expect(wrapper.emitted('insertDraft')?.[0]?.[0]).toMatchObject({ content: '甲。' })
    wrapper.unmount()
  })

  it('turns the mobile panel into persistent private quick notes', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: { template: '<div />' } }]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
    const existing: ProjectNote = {
      id: 'pn-1', projectId: 'p1', chapterId: 'ch87', chapterIndex: 87,
      chapterTitle: '断刃', content: '让老周头先认出剑穗。', createdAt: '2026-09-07T10:00:00Z'
    }
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    vi.spyOn(contentApi, 'listProjectNotes').mockResolvedValue([existing])
    const create = vi.spyOn(contentApi, 'createProjectNote').mockResolvedValue({
      ...existing, id: 'pn-2', content: '雨停后再揭示玄铁令。'
    })
    const remove = vi.spyOn(contentApi, 'deleteProjectNote').mockResolvedValue()
    const wrapper = mount(AiSidePanel, {
      attachTo: document.body,
      props: { drafts: [], draftsLoading: false, mobileReadOnly: true },
      global: { plugins: [pinia, router] }
    })
    await flushPromises()

    expect(wrapper.findAll('.wk-tab').map((button) => button.text())).toEqual(['笔记'])
    expect(wrapper.get('.quick-note-list').text()).toContain('让老周头先认出剑穗。')
    await wrapper.get('.quick-note-form textarea').setValue('雨停后再揭示玄铁令。')
    await wrapper.get('.quick-note-form').trigger('submit')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('p1', '雨停后再揭示玄铁令。', useProjectStore().activeId)
    expect(wrapper.get('.quick-note-list').text()).toContain('雨停后再揭示玄铁令。')

    await wrapper.findAll('.quick-note-row footer button')[1]!.trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith('p1', 'pn-1')
    wrapper.unmount()
  })

  it('does not let notes from a previous project replace the active project list', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: { template: '<div />' } }]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    const project = useProjectStore()
    await Promise.all([project.load('p1'), useCodexStore().load('p1')])
    let resolveP1!: (value: ProjectNote[]) => void
    let resolveP2!: (value: ProjectNote[]) => void
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    vi.spyOn(contentApi, 'listProjectNotes').mockImplementation((projectId) => new Promise((resolve) => {
      if (projectId === 'p1') resolveP1 = resolve
      else resolveP2 = resolve
    }))
    const wrapper = mount(AiSidePanel, {
      attachTo: document.body,
      props: { drafts: [], draftsLoading: false, mobileReadOnly: true },
      global: { plugins: [pinia, router] }
    })
    await Promise.resolve()

    await project.load('p2')
    await Promise.resolve()
    resolveP2([{ id: 'pn-p2', projectId: 'p2', content: '新作品的速记', createdAt: '2026-09-07T10:00:00Z' }])
    await flushPromises()
    expect(wrapper.get('.quick-note-list').text()).toContain('新作品的速记')

    resolveP1([{ id: 'pn-p1', projectId: 'p1', content: '不应覆盖当前作品', createdAt: '2026-09-07T09:00:00Z' }])
    await flushPromises()
    expect(wrapper.get('.quick-note-list').text()).toContain('新作品的速记')
    expect(wrapper.get('.quick-note-list').text()).not.toContain('不应覆盖当前作品')
    wrapper.unmount()
  })
})
