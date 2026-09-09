import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GuardView from './GuardView.vue'
import { useGuardStore } from '@/stores/guard'
import { useProjectStore } from '@/stores/project'
import { contentApi } from '@/api/content'

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

  it('opens the evidence paragraph with compatible writer query parameters', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:projectId/guard', component: GuardView },
        { path: '/projects/:projectId/write', component: { template: '<div />' } }
      ]
    })
    await router.push('/projects/p1/guard')
    await router.isReady()
    const project = useProjectStore()
    await project.load('p1')
    const guard = useGuardStore()
    const evidence = { label: '本次正文', text: '她推开门。', accent: true } as typeof guard.issues[number]['evidence'][number]
    Object.assign(evidence, { chapterId: 'ch87', paragraphId: 'p-7', startOffset: 2, endOffset: 7, sourceAnchor: 'claim:42' })
    guard.issues = [{
      id: 'g-anchor', kind: 'conflict', severity: 'high', category: '设定冲突', title: '锚点测试',
      chapterRef: '第 87 章', chapterId: 'ch87', evidence: [evidence], actions: ['打开正文'],
      actionCodes: ['fixed_in_body'], resolved: false, arbitrationStatus: 'not_requested'
    }]
    const wrapper = mount(GuardView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await flushPromises()

    expect(wrapper.get('button').text()).toBeTruthy()
    const locate = wrapper.findAll('button').find((button) => button.text() === '定位此证据')
    expect(locate).toBeTruthy()
    await locate!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/projects/p1/write')
    expect(router.currentRoute.value.query).toMatchObject({ chapter: 'ch87', paragraph: 'p-7', start: '2', end: '7', anchor: 'claim:42' })
    wrapper.unmount()
  })

  it('shows the recorded action after resolving instead of silently showing resolved', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/guard', component: GuardView }]
    })
    await router.push('/projects/p1/guard')
    await router.isReady()
    const project = useProjectStore()
    await project.load('p1')
    const guard = useGuardStore()
    guard.loadedProjectId = 'p1'
    guard.issues = [{
      id: 'g-action', kind: 'conflict', severity: 'mid', category: '设定冲突', title: '动作测试',
      chapterRef: '第 1 章', chapterId: 'p1-ch01', evidence: [],
      actions: ['以本章为准，更新设定'], actionCodes: ['accept_new_fact'], issueRev: 1,
      resolved: false, arbitrationStatus: 'not_requested'
    }]
    const resolve = vi.spyOn(contentApi, 'resolveGuardIssue').mockResolvedValue()
    const wrapper = mount(GuardView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await flushPromises()

    const actionButton = wrapper.findAll('button').find((button) => button.text().includes('以本章为准'))
    expect(actionButton).toBeTruthy()
    await actionButton!.trigger('click')
    await flushPromises()
    expect(resolve).toHaveBeenCalledWith('p1', 'g-action', 1, 'accept_new_fact')
    expect(wrapper.text()).toContain('已采用本章新事实')
    expect(wrapper.text()).toContain('请在设定库确认条目描述和状态')
    wrapper.unmount()
  })

  it('uses the leading chapter number when the chapter title contains other numbers', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/:projectId/guard', component: GuardView },
        { path: '/projects/:projectId/write', component: { template: '<div />' } }
      ]
    })
    await router.push('/projects/p1/guard')
    await router.isReady()
    const project = useProjectStore()
    await project.load('p1')
    const guard = useGuardStore()
    guard.issues = [{
      id: 'g-title-number', kind: 'conflict', severity: 'mid', category: '时间冲突',
      title: '标题数字回退测试', chapterRef: '第 87 章 · 二十四节气', evidence: [],
      actions: ['打开对应章节'], actionCodes: ['fixed_in_body'], resolved: false,
      arbitrationStatus: 'not_requested'
    }]
    const wrapper = mount(GuardView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '打开对应章节')?.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.chapter).toBe('ch87')
    wrapper.unmount()
  })
})
