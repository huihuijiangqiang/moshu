import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ReviewPanel from './ReviewPanel.vue'
import type { ReviewWorkspace } from '@/types'

function workspace(): ReviewWorkspace {
  return {
    chapterId: 'ch-1', currentBodyRevision: 3, canSubmit: true, canReview: true, canResolve: true,
    rounds: [{
      id: 'round-1', chapterId: 'ch-1', submittedBodyRevision: 2, status: 'submitted', revision: 1,
      submittedByName: '执笔人', submittedAt: '2026-09-06T08:20:00Z', stale: true,
      comments: [{
        id: 'comment-1', roundId: 'round-1', chapterId: 'ch-1', bodyRevision: 2,
        paragraphId: 'p-field', paragraphExcerpt: '她在春汛前整好两亩荒地。', selectedText: '两亩荒地',
        content: '补充必须赶在春汛前完成的现实压力。', status: 'open', revision: 1,
        authorName: '主编', createdAt: '2026-09-06T08:35:00Z'
      }]
    }]
  }
}

describe('ReviewPanel', () => {
  it('keeps approval blocked while an open comment remains and locates its paragraph', async () => {
    const current = workspace()
    current.rounds[0]!.stale = false
    const wrapper = mount(ReviewPanel, { props: { workspace: current } })

    expect(wrapper.text()).toContain('1 待处理 / 1 条')
    const approval = wrapper.findAll('button').find((button) => button.text() === '批准本版')
    expect(approval?.attributes('disabled')).toBeDefined()

    await wrapper.find('.review-comment-anchor').trigger('click')
    expect(wrapper.emitted('locate')?.[0]?.[0]).toMatchObject({ paragraphId: 'p-field' })
  })

  it('creates a comment only after the editor supplies a paragraph anchor', async () => {
    const current = workspace()
    current.rounds[0]!.stale = false
    const wrapper = mount(ReviewPanel, {
      props: {
        workspace: current,
        anchor: { paragraphId: 'p-new', paragraphText: '她推开柴门。', selectedText: '柴门' }
      }
    })
    const textarea = wrapper.find('.review-compose textarea')
    await textarea.setValue('动作之后补一个听觉细节。')
    await wrapper.find('.review-compose button').trigger('click')

    expect(wrapper.emitted('addComment')).toEqual([['round-1', '动作之后补一个听觉细节。']])
  })

  it('submits only a newer saved body revision', async () => {
    const current = workspace()
    current.currentBodyRevision = 2
    const wrapper = mount(ReviewPanel, { props: { workspace: current } })
    expect(wrapper.text()).not.toContain('提交第 2 版')

    await wrapper.setProps({ workspace: { ...current, currentBodyRevision: 3 } })
    expect(wrapper.text()).toContain('提交第 3 版')
  })

  it('does not let an editor annotate or approve a stale submitted version', () => {
    const wrapper = mount(ReviewPanel, { props: { workspace: workspace() } })
    expect(wrapper.text()).toContain('请作者提交新版本')
    expect(wrapper.find('.review-compose').exists()).toBe(false)
    expect(wrapper.find('.review-decision').exists()).toBe(false)
  })
})
