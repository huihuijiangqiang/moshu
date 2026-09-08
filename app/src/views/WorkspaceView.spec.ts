import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspaceView from './WorkspaceView.vue'
import { contentApi } from '@/api/content'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { GenerationError, generationDraftApi, streamChapter } from '@/api/generation'
import { scenesApi, type ChapterScene } from '@/api/scenes'
import type { GenerationDraftDetail, GenerationDraftSummary } from '@/types'

vi.mock('@/api/generation', async () => {
  const actual = await vi.importActual<typeof import('@/api/generation')>('@/api/generation')
  return { ...actual, streamChapter: vi.fn(() => () => undefined) }
})

describe('mobile writing workspace', () => {
  const originalWidth = window.innerWidth

  beforeEach(() => {
    document.body.innerHTML = ''
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
  })

  it('keeps manuscript read-only and opens the persistent quick-note panel', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:projectId/write', component: WorkspaceView },
        { path: '/projects/:projectId/outline', component: { template: '<div />' } },
        { path: '/projects/:projectId/guard', component: { template: '<div />' } }
      ]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    vi.spyOn(contentApi, 'listProjectNotes').mockResolvedValue([])

    const wrapper = mount(WorkspaceView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] }
    })
    await flushPromises()

    expect(wrapper.get('.mobile-readonly-banner').text()).toContain('手机只读')
    expect(wrapper.get('.ProseMirror').attributes('contenteditable')).toBe('false')
    expect(wrapper.find('.ai-floating').exists()).toBe(false)
    await wrapper.get('.mobile-readonly-banner button').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.wk-pane-right .wk-tab').map((button) => button.text())).toEqual(['笔记'])
    wrapper.unmount()
  })

  it('creates a chapter from the writing sidebar after the current body is safe', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:projectId/write', component: WorkspaceView },
        { path: '/projects/:projectId/outline', component: { template: '<div />' } },
        { path: '/projects/:projectId/guard', component: { template: '<div />' } },
        { path: '/projects/:projectId/codex', component: { template: '<div />' } }
      ]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    const store = useProjectStore()
    await Promise.all([store.load('p1'), useCodexStore().load('p1')])
    const shell = useShellStore()
    shell.leftOpen = true
    shell.rightOpen = false
    const current = store.active
    if (!current) throw new Error('active chapter not found')
    const created = {
      ...current,
      id: 'ch-new-from-write',
      index: current.index + 1,
      title: '未命名章节',
      words: 0,
      content: ''
    }
    const insert = vi.spyOn(contentApi, 'insertChapter').mockResolvedValue(created)
    const wrapper = mount(WorkspaceView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] }
    })
    await flushPromises()
    expect(wrapper.find('.mobile-readonly-banner').exists()).toBe(false)

    await wrapper.get('.wk-pane-left button[aria-label="新建章节"]').trigger('click')
    await vi.waitFor(() => expect(insert).toHaveBeenCalledWith('p1', current.volumeId, current.index), { timeout: 3000 })
    await flushPromises()
    expect(insert).toHaveBeenCalledWith('p1', current.volumeId, current.index)
    expect(store.activeId).toBe(created.id)
    wrapper.unmount()
  })

  it('starts the first chapter candidate once after the wizard redirects', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: WorkspaceView }]
    })
    await router.push('/projects/p1/write?chapter=ch88&autoGenerate=1')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    vi.spyOn(contentApi, 'listProjectNotes').mockResolvedValue([])

    const wrapper = mount(WorkspaceView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] }
    })
    await vi.waitFor(() => expect(streamChapter).toHaveBeenCalledTimes(1), { timeout: 3000 })

    expect(streamChapter).toHaveBeenCalledWith(
      expect.objectContaining({ chapterId: 'ch88', targetWords: 3000, model: 'basic' }),
      expect.any(Object)
    )
    expect(router.currentRoute.value.query.autoGenerate).toBeUndefined()
    expect(router.currentRoute.value.query.chapter).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps scene context visible and links it back to planning and chapter guard', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:projectId/write', component: WorkspaceView },
        { path: '/projects/:projectId/outline', component: { template: '<div />' } },
        { path: '/projects/:projectId/guard', component: { template: '<div />' } }
      ]
    })
    const scene: ChapterScene = {
      id: 'scene-ch89-review', chapterId: 'ch89', order: 2,
      goal: '拿到出城文书', obstacle: '校尉认出了印泥', turn: '校尉提出交换条件',
      infoGain: '', emotionShift: '', hook: '', status: 'ready', rev: 1, outlineRev: 1,
      createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z'
    }
    vi.spyOn(scenesApi, 'list').mockResolvedValue([scene])
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    vi.spyOn(contentApi, 'listProjectNotes').mockResolvedValue([])
    await router.push('/projects/p1/write?chapter=ch89&scene=scene-ch89-review')
    await router.isReady()
    await Promise.all([useProjectStore().load('p1'), useCodexStore().load('p1')])

    const wrapper = mount(WorkspaceView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] }
    })
    await vi.waitFor(() => expect(wrapper.find('.scene-context-strip').exists()).toBe(true))
    expect(wrapper.get('.scene-context-strip').text()).toContain('拿到出城文书')

    await wrapper.findAll('.scene-context-actions button')[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/p1/outline')
    expect(router.currentRoute.value.query).toMatchObject({
      chapter: 'ch89', mode: 'scenes', scene: 'scene-ch89-review'
    })

    await router.push('/projects/p1/write?chapter=ch89&scene=scene-ch89-review')
    await flushPromises()
    await wrapper.findAll('.scene-context-actions button')[1].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/p1/guard')
    expect(router.currentRoute.value.query).toMatchObject({ chapter: 'ch89' })
    wrapper.unmount()
  })

  it('applies an external naturalization replacement immediately when the editor is clean', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: WorkspaceView }]
    })
    await router.push('/projects/p1/write?chapter=ch87')
    await router.isReady()
    const store = useProjectStore()
    await store.load('p1')
    const chapterStructure = store.chapters.find((item) => item.id === 'ch87')
    if (!chapterStructure) throw new Error('chapter not found')
    vi.spyOn(contentApi, 'getChapter').mockResolvedValue({
      ...chapterStructure,
      content: '<p>沈砚正在检查旧伤。</p>',
      rev: 1
    })
    store.activeId = 'ch87'
    await store.openChapter('ch87')
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    vi.spyOn(contentApi, 'listProjectNotes').mockResolvedValue([])
    const wrapper = mount(WorkspaceView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await vi.waitFor(() => expect(wrapper.get('.ProseMirror').text()).toContain('沈砚'), { timeout: 3000 })
    const chapter = store.chapters.find((item) => item.id === 'ch87')
    if (!chapter) throw new Error('chapter not found')
    chapter.content = '<p>自然化后的正文</p>'
    chapter.rev = (chapter.rev ?? 0) + 1
    window.dispatchEvent(new CustomEvent('moshu:chapter-replaced', { detail: { chapterIds: ['ch87'] } }))
    await vi.waitFor(() => expect(wrapper.get('.ProseMirror').text()).toContain('自然化后的正文'))
    expect(wrapper.text()).toContain('正文已同步自然化修改')
    wrapper.unmount()
  })

  it('recovers when continuing a failed draft is rejected before streaming starts', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/write', component: WorkspaceView }]
    })
    await router.push('/projects/p1/write')
    await router.isReady()
    const store = useProjectStore()
    await Promise.all([store.load('p1'), useCodexStore().load('p1')])
    const chapterId = store.activeId
    if (!chapterId) throw new Error('active chapter not found')
    const summary: GenerationDraftSummary = {
      id: 'draft-interrupted', runId: 'run-interrupted', projectId: 'p1', chapterId, kind: 'chapter',
      status: 'failed', generatedWords: 320, excerpt: '雨声压住了院外的脚步。', requestSummary: {},
      errorCode: 'network_error', createdAt: '2026-09-08T10:00:00Z', updatedAt: '2026-09-08T10:01:00Z',
      acceptedAt: null, reviewVersion: 0, review: { total: 1, pending: 1, accepted: 0, rejected: 0 }
    }
    const detail: GenerationDraftDetail = {
      ...summary,
      content: '雨声压住了院外的脚步。',
      segments: [{ id: 'segment-1', text: '雨声压住了院外的脚步。', decision: 'pending' }]
    }
    vi.spyOn(contentApi, 'getContextLayers').mockResolvedValue([])
    vi.spyOn(contentApi, 'listProjectNotes').mockResolvedValue([])
    vi.spyOn(generationDraftApi, 'list').mockResolvedValue([summary])
    vi.spyOn(generationDraftApi, 'get').mockResolvedValue(detail)
    const continuation = vi.spyOn(generationDraftApi, 'continue').mockImplementation(() => {
      throw new GenerationError('continuation_not_available', '当前候选已经无法继续，请重新生成')
    })

    const wrapper = mount(WorkspaceView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await flushPromises()
    const draftsTab = wrapper.findAll('.wk-tab').find((button) => button.text().includes('候选'))
    if (!draftsTab) throw new Error('draft tab not found')
    await draftsTab.trigger('click')
    await wrapper.get('.draft-row').trigger('click')
    await flushPromises()
    const continueButton = wrapper.findAll('.draft-actions button').find((button) => button.text() === '从末尾继续')
    if (!continueButton) throw new Error('continue button not found')
    await continueButton.trigger('click')
    await flushPromises()

    expect(continuation).toHaveBeenCalledWith('draft-interrupted', expect.any(Object), expect.any(Object))
    expect(continueButton.attributes('disabled')).toBeUndefined()
    const aiTab = wrapper.findAll('.wk-tab').find((button) => button.text() === 'AI')
    if (!aiTab) throw new Error('AI tab not found')
    await aiTab.trigger('click')
    expect(wrapper.get('.generation-recovery').text()).toContain('当前候选已经无法继续，请重新生成')
    wrapper.unmount()
  })
})
