import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { provenanceApi, type ProvenanceReport } from '@/api/provenance'
import { naturalizationApi } from '@/api/naturalization'
import { useProjectStore } from '@/stores/project'
import AiRatioView from './AiRatioView.vue'

const report: ProvenanceReport = {
  scope: 'chapter', totalWords: 28, aiRawWords: 28, aiEditedWords: 0, humanWords: 0,
  paragraphs: [{ id: 'p-risk', text: '值得注意的是，这不仅是雨，而且像是回答。', words: 22, source: 'ai-raw', runId: 'run-1' }],
  suspectedSentences: [{
    id: 'p-risk:0:22', paragraphId: 'p-risk', text: '值得注意的是，这不仅是雨，而且像是回答。',
    start: 0, end: 22, source: 'ai-raw', score: 71, reasons: ['模板化衔接', '成套并列句式']
  }],
  sentenceRiskVersion: 'zh-fiction-risk-v1',
  sentenceRiskDisclaimer: '只提示需要人工复核的表达风险。'
}

async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useProjectStore()
  store.project = {
    id: 'p1', title: '试写小说', volumes: [{ id: 'v1', index: 1, title: '第一卷' }],
    dailyGoal: 3000, dailyWords: 0, styleProfile: null
  }
  store.loadedProjectId = 'p1'
  store.chapters = [{
    id: 'ch1', volumeId: 'v1', index: 1, title: '雨夜', words: 28, status: 'drafting',
    outline: [], outlineNote: '', content: '<p>正文</p>', rev: 1
  }]
  store.activeId = 'ch1'
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/ai-ratio', component: AiRatioView },
      { path: '/projects/:projectId/write', component: { template: '<div />' } }
    ]
  })
  await router.push('/projects/p1/ai-ratio')
  await router.isReady()
  const wrapper = mount(AiRatioView, { attachTo: document.body, global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('AiRatioView sentence proofing', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="topbar-actions"></div>'
    vi.spyOn(provenanceApi, 'report').mockResolvedValue(report)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it.each([
    ['定位', undefined],
    ['重写', '1']
  ])('opens the exact sentence when clicking %s', async (label, rewrite) => {
    const { wrapper, router } = await mountView()
    const button = wrapper.findAll('.proof-actions button').find((item) => item.text().includes(label))
    expect(button).toBeDefined()

    await button!.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/projects/p1/write')
    expect(router.currentRoute.value.query).toMatchObject({
      chapter: 'ch1', paragraph: 'p-risk', start: '0', end: '22', ...(rewrite ? { rewrite } : {})
    })
    wrapper.unmount()
  })

  it('starts an author-reviewed naturalization scan without rewriting the chapter', async () => {
    const scan = vi.spyOn(naturalizationApi, 'scan')
    const { wrapper } = await mountView()
    await wrapper.get('.naturalization-heading button').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 80))
    await flushPromises()

    expect(scan).toHaveBeenCalledWith('p1', expect.objectContaining({ chapterId: 'ch1', mode: 'rules' }))
    expect(wrapper.text()).toContain('本章没有命中当前自然化规则')
    wrapper.unmount()
  })
})
