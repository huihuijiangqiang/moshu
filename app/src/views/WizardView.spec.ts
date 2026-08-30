import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import WizardView from './WizardView.vue'

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((button) => button.text().trim() === text)
}

const storage = new Map<string, string>()
vi.stubGlobal('localStorage', {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
  removeItem: (key: string) => storage.delete(key),
  clear: () => storage.clear()
})

describe('new project wizard', () => {
  beforeEach(() => storage.clear())
  afterAll(() => vi.unstubAllGlobals())

  it('requires inspiration, genre, and template before building the editable skeleton', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/new', component: WizardView },
        { path: '/projects/p1/outline', component: { template: '<div />' } }
      ]
    })
    await router.push('/projects/new')
    await router.isReady()

    const wrapper = mount(WizardView, { global: { plugins: [router] } })
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeDefined()

    await wrapper.get('textarea[placeholder]').setValue('一个守关将军发现自己的佩剑一直在替师父撒谎。')
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeUndefined()
    await buttonByText(wrapper, '继续')?.trigger('click')

    expect(wrapper.text()).toContain('选择题材和故事推进方式')
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeDefined()

    await wrapper.get('input[value="fantasy"]').setValue(true)
    await wrapper.get('input[value="mystery"]').setValue(true)
    await nextTick()
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeUndefined()
    await buttonByText(wrapper, '继续')?.trigger('click')

    expect(wrapper.text()).toContain('这是根据你的选择搭出的骨架')
    expect(wrapper.get('input[type="text"]').element).toHaveProperty('value', '残锋照雪')
    expect(wrapper.get('textarea[aria-label="主角设定"]').element).toHaveProperty('value')
  })
})
