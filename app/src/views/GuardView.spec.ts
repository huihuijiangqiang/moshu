import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import GuardView from './GuardView.vue'
import { useGuardStore } from '@/stores/guard'
import { useProjectStore } from '@/stores/project'

describe('chapter-scoped guard navigation', () => {
  beforeEach(() => { document.body.innerHTML = '' })
  afterEach(() => { document.body.innerHTML = '' })

  it('filters issues to the requested chapter and can restore the whole book', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:projectId/guard', component: GuardView },
        { path: '/projects/:projectId/write', component: { template: '<div />' } }
      ]
    })
    await router.push('/projects/p1/guard?chapter=ch87')
    await router.isReady()
    await useProjectStore().load('p1')
    const guard = useGuardStore()
    guard.issues = [
      { id: 'g-ch87', kind: 'conflict', severity: 'high', category: '器物状态', title: '第 87 章冲突', chapterRef: '第 87 章', chapterId: 'ch87', evidence: [], actions: [], resolved: false, arbitrationStatus: 'pending' },
      { id: 'g-ch84', kind: 'conflict', severity: 'mid', category: '人物能力', title: '第 84 章冲突', chapterRef: '第 84 章', chapterId: 'ch84', evidence: [], actions: [], resolved: false, arbitrationStatus: 'pending' }
    ]

    const wrapper = mount(GuardView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] }
    })
    await flushPromises()

    expect(wrapper.get('.guard-chapter-scope').text()).toContain('本章守卫')
    expect(wrapper.get('[aria-label="告警列表"]').text()).toContain('第 87 章冲突')
    expect(wrapper.get('[aria-label="告警列表"]').text()).not.toContain('第 84 章冲突')
    await wrapper.get('.guard-chapter-scope button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.chapter).toBeUndefined()
    expect(wrapper.get('[aria-label="告警列表"]').text()).toContain('第 84 章冲突')
    wrapper.unmount()
  })
})
