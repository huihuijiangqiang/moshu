import { request, USE_MOCK, delay } from './http'

export interface BillingProduct {
  id: string
  code: string
  name: string
  description: string | null
  plan: string | null
  currency: 'CNY'
  amount_minor: number
  credits: number
  billing_interval: 'one_time' | 'month' | 'year'
  is_active: boolean
  sort_order: number
}

export interface BillingOrder {
  id: string
  product_code: string | null
  product_name: string | null
  provider: 'wechat' | 'alipay'
  status: 'pending' | 'paid' | 'cancelled' | 'failed' | 'refunded' | 'partially_refunded'
  amount_minor: number
  currency: string
  credits: number
  provider_order_id: string | null
  paid_at: string | null
  created_at: string | null
  failure_code: string | null
}

export interface BillingStatus {
  currency: 'CNY'
  providers: Record<'wechat' | 'alipay', { configured: boolean; state: string }>
}

const realBillingApi = {
  products: () => request<BillingProduct[]>('/billing/products'),
  status: () => request<BillingStatus>('/billing/status'),
  orders: () => request<BillingOrder[]>('/billing/orders'),
  createOrder: (payload: { product_code: string; provider: 'wechat' | 'alipay'; idempotency_key: string }) =>
    request<BillingOrder & { payment: { provider: string; checkout_url: string | null; state: string } }>('/billing/orders', {
      method: 'POST', body: JSON.stringify(payload)
    })
}

const mockBillingApi = {
  async products(): Promise<BillingProduct[]> { await delay(80); return [] },
  async status(): Promise<BillingStatus> {
    await delay(80)
    return {
      currency: 'CNY',
      providers: {
        wechat: { configured: false, state: 'credentials_required' },
        alipay: { configured: false, state: 'credentials_required' }
      }
    }
  },
  async orders(): Promise<BillingOrder[]> { await delay(80); return [] },
  async createOrder(): Promise<never> { await delay(80); throw new Error('支付渠道尚未配置') }
}

export const billingApi = USE_MOCK ? mockBillingApi : realBillingApi
