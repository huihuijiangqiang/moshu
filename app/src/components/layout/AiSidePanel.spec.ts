import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AiSidePanel from './AiSidePanel.vue'
import { contentApi } from '@/api/content'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import type { CodexStateHistoryItem } from '@/types'

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
})
