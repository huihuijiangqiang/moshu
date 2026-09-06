import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TimelineView from './TimelineView.vue'
import { contentApi } from '@/api/content'
import { useProjectStore } from '@/stores/project'
import type { TimelineBoard } from '@/types'

vi.mock('@/api/content', () => ({
  contentApi: {
    getTimelineBoard: vi.fn(),
    createTimelineEntry: vi.fn(),
    updateTimelineEntry: vi.fn(),
    archiveTimelineEntry: vi.fn()
  }
}))

const emptyBoard: TimelineBoard = {
  lanes: [], eventCount: 0, placedCount: 0, reviewCount: 0, unplacedCount: 0
}

function buttonByText(root: ParentNode, text: string) {
  return Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.trim() === text)
}

async function mountTimeline(board: TimelineBoard) {
  vi.mocked(contentApi.getTimelineBoard).mockResolvedValue(board)
  const pinia = createPinia()
  setActivePinia(pinia)
  const project = useProjectStore()
  project.chapters = [{
    id: 'ch1', volumeId: 'v1', index: 1, title: '春种', words: 800,
    outline: [], outlineNote: '', summary: '', status: 'drafting'
  }]
  document.body.innerHTML = '<div id="topbar-actions"></div>'
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/timeline', component: TimelineView, meta: { scope: 'project' } },
      { path: '/projects/:projectId/write', component: { template: '<div />' } },
      { path: '/projects/:projectId/guard', component: { template: '<div />' } }
    ]
  })
  await router.push('/projects/p1/timeline')
  await router.isReady()
  const wrapper = mount(TimelineView, { attachTo: document.body, global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

describe('author-managed timeline events', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => { document.body.innerHTML = '' })

  it('creates a planned event when a number input provides a runtime number', async () => {
    const createdBoard: TimelineBoard = {
      lanes: [{
        timelineId: 'main', label: '主线', eventCount: 1, placedCount: 1, reviewCount: 0,
        events: [{
          eventId: 'entry:e1', source: 'planned', entryId: 'e1', timelineId: 'main',
          eventRef: '返乡取种', storyOrder: 12, placementStatus: 'placed',
          dependencyStatus: 'manual', editable: true, revision: 1
        }]
      }],
      eventCount: 1, placedCount: 1, reviewCount: 0, unplacedCount: 0,
      storyOrderMin: 12, storyOrderMax: 12
    }
    vi.mocked(contentApi.createTimelineEntry).mockResolvedValue({
      id: 'e1', projectId: 'p1', title: '返乡取种', timelineId: 'main', storyOrder: 12,
      status: 'active', rev: 1, createdAt: '2026-09-06T00:00:00Z', updatedAt: '2026-09-06T00:00:00Z'
    })
    const wrapper = await mountTimeline(emptyBoard)
    vi.mocked(contentApi.getTimelineBoard).mockResolvedValue(createdBoard)

    buttonByText(document, '计划事件')?.click()
    await flushPromises()
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')
    expect(dialog).not.toBeNull()
    const inputs = Array.from(dialog!.querySelectorAll<HTMLInputElement>('input'))
    const title = inputs.find((input) => input.placeholder === '例如：冬集开市')
    const storyOrder = inputs.find((input) => input.type === 'number')
    title!.value = '返乡取种'
    title!.dispatchEvent(new Event('input'))
    storyOrder!.valueAsNumber = 12
    storyOrder!.dispatchEvent(new Event('input'))
    buttonByText(dialog!, '保存事件')?.click()
    await flushPromises()

    expect(contentApi.createTimelineEntry).toHaveBeenCalledWith('p1', expect.objectContaining({
      title: '返乡取种', timelineId: 'main', storyOrder: 12
    }))
    expect(wrapper.text()).toContain('返乡取种')
    wrapper.unmount()
  })

  it('puts a chapterless planned event in the pending area in chapter mode', async () => {
    const wrapper = await mountTimeline({
      lanes: [{
        timelineId: 'main', label: '主线', eventCount: 1, placedCount: 1, reviewCount: 0,
        events: [{
          eventId: 'entry:e2', source: 'planned', entryId: 'e2', timelineId: 'main',
          eventRef: '来年扩建水渠', storyOrder: 30, placementStatus: 'placed',
          dependencyStatus: 'manual', editable: true, revision: 1
        }]
      }],
      eventCount: 1, placedCount: 1, reviewCount: 0, unplacedCount: 0,
      storyOrderMin: 30, storyOrderMax: 30
    })

    await wrapper.findAll('button').find((button) => button.text() === '章节顺序')?.trigger('click')

    expect(wrapper.get('.lane-pending').text()).toContain('来年扩建水渠')
    expect(wrapper.get('.lane-pending').text()).toContain('未绑定章节')
    expect(wrapper.find('.lane-track .timeline-event').exists()).toBe(false)
    wrapper.unmount()
  })

  it('allocates separate rows to events that would overlap horizontally', async () => {
    const events = [0, 40, 41, 42, 43, 100].map((order, index) => ({
      eventId: `entry:e${index}`, source: 'planned' as const, entryId: `e${index}`,
      timelineId: 'main', eventRef: `事件 ${order}`, storyOrder: order,
      placementStatus: 'placed' as const, dependencyStatus: 'manual', editable: true, revision: 1
    }))
    const wrapper = await mountTimeline({
      lanes: [{ timelineId: 'main', label: '主线', eventCount: 6, placedCount: 6, reviewCount: 0, events }],
      eventCount: 6, placedCount: 6, reviewCount: 0, unplacedCount: 0,
      storyOrderMin: 0, storyOrderMax: 100
    })

    const tops = wrapper.findAll('.timeline-event')
      .filter((event) => /事件 4[0-3]/.test(event.text()))
      .map((event) => event.attributes('style')?.match(/top:\s*([^;]+)/)?.[1])
    expect(new Set(tops).size).toBe(4)
    expect(wrapper.get('.lane-track').attributes('style')).toContain('298px')
    wrapper.unmount()
  })
})
