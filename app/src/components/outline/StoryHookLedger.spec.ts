import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import StoryHookLedger from './StoryHookLedger.vue'
import { storyHooksApi } from '@/api/story-hooks'
import type { Chapter, StoryHook } from '@/types'

const chapters: Chapter[] = [1, 2, 3, 4].map((index) => ({
  id: `chapter-${index}`,
  volumeId: 'volume-1',
  index: index * 1024,
  title: `第 ${index} 章标题`,
  words: index < 4 ? 2000 : 0,
  status: index < 4 ? 'done' : 'outlined',
  outline: [],
  outlineNote: ''
}))

function hook(overrides: Partial<StoryHook> = {}): StoryHook {
  return {
    id: 'hook-1',
    projectId: 'project-1',
    sourceChapterId: 'chapter-1',
    hookType: '证据缺口',
    concreteEvent: '账册最后一页已经被人撕走。',
    unresolvedQuestion: '谁先拿走了名单？',
    payoffByChapter: 3,
    status: 'open',
    noveltySignature: 'signature',
    resolution: '',
    revision: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides
  }
}

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((button) => button.text().trim() === text)
}

describe('StoryHookLedger', () => {
  afterEach(() => vi.restoreAllMocks())

  it('marks unresolved hooks against the current writing chapter', async () => {
    vi.spyOn(storyHooksApi, 'list').mockResolvedValue([
      hook({ id: 'overdue-hook', payoffByChapter: 3 }),
      hook({ id: 'due-hook', payoffByChapter: 4, hookType: '倒计时' })
    ])
    const wrapper = mount(StoryHookLedger, {
      props: { projectId: 'project-1', chapters, focusChapterId: 'chapter-4' }
    })
    await flushPromises()

    expect(wrapper.get('[data-hook-id="overdue-hook"]').attributes('data-urgency')).toBe('overdue')
    expect(wrapper.get('[data-hook-id="overdue-hook"]').text()).toContain('已逾期')
    expect(wrapper.get('[data-hook-id="due-hook"]').attributes('data-urgency')).toBe('due')
    expect(wrapper.get('[data-hook-id="due-hook"]').text()).toContain('本章到期')
  })

  it('creates a concrete hook debt from the selected chapter', async () => {
    vi.spyOn(storyHooksApi, 'list').mockResolvedValue([])
    const create = vi.spyOn(storyHooksApi, 'create').mockResolvedValue(hook({
      sourceChapterId: 'chapter-2',
      payoffByChapter: 4
    }))
    const wrapper = mount(StoryHookLedger, {
      props: { projectId: 'project-1', chapters, focusChapterId: 'chapter-2' }
    })
    await flushPromises()

    await buttonByText(wrapper, '新增伏笔')?.trigger('click')
    await wrapper.get('input[placeholder="例如：身份疑云"]').setValue('证据缺口')
    await wrapper.get('textarea[placeholder="必须是读者已经看见的动作、物件或局面变化"]').setValue('账册最后一页已经被人撕走。')
    await wrapper.get('textarea[placeholder="这件事接下来必须回答什么"]').setValue('谁先拿走了名单？')
    await wrapper.get('input[type="number"]').setValue('4')
    await buttonByText(wrapper, '加入台账')?.trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledWith('project-1', {
      sourceChapterId: 'chapter-2',
      hookType: '证据缺口',
      concreteEvent: '账册最后一页已经被人撕走。',
      unresolvedQuestion: '谁先拿走了名单？',
      payoffByChapter: 4
    })
    expect(wrapper.text()).toContain('后续生成会持续追踪')
  })

  it('records the payoff chapter and visible consequence before resolving', async () => {
    vi.spyOn(storyHooksApi, 'list').mockResolvedValue([hook({ payoffByChapter: 4 })])
    const update = vi.spyOn(storyHooksApi, 'update').mockResolvedValue(hook({
      payoffByChapter: 4,
      payoffChapterId: 'chapter-4',
      resolution: '名单藏在粮车夹层，被当众取出。',
      status: 'resolved',
      revision: 2
    }))
    const wrapper = mount(StoryHookLedger, {
      props: { projectId: 'project-1', chapters, focusChapterId: 'chapter-4' }
    })
    await flushPromises()

    await wrapper.get('[data-hook-id="hook-1"]').trigger('click')
    await buttonByText(wrapper, '标记兑现')?.trigger('click')
    await wrapper.get('.hook-resolution select').setValue('chapter-4')
    await wrapper.get('textarea[placeholder="谁做了什么，局面因此发生了什么变化"]').setValue('名单藏在粮车夹层，被当众取出。')
    await buttonByText(wrapper, '确认兑现')?.trigger('click')
    await flushPromises()

    expect(update).toHaveBeenCalledWith('project-1', 'hook-1', {
      expectedRevision: 1,
      status: 'resolved',
      payoffChapterId: 'chapter-4',
      resolution: '名单藏在粮车夹层，被当众取出。'
    })
    expect(wrapper.text()).toContain('伏笔已兑现')
  })
})
