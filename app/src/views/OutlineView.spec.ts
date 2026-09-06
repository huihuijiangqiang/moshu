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

  it('creates and edits volumes and exposes the recycle bin', async () => {
    const { wrapper, store } = await mountOutline()
    const before = store.project?.volumes.length ?? 0

    const topbarButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('#topbar-actions button'))
    topbarButtons.find((button) => button.textContent?.trim() === '新建卷')?.click()
    await flushPromises()
    await wrapper.get('input[placeholder="例如：第一卷 · 落脚"]').setValue('终卷 · 归乡')
    await wrapper.get('textarea[placeholder="记录本卷目标、转折与收束"]').setValue('收束主线。')
    await wrapper.get('.outline-dialog footer button[data-primary="true"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 520))
    await flushPromises()

    expect(store.project?.volumes).toHaveLength(before + 1)
    expect(wrapper.text()).toContain('终卷 · 归乡')

    topbarButtons.find((button) => button.textContent?.trim() === '回收站')?.click()
    await waitForSave()
    expect(wrapper.get('[aria-label="作品回收站"]').text()).toContain('没有已删除章节')
    wrapper.unmount()
  })

  it('assigns a confirmed character as chapter POV and shows it in the outline', async () => {
    const { wrapper, store } = await mountOutline()
    await new Promise((resolve) => setTimeout(resolve, 320))
    await flushPromises()
    await wrapper.get('[data-chapter-id="ch89"]').trigger('click')
    const select = wrapper.get<HTMLSelectElement>('.outline-pov-field select')
    expect(select.findAll('option').some((option) => option.text().includes('沈砚'))).toBe(true)

    await select.setValue('c-shenyan')
    await new Promise((resolve) => setTimeout(resolve, 140))
    await flushPromises()

    expect(store.chapters.find((chapter) => chapter.id === 'ch89')).toMatchObject({
      povEntryId: 'c-shenyan', povRevision: 1
    })
    expect(wrapper.text()).toContain('本章视角已设为沈砚')
    expect(wrapper.get('[data-chapter-id="ch89"]').text()).toContain('视角 · 沈砚')
    wrapper.unmount()
  })

  it('reorders volumes and moves chapters with native drag and drop', async () => {
    const { wrapper, store } = await mountOutline()
    const volumeSlots = wrapper.findAll('.outline-volume-slot')
    expect(volumeSlots.length).toBeGreaterThanOrEqual(2)
    const sourceVolumeId = store.project?.volumes[0]?.id
    const targetVolumeId = store.project?.volumes[1]?.id
    expect(sourceVolumeId).toBeTruthy()
    expect(targetVolumeId).toBeTruthy()

    await wrapper.get(`[data-volume-id="${sourceVolumeId}"]`).trigger('dragstart')
    expect(wrapper.get(`[data-volume-id="${sourceVolumeId}"]`).attributes('data-dragging')).toBe('true')
    await wrapper.get(`[data-volume-id="${targetVolumeId}"]`).trigger('dragover')
    expect(wrapper.get(`[data-volume-id="${targetVolumeId}"]`).attributes('data-drop-target')).toBe('true')
    await wrapper.get(`[data-volume-id="${targetVolumeId}"]`).trigger('drop')
    await new Promise((resolve) => setTimeout(resolve, 420))
    await flushPromises()
    const volumeOrder = store.project?.volumes.map((volume) => volume.id) ?? []
    expect(volumeOrder.indexOf(targetVolumeId!)).toBeLessThan(volumeOrder.indexOf(sourceVolumeId!))

    await wrapper.get('[data-volume-id="v2"] > button').trigger('click')
    await wrapper.get('[data-chapter-id="ch85"]').trigger('click')
    const target = wrapper.get('[data-chapter-id="ch86"]')
    await wrapper.get('[data-chapter-id="ch85"]').trigger('dragstart')
    expect(wrapper.get('[data-chapter-id="ch85"]').attributes('data-dragging')).toBe('true')
    await target.trigger('dragover')
    expect(target.attributes('data-drop-target')).toBe('true')
    await target.trigger('drop', { dataTransfer: { getData: () => 'chapter:ch85' } })
    await new Promise((resolve) => setTimeout(resolve, 420))
    await flushPromises()

    const moved = store.chapters.find((chapter) => chapter.id === 'ch85')
    expect(moved?.volumeId).toBe('v2')
    const v2Chapters = store.byVolume.find((group) => group.volume.id === 'v2')?.chapters.map((chapter) => chapter.id) ?? []
    expect(v2Chapters.indexOf('ch85')).toBe(v2Chapters.indexOf('ch86') + 1)
    wrapper.unmount()
  })
})
