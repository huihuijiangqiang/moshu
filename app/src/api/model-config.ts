import { USE_MOCK, delay, request, requestResponse } from './http'

export type ModelTestStatus = 'untested' | 'ok' | 'failed'

export interface UserModelConfig {
  configured: boolean
  providerName: string
  baseUrl: string
  model: string
  keyHint: string | null
  enabled: boolean
  revision: number
  lastTestStatus: ModelTestStatus
  lastTestedAt: string | null
  lastErrorCode: string | null
}

export interface UserModelConfigWrite {
  providerName: string
  baseUrl: string
  model: string
  apiKey?: string
  enabled: boolean
  revision: number
}

const emptyConfig = (): UserModelConfig => ({
  configured: false,
  providerName: '',
  baseUrl: '',
  model: '',
  keyHint: null,
  enabled: false,
  revision: 0,
  lastTestStatus: 'untested',
  lastTestedAt: null,
  lastErrorCode: null
})

const realApi = {
  get: () => request<UserModelConfig>('/account/model-config'),
  save: (value: UserModelConfigWrite) => request<UserModelConfig>('/account/model-config', {
    method: 'PUT', body: JSON.stringify(value)
  }),
  test: (revision: number) => request<UserModelConfig>('/account/model-config/test', {
    method: 'POST', body: JSON.stringify({ revision })
  }),
  async remove(revision: number) {
    await requestResponse(`/account/model-config?revision=${revision}`, { method: 'DELETE' })
  }
}

let mockConfig = emptyConfig()
const mockApi = {
  async get() { await delay(80); return { ...mockConfig } },
  async save(value: UserModelConfigWrite) {
    await delay(100)
    mockConfig = {
      configured: true,
      providerName: value.providerName,
      baseUrl: value.baseUrl.replace(/\/$/, ''),
      model: value.model,
      keyHint: value.apiKey ? `${value.apiKey.slice(0, 3)}****${value.apiKey.slice(-4)}` : mockConfig.keyHint,
      enabled: value.enabled,
      revision: mockConfig.revision + 1,
      lastTestStatus: 'untested',
      lastTestedAt: null,
      lastErrorCode: null
    }
    return { ...mockConfig }
  },
  async test() {
    await delay(100)
    mockConfig = { ...mockConfig, lastTestStatus: 'ok', lastTestedAt: new Date().toISOString(), lastErrorCode: null }
    return { ...mockConfig }
  },
  async remove() { await delay(80); mockConfig = emptyConfig() }
}

export const modelConfigApi = USE_MOCK ? mockApi : realApi
