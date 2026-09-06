import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const production = {
  project_id: 'p1', org_id: 'org-1', can_manage: false, current_user_id: 'writer-1',
  assignments: [{
    id: 7, chapter_id: 'c1', chapter_title: '雨夜来客', chapter_index: 1,
    assigned_to: 'writer-1', assignee_name: '写手甲', assigned_by: 'owner-1', assigner_name: '主编',
    status: 'assigned' as const, notes: '完成开篇冲突', words: 1800, updated_at: '2026-09-07T00:00:00Z'
  }],
  members: [{
    user_id: 'writer-1', name: '写手甲', role: 'writer' as const,
    assigned_count: 1, claimed_count: 0, completed_count: 0, returned_count: 0,
    active_words: 1800, completed_words: 0
  }]
}

const { api } = vi.hoisted(() => ({
  api: {
    list: vi.fn(), members: vi.fn(), projectPermissions: vi.fn(), production: vi.fn(),
    create: vi.fn(), addMember: vi.fn(), updateMember: vi.fn(), removeMember: vi.fn(),
    attachProject: vi.fn(), assignChapter: vi.fn(), updateAssignment: vi.fn(), removeAssignment: vi.fn()
  }
}))

vi.mock('@/api/orgs', () => ({ orgApi: api }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { projectId: 'p1' } }) }))
vi.mock('@/stores/project', () => ({
  useProjectStore: () => ({
    project: { id: 'p1', title: '春溪记', orgId: 'org-1' },
    chapters: [{ id: 'c1', index: 1, title: '雨夜来客', words: 1800 }],
    load: vi.fn().mockResolvedValue(undefined)
  })
}))
vi.mock('@/stores/shell', () => ({ useShellStore: () => ({ setCrumb: vi.fn() }) }))

import AccessView from './AccessView.vue'

describe('AccessView production workflow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.list.mockResolvedValue([{ id: 'org-1', name: '春溪工作室', plan: 'studio', seats: 5, seats_used: 2, role: 'writer' }])
    api.members.mockResolvedValue([{ user_id: 'writer-1', name: '写手甲', email: 'writer@example.test', role: 'writer' }])
    api.projectPermissions.mockResolvedValue(['view', 'edit_body'])
    api.production.mockResolvedValue(production)
    api.updateAssignment.mockResolvedValue({ ...production.assignments[0], status: 'claimed' })
  })

  it('shows the assigned chapter and lets its writer claim it', async () => {
    const wrapper = mount(AccessView)
    await flushPromises()

    expect(wrapper.text()).toContain('章节任务')
    expect(wrapper.text()).toContain('雨夜来客')
    expect(wrapper.text()).toContain('完成开篇冲突')

    const claim = wrapper.findAll('button').find((button) => button.text() === '领取')
    expect(claim).toBeTruthy()
    await claim!.trigger('click')
    await flushPromises()

    expect(api.updateAssignment).toHaveBeenCalledWith('org-1', 'p1', 7, 'claimed')
  })
})
