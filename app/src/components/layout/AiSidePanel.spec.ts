import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AiSidePanel from './AiSidePanel.vue'
import { contentApi } from '@/api/content'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import type { CodexStateHistoryItem, ProjectNote } from '@/types'

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
