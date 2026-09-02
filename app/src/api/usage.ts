import { request } from './http'

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
  timestamp: string
}

export interface UsageSummary {
  plan: string
  plan_label: string
  remaining: number
  quota: number
  spent: number
  period_start: string
  resets_at: string | null
  rates: CreditRates
  items: UsageBreakdown[]
  daily: Array<{ date: string; credits: number }>
  recent: UsageEvent[]
}

export const usageApi = {
  summary: () => request<UsageSummary>('/usage/summary')
}
