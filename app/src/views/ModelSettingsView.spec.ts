import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { modelConfigApi, type UserModelConfig } from '@/api/model-config'
import ModelSettingsView from './ModelSettingsView.vue'

vi.mock('@/api/model-config', () => ({
  modelConfigApi: {
    get: vi.fn(),
    save: vi.fn(),
    test: vi.fn(),
    remove: vi.fn()
  }
}))

const empty: UserModelConfig = {
  configured: false,
  providerName: '',
  baseUrl: '',
  model: '',
  contextWindowTokens: 32768,
  maxOutputTokens: 4096,
  contextSafetyMarginTokens: 2048,
  keyHint: null,
  enabled: false,
  revision: 0,
  lastTestStatus: 'untested',
  lastTestedAt: null,
  lastErrorCode: null
}

const saved: UserModelConfig = {
  configured: true,
  providerName: '作者中转站',
  baseUrl: 'https://gateway.example.com/v1',
  model: 'novel-model',
  contextWindowTokens: 256000,
  maxOutputTokens: 32000,
  contextSafetyMarginTokens: 16000,
  keyHint: 'sk-****cret',
  enabled: true,
  revision: 1,
  lastTestStatus: 'untested',
  lastTestedAt: null,
  lastErrorCode: null
}

describe('user model settings', () => {
  beforeEach(() => {
    vi.mocked(modelConfigApi.get).mockReset().mockResolvedValue(empty)
    vi.mocked(modelConfigApi.save).mockReset().mockResolvedValue(saved)
    vi.mocked(modelConfigApi.test).mockReset().mockResolvedValue({
      ...saved, lastTestStatus: 'ok', lastTestedAt: '2026-09-07T00:00:00Z'
    })
    vi.mocked(modelConfigApi.remove).mockReset().mockResolvedValue(undefined)
  })

  it('saves, masks, tests, and removes an author-owned model route', async () => {
    const wrapper = mount(ModelSettingsView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    const inputs = wrapper.findAll('.model-form input')
    await inputs[0]!.setValue('作者中转站')
    await inputs[1]!.setValue('https://gateway.example.com/v1')
    await inputs[2]!.setValue('novel-model')
    await inputs[6]!.setValue('sk-personal-secret')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(modelConfigApi.save).toHaveBeenCalledWith({
      providerName: '作者中转站',
      baseUrl: 'https://gateway.example.com/v1',
      model: 'novel-model',
      contextWindowTokens: 32768,
      maxOutputTokens: 4096,
      contextSafetyMarginTokens: 2048,
      apiKey: 'sk-personal-secret',
      enabled: true,
      revision: 0
    })
    expect((wrapper.findAll('.model-form input')[6]!.element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('sk-****cret')
    expect(wrapper.text()).not.toContain('sk-personal-secret')

    const testButton = wrapper.findAll('button').find(button => button.text() === '测试连接')
    if (!testButton) throw new Error('test connection button not found')
    await testButton.trigger('click')
    await flushPromises()
    expect(modelConfigApi.test).toHaveBeenCalledWith(1)
    expect(wrapper.text()).toContain('连接正常')

    const deleteButton = wrapper.findAll('.danger-zone button')[0]!
    await deleteButton.trigger('click')
    expect(deleteButton.text()).toContain('确认删除')
    vi.mocked(modelConfigApi.get).mockResolvedValueOnce(empty)
    await deleteButton.trigger('click')
    await flushPromises()
    expect(modelConfigApi.remove).toHaveBeenCalledWith(1)
    expect(wrapper.text()).toContain('生成已切回平台模型')
    wrapper.unmount()
  })
})
