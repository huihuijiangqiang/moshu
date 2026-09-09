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

  it('requires a detailed genre and template before building a tagged story skeleton', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/projects/new', component: WizardView },
        { path: '/projects/:projectId/write', component: { template: '<div />' } }
      ]
    })
    await router.push('/projects/new')
    await router.isReady()

    const wrapper = mount(WizardView, { global: { plugins: [router] } })
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeDefined()

    await wrapper.get('textarea[placeholder]').setValue('一个守关将军发现自己的佩剑一直在替师父撒谎。')
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeUndefined()
    await buttonByText(wrapper, '继续')?.trigger('click')

    expect(wrapper.text()).toContain('先定题材，再补充故事元素')
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeDefined()

    await buttonByText(wrapper, '历史军事')?.trigger('click')
    expect(wrapper.text()).toContain('架空历史')
    expect(wrapper.text()).not.toContain('东方玄幻')
    await wrapper.get('input[value="alternate-history"]').setValue(true)
    await wrapper.get('input[value="revenge"]').setValue(true)
    await wrapper.get('input[value="politics"]').setValue(true)
    await wrapper.get('input[value="war"]').setValue(true)
    expect(wrapper.get('input[type="checkbox"][value="growth"]').attributes('disabled')).toBeDefined()
    await wrapper.get('input[value="mystery"]').setValue(true)
    await nextTick()
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeUndefined()
    await buttonByText(wrapper, '继续')?.trigger('click')

    expect(wrapper.text()).toContain('这是根据你的选择搭出的骨架')
    expect((wrapper.get('input[type="text"]').element as HTMLInputElement).value).toContain('架空历史')
    expect(wrapper.get('textarea[aria-label="主角设定"]').element).toHaveProperty('value')
    expect(wrapper.get('textarea:not([aria-label])').element).toHaveProperty('value')
    expect(wrapper.text()).toContain('架空历史 · 谜团追索')

    await buttonByText(wrapper, '确认故事骨架')?.trigger('click')
    expect(wrapper.text()).toContain('内容标签')
    expect(wrapper.text()).toContain('复仇')
    expect(wrapper.text()).toContain('权谋')
    await buttonByText(wrapper, '创建作品并生成第一章')?.trigger('click')
    await vi.waitFor(
      () => expect(router.currentRoute.value.path).toMatch(/^\/projects\/draft-[a-z0-9]+\/write$/),
      { timeout: 3000 }
    )
    expect(router.currentRoute.value.path).toMatch(/^\/projects\/draft-[a-z0-9]+\/write$/)
    expect(router.currentRoute.value.query.autoGenerate).toBe('1')
    expect(router.currentRoute.value.query.chapter).toMatch(/-ch1$/)
  })

  it('accepts and restores a custom genre', async () => {
    storage.set('moshu:new-project-draft', JSON.stringify({
      step: 2,
      highestStep: 2,
      inspiration: '一座城市每天醒来都会随机交换所有人的职业。',
      audience: 'general',
      genreGroupId: 'custom',
      genreId: '',
      customGenre: '近未来职业寓言',
      tagIds: ['ensemble'],
      templateId: 'reversal'
    }))

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/new', component: WizardView }]
    })
    await router.push('/projects/new')
    await router.isReady()

    const wrapper = mount(WizardView, { global: { plugins: [router] } })
    await nextTick()

    expect(wrapper.get('input[placeholder="例如：民国工业探险"]').element).toHaveProperty('value', '近未来职业寓言')
    expect(wrapper.get('input[value="ensemble"]').element).toHaveProperty('checked', true)
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeUndefined()
  })
})
