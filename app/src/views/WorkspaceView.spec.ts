import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspaceView from './WorkspaceView.vue'
import { contentApi } from '@/api/content'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import { streamChapter } from '@/api/generation'

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
})
