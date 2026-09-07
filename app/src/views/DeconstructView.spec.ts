import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import DeconstructView from './DeconstructView.vue'

describe('reference deconstruction view', () => {
  it('shows an in-memory analysis and its copyright boundary', async () => {
    const wrapper = mount(DeconstructView, { global: { plugins: [createPinia()] } })
    const input = wrapper.get<HTMLInputElement>('input[type="file"]')
    const file = new File(['第一章 开始\n\n故事终于开始。'], 'reference.txt', { type: 'text/plain' })
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')

    expect(wrapper.text()).toContain('reference.txt')
    await wrapper.get('button[data-primary="true"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('节奏节点')
    expect(wrapper.text()).toContain('章纲结构')
    expect(wrapper.text()).toContain('版权')
    expect(wrapper.text()).toContain('原文只在本次请求内处理')
    wrapper.unmount()
  })
})
