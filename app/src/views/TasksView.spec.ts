import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const { overview, trigger } = vi.hoisted(() => ({ overview: vi.fn(), trigger: vi.fn() }))
vi.mock('@/api/tasks', () => ({
  tasksApi: { overview, trigger }
}))

import TasksView from './TasksView.vue'

describe('TasksView', () => {
  it('renders persisted activity and starts a safe retry action', async () => {
    overview.mockResolvedValue({
      generated_at: '2026-09-08T00:00:00Z',
      counts: { total: 1, queued: 0, running: 0, succeeded: 0, failed: 1, cancelled: 0 },
      items: [{
        id: 'consistency:1', kind: 'consistency', title: '一致性扫描', project_id: 'p1', project_title: '春日记',
        chapter_id: 'c1', chapter_index: 1, chapter_title: '第一章', state: 'failed', source_status: 'failed',
        progress: 67, progress_label: '抽取 succeeded · 摘要 failed · 扫描 succeeded',
        created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:01:00Z', started_at: null,
        finished_at: null, error_code: 'provider_error', error_detail: '上游暂不可用', open_path: '/projects/p1/guard',
        retry: { method: 'POST', path: '/consistency/scan', body: { chapter_id: 'c1', body_rev: 1 }, label: '重新扫描' }
      }], limit: 100, has_more: false
    })
    trigger.mockResolvedValue(undefined)
    const wrapper = mount(TasksView, { attachTo: document.body })
    await flushPromises()
    expect(wrapper.text()).toContain('春日记')
    expect(wrapper.text()).toContain('需要处理')
    await wrapper.get('.task-actions .text-button').trigger('click')
    await flushPromises()
    expect(trigger).toHaveBeenCalledWith(expect.objectContaining({ path: '/consistency/scan' }))
    wrapper.unmount()
  })

  it('shows an empty state when no activity is persisted', async () => {
    overview.mockResolvedValue({
      generated_at: '2026-09-08T00:00:00Z',
      counts: { total: 0, queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 },
      items: [], limit: 100, has_more: false
    })
    const wrapper = mount(TasksView)
    await flushPromises()
    expect(wrapper.text()).toContain('还没有任务记录')
    wrapper.unmount()
  })
})
