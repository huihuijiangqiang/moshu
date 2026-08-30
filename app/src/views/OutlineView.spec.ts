import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import OutlineView from './OutlineView.vue'
import { useProjectStore } from '@/stores/project'

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((button) => button.text().trim() === text)
}

async function waitForSave() {
  await new Promise((resolve) => setTimeout(resolve, 220))
  await flushPromises()
}

async function mountOutline() {
  document.body.innerHTML = '<div id="topbar-actions"></div>'
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/outline', component: OutlineView },
      { path: '/projects/:projectId/write', component: { template: '<div />' } },
      { path: '/projects/:projectId/guard', component: { template: '<div />' } }
    ]
  })
  await router.push('/projects/p1/outline')
  await router.isReady()
  const store = useProjectStore()
  await store.load('p1')
  const wrapper = mount(OutlineView, { attachTo: document.body, global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, store }
}

describe('continuous outline editing', () => {
  beforeEach(() => { document.body.innerHTML = '' })
  afterEach(() => { document.body.innerHTML = '' })

  it('requires an explicit body decision when an outlined plan changes after writing', async () => {
    const { wrapper, store } = await mountOutline()
    await wrapper.get('[data-chapter-id="ch85"]').trigger('click')
    await wrapper.get('input[placeholder="未命名章节"]').setValue('雪夜叩关 · 修订')
    await buttonByText(wrapper, '保存章纲')?.trigger('click')

    expect(wrapper.text()).toContain('这章已经有正文')
    expect(store.chapters.find((chapter) => chapter.id === 'ch85')?.title).toBe('雪夜叩关')

    await buttonByText(wrapper, '仅更新计划')?.trigger('click')
    await waitForSave()
    expect(store.chapters.find((chapter) => chapter.id === 'ch85')).toMatchObject({
      title: '雪夜叩关 · 修订',
      bodyNeedsRevision: false,
      outlineRevision: 1
    })

    await wrapper.get('textarea[placeholder="记录人物选择、伏笔或章末钩子"]').setValue('调整雪夜发生的时间。')
    await buttonByText(wrapper, '保存章纲')?.trigger('click')
    await buttonByText(wrapper, '更新计划并标记正文')?.trigger('click')
    await waitForSave()
    expect(store.chapters.find((chapter) => chapter.id === 'ch85')).toMatchObject({
      bodyNeedsRevision: true,
      outlineRevision: 2
    })
    expect(wrapper.text()).toContain('正文待调整')
    wrapper.unmount()
  })

  it('saves an unwritten chapter directly and inserts the next chapter', async () => {
    const { wrapper, store } = await mountOutline()
    const before = store.chapters.length
    await wrapper.get('[data-chapter-id="ch89"]').trigger('click')
    await wrapper.get('input[placeholder="未命名章节"]').setValue('北上遇伏')
    await buttonByText(wrapper, '保存章纲')?.trigger('click')
    await waitForSave()

    expect(wrapper.text()).not.toContain('这章已经有正文')
    expect(store.chapters.find((chapter) => chapter.id === 'ch89')?.title).toBe('北上遇伏')

    await buttonByText(wrapper, '在当前章后插入')?.trigger('click')
    await waitForSave()
    expect(store.chapters).toHaveLength(before + 1)
    expect(store.chapters.some((chapter) => chapter.index === 90 && chapter.status === 'outlined')).toBe(true)
    wrapper.unmount()
  })
})
