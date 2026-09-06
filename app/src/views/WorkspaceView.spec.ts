import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspaceView from './WorkspaceView.vue'
import { contentApi } from '@/api/content'
import { useCodexStore } from '@/stores/codex'
import { useProjectStore } from '@/stores/project'

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
})
