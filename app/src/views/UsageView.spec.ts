import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BillingOrder } from '@/api/billing'
import type { UsageSummary } from '@/api/usage'

const { billing, usage, qr } = vi.hoisted(() => ({
  billing: {
    products: vi.fn(), status: vi.fn(), orders: vi.fn(), createOrder: vi.fn(),
    syncOrder: vi.fn(), closeOrder: vi.fn(), refundOrder: vi.fn()
  },
  usage: { summary: vi.fn() },
  qr: { toDataURL: vi.fn() }
}))

vi.mock('@/api/billing', () => ({ billingApi: billing }))
vi.mock('@/api/usage', () => ({ usageApi: usage }))
vi.mock('qrcode', () => ({ default: qr }))
vi.mock('@/stores/shell', () => ({ useShellStore: () => ({ setCrumb: vi.fn() }) }))

import UsageView from './UsageView.vue'

const summary: UsageSummary = {
  plan: 'author', plan_label: '作者版', remaining: 5000, quota: 5000,
  purchased_remaining: 0, available: 5000, spent: 0,
  period_start: '2026-09-01T00:00:00Z', resets_at: null,
  rates: { basic_input: 1, basic_output: 2, advanced_input: 4, advanced_output: 8, cached_percent: 20 },
  items: [], daily: [], recent: []
}

const pendingOrder: BillingOrder = {
  id: 'ord-1', product_code: 'starter', product_name: '入门积分包', provider: 'wechat',
  status: 'pending', amount_minor: 990, currency: 'CNY', credits: 1000,
  provider_order_id: null, provider_refund_id: null, refunded_amount_minor: 0,
  paid_at: null, refunded_at: null, provider_synced_at: null, expires_at: null,
  created_at: '2026-09-08T00:00:00Z', failure_code: null
}

describe('usage payment workflow', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
    setActivePinia(createPinia())
    usage.summary.mockResolvedValue(summary)
    billing.products.mockResolvedValue([{
      id: 'prd-1', code: 'starter', name: '入门积分包', description: null, plan: null,
      currency: 'CNY', amount_minor: 990, credits: 1000, billing_interval: 'one_time',
      is_active: true, sort_order: 1
    }])
    billing.status.mockResolvedValue({
      currency: 'CNY',
      providers: {
        wechat: { configured: true, state: 'ready' },
        alipay: { configured: false, state: 'credentials_required' }
      }
    })
    billing.orders.mockResolvedValue([])
    billing.createOrder.mockResolvedValue({
      ...pendingOrder,
      payment: { provider: 'wechat', checkout_url: 'weixin://wxpay/example', state: 'checkout_ready' }
    })
    billing.syncOrder.mockResolvedValue({ ...pendingOrder, status: 'paid', provider_order_id: 'wx-1' })
    qr.toDataURL.mockResolvedValue('data:image/png;base64,qr')
  })

  it('creates a configured-channel order, renders its QR and synchronizes payment', async () => {
    const wrapper = mount(UsageView, { attachTo: document.body, global: { plugins: [createPinia()] } })
    await flushPromises()

    const wechat = wrapper.findAll('button').find((button) => button.text() === '微信支付')
    expect(wechat).toBeTruthy()
    await wechat!.trigger('click')
    await flushPromises()

    expect(billing.createOrder).toHaveBeenCalledWith(expect.objectContaining({
      product_code: 'starter', provider: 'wechat', idempotency_key: expect.any(String)
    }))
    expect(qr.toDataURL).toHaveBeenCalledWith('weixin://wxpay/example', expect.any(Object))
    expect(document.body.querySelector<HTMLImageElement>('.payment-dialog img')?.src).toContain('data:image/png')

    const sync = Array.from(document.body.querySelectorAll<HTMLButtonElement>('.payment-dialog button'))
      .find((button) => button.textContent === '我已完成支付')
    sync?.click()
    await flushPromises()
    expect(billing.syncOrder).toHaveBeenCalledWith('ord-1')
    expect(document.body.textContent).toContain('已到账')
    wrapper.unmount()
  })

  it('shows a retry action and keeps channel status visible when billing data partially fails', async () => {
    billing.products.mockRejectedValueOnce(new Error('商品服务不可用'))
    const wrapper = mount(UsageView, { attachTo: document.body, global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.find('.billing-load-error').text()).toContain('部分支付信息暂时无法读取')
    expect(wrapper.find('.billing-provider-list').text()).toContain('微信支付：已就绪')
    expect(wrapper.find('.billing-provider-list').text()).toContain('支付宝：待配置商户凭证')

    billing.products.mockResolvedValueOnce([{
      id: 'prd-1', code: 'starter', name: '入门积分包', description: null, plan: null,
      currency: 'CNY', amount_minor: 990, credits: 1000, billing_interval: 'one_time',
      is_active: true, sort_order: 1
    }])
    await wrapper.find('.billing-load-error button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.billing-load-error').exists()).toBe(false)
    wrapper.unmount()
  })

  it('polls a pending checkout and stops after payment arrives', async () => {
    vi.useFakeTimers()
    const wrapper = mount(UsageView, { attachTo: document.body, global: { plugins: [createPinia()] } })
    await flushPromises()

    const wechat = wrapper.findAll('button').find((button) => button.text() === '微信支付')
    await wechat!.trigger('click')
    await flushPromises()
    expect(billing.syncOrder).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()
    expect(billing.syncOrder).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).toContain('已到账')

    await vi.advanceTimersByTimeAsync(8000)
    expect(billing.syncOrder).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    vi.useRealTimers()
  })
})
