import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { admin, billing } = vi.hoisted(() => ({
  admin: {
    overview: vi.fn(), users: vi.fn(), settings: vi.fn(), platformUsage: vi.fn(), billingProducts: vi.fn(),
    createBillingProduct: vi.fn(), updateBillingProduct: vi.fn(), updateUser: vi.fn(), updateSettings: vi.fn()
  },
  billing: { status: vi.fn() }
}))

vi.mock('@/api/admin', () => ({ adminApi: admin }))
vi.mock('@/api/billing', () => ({ billingApi: billing }))
vi.mock('@/api/session', () => ({ getSessionUser: () => ({ id: 'admin-1', name: '管理员', email: 'admin@example.test', plan: 'studio', system_role: 'admin' }) }))
vi.mock('@/stores/shell', () => ({ useShellStore: () => ({ setCrumb: vi.fn() }) }))

import AdminView from './AdminView.vue'

describe('AdminView loading recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    admin.overview.mockRejectedValueOnce(new Error('服务暂不可用'))
    admin.users.mockResolvedValue([])
    admin.settings.mockResolvedValue(null)
    admin.platformUsage.mockResolvedValue(null)
    admin.billingProducts.mockResolvedValue([])
    billing.status.mockResolvedValue({
      currency: 'CNY',
      providers: {
        wechat: { configured: false, state: 'credentials_required' },
        alipay: { configured: true, state: 'ready' }
      }
    })
  })

  it('shows a retry action after the initial management load fails', async () => {
    const wrapper = mount(AdminView)
    await flushPromises()

    expect(wrapper.find('.admin-load-error').text()).toContain('服务暂不可用')
    expect(wrapper.find('.admin-load-error button').text()).toBe('重试')

    admin.overview.mockResolvedValue({ users: 1, projects: 1, active_sessions: 0, active_consistency_runs: 0, open_guard_issues: 0 })
    admin.settings.mockResolvedValue({
      registration_enabled: true, default_plan: 'free', default_monthly_quota: 1000,
      credit_rates: { basic_input: 1, basic_output: 2, advanced_input: 3, advanced_output: 4, cached_percent: 20 },
      generation_model: 'model', consistency_model: 'model', embedding_model: 'embed',
      generation_gateway_configured: true, embedding_gateway_configured: true
    })
    admin.platformUsage.mockResolvedValue({
      period_days: 30, totals: { events: 0, requests: 0, prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0, estimated_events: 0 }, items: [], recent: []
    })
    await wrapper.find('.admin-load-error button').trigger('click')
    await flushPromises()

    expect(wrapper.find('.admin-load-error').exists()).toBe(false)
    wrapper.unmount()
  })
})
