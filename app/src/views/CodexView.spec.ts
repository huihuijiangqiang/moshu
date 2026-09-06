import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CodexView from './CodexView.vue'
import { contentApi } from '@/api/content'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import type { CharacterStatistics } from '@/types'

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
