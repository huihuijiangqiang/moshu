import { USE_MOCK, delay, request } from './http'

export interface CreditRates {
  basic_input: number
  basic_output: number
  advanced_input: number
  advanced_output: number
  cached_percent: number
}

export interface UsageBreakdown {
  feature: string
  label: string
  count: number
  user_key_count: number
  credits: number
  prompt_tokens: number
  completion_tokens: number
}

export interface UsageEvent {
  id: number
  feature: string
  label: string
  model: string | null
  credits: number
  prompt_tokens: number
  cached_tokens: number
  completion_tokens: number
  billing_mode: 'platform' | 'user_key'
  timestamp: string
}

export interface UsageSummary {
  plan: string
  plan_label: string
  remaining: number
  quota: number
  purchased_remaining: number
  available: number
  spent: number
  period_start: string
  resets_at: string | null
  rates: CreditRates
  items: UsageBreakdown[]
  daily: Array<{ date: string; credits: number }>
  recent: UsageEvent[]
}

const realUsageApi = {
  summary: () => request<UsageSummary>('/usage/summary')
}

const mockUsageApi = {
  async summary(): Promise<UsageSummary> {
    await delay(120)
    return {
      plan: 'author', plan_label: '作者版', remaining: 4980, quota: 5000, spent: 20,
      period_start: new Date().toISOString(), resets_at: null, purchased_remaining: 0, available: 4980,
      rates: { basic_input: 1, basic_output: 2, advanced_input: 4, advanced_output: 8, cached_percent: 20 },
      items: [], daily: [], recent: []
    }
  }
}

export const usageApi = USE_MOCK ? mockUsageApi : realUsageApi
