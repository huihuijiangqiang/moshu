import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import WizardView from './WizardView.vue'
import * as wizardApi from '@/api/wizard'

const selectedDraft = {
  step: 2,
  highestStep: 2,
  inspiration: '她穿越成为边城小吏，靠粮道破局，最终登基为女皇。',
  audience: 'female',
  genreGroupId: 'history',
  genreId: 'court-strategy',
  templateId: 'ensemble'
}

function storyPlan() {
  return wizardApi.buildMockPlan({
    inspiration: selectedDraft.inspiration,
    audience: '女频', genre: '古代权谋', tags: [], template: '群像经营'
  })
}

async function mountWizard(draft: Record<string, unknown> = selectedDraft) {
  storage.set('moshu:new-project-draft', JSON.stringify(draft))
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/new', component: WizardView }]
  })
  await router.push('/projects/new')
  await router.isReady()
  const wrapper = mount(WizardView, { global: { plugins: [router] } })
  await nextTick()
  return wrapper
}

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
  afterEach(() => vi.restoreAllMocks())
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
    await flushPromises()

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

  it('opens the settings step from the step navigation before planning finishes', async () => {
    let resolvePlan!: (plan: wizardApi.WizardPlan) => void
    const planning = vi.spyOn(wizardApi, 'planWizard').mockImplementationOnce(
      () => new Promise((resolve) => { resolvePlan = resolve })
    )
    const wrapper = await mountWizard()
    const scrollPane = wrapper.get('.wizard-main').element
    scrollPane.scrollTop = 420
    const settingsStep = wrapper.get('.wizard-steps button:nth-child(3)')
    expect(settingsStep.attributes('disabled')).toBeUndefined()
    await settingsStep.trigger('click')

    expect(wrapper.find('#skeleton-title').exists()).toBe(true)
    expect(scrollPane.scrollTop).toBe(0)
    expect(wrapper.get('[role="status"]').text()).toContain('正在根据你的选择生成')
    expect(buttonByText(wrapper, '正在生成…')?.attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '上一步')?.attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '重新生成骨架')?.attributes('disabled')).toBeDefined()
    expect(wrapper.get('.wizard-skeleton-fields').attributes('disabled')).toBeDefined()
    expect(planning).toHaveBeenCalledTimes(1)

    resolvePlan(storyPlan())
    await flushPromises()
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.get('textarea[aria-label="主角设定"]').element).toHaveProperty('value', storyPlan().protagonist)
    expect(buttonByText(wrapper, '确认故事骨架')?.attributes('disabled')).toBeUndefined()
  })

  it('prefetches a stable story plan once and reuses it on the settings step', async () => {
    vi.useFakeTimers()
    try {
      const planning = vi.spyOn(wizardApi, 'planWizard').mockResolvedValue(storyPlan())
      const wrapper = await mountWizard()

      await vi.advanceTimersByTimeAsync(650)
      await flushPromises()
      expect(planning).toHaveBeenCalledTimes(1)

      await buttonByText(wrapper, '继续')?.trigger('click')
      await flushPromises()
      expect(planning).toHaveBeenCalledTimes(1)
      expect(wrapper.get('input[type="text"]').element).toHaveProperty('value', storyPlan().title)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows planning failures on the current step and lets the author retry', async () => {
    const planning = vi.spyOn(wizardApi, 'planWizard')
      .mockRejectedValueOnce(new Error('upstream unavailable'))
      .mockResolvedValueOnce(storyPlan())
    const wrapper = await mountWizard()
    await buttonByText(wrapper, '继续')?.trigger('click')
    await flushPromises()

    expect(wrapper.find('#skeleton-title').exists()).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('故事骨架生成失败')
    expect(buttonByText(wrapper, '确认故事骨架')?.attributes('disabled')).toBeDefined()
    await buttonByText(wrapper, '重试生成')?.trigger('click')
    await flushPromises()

    expect(planning).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    await buttonByText(wrapper, '确认故事骨架')?.trigger('click')
    expect(wrapper.get('#review-title').text()).toBe(storyPlan().title)
  })

  it.each(['current', 'legacy'])('restores %s chapter drafts and volume assignments without regenerating', async (format) => {
    const plan = storyPlan()
    const planning = vi.spyOn(wizardApi, 'planWizard')
    const wrapper = await mountWizard({
      ...selectedDraft, step: 3, highestStep: 3,
      bookTitle: plan.title, protagonist: plan.protagonist, coreHook: plan.coreHook,
      synopsis: plan.synopsis, volumes: plan.volumes,
      chapters: plan.chapters.map((chapter) => ({
        ...chapter, outline: format === 'current' ? chapter.outline.join('\n') : chapter.outline
      }))
    })
    expect(wrapper.findAll('.wizard-chapter-plan')).toHaveLength(3)
    expect(wrapper.get('textarea[aria-label="第 3 章章纲"]').element)
      .toHaveProperty('value', plan.chapters[2]!.outline.join('\n'))
    expect(wrapper.get('textarea[placeholder^="前三章"]').element)
      .toHaveProperty('value', plan.chapters[0]!.outline.at(-1))
    expect(wrapper.get('textarea[placeholder^="中后期"]').element)
      .toHaveProperty('value', plan.volumes.map((volume) => volume.summary).join('；'))
    expect(buttonByText(wrapper, '确认故事骨架')?.attributes('disabled')).toBeUndefined()
    await buttonByText(wrapper, '上一步')?.trigger('click')
    await wrapper.get('.wizard-steps button:nth-child(3)').trigger('click')
    expect(planning).not.toHaveBeenCalled()
    const saved = JSON.parse(storage.get('moshu:new-project-draft')!)
    expect(saved.chapters[2].volumeIndex).toBe(1)
    expect(saved.chapters[2].outline).toBe(plan.chapters[2]!.outline.join('\n'))
  })

  it('keeps an explicitly cleared payoff invalid when restoring a draft', async () => {
    const plan = storyPlan()
    const wrapper = await mountWizard({
      ...selectedDraft, step: 3, highestStep: 3,
      bookTitle: plan.title, protagonist: plan.protagonist, coreHook: plan.coreHook,
      synopsis: plan.synopsis, volumes: plan.volumes, chapters: plan.chapters,
      firstPayoff: ''
    })
    expect(buttonByText(wrapper, '确认故事骨架')?.attributes('disabled')).toBeDefined()
  })

  it('keeps a selected subgenre when its current genre group is clicked again', async () => {
    const wrapper = await mountWizard()
    await buttonByText(wrapper, '历史军事')?.trigger('click')
    expect(wrapper.get('input[value="court-strategy"]').element).toHaveProperty('checked', true)
    expect(wrapper.get('.wizard-steps button:nth-child(3)').attributes('disabled')).toBeUndefined()
  })

  it('requires an updated skeleton before returning to creation after changing the genre', async () => {
    const wrapper = await mountWizard()
    await buttonByText(wrapper, '继续')?.trigger('click')
    await flushPromises()
    await buttonByText(wrapper, '确认故事骨架')?.trigger('click')
    await wrapper.get('.wizard-steps button:nth-child(2)').trigger('click')
    await wrapper.get('input[value="military"]').setValue(true)

    expect(wrapper.get('.wizard-steps button:nth-child(4)').attributes('disabled')).toBeDefined()
    const planning = vi.spyOn(wizardApi, 'planWizard')
    await wrapper.get('.wizard-steps button:nth-child(3)').trigger('click')
    await flushPromises()
    expect(planning).toHaveBeenCalledWith(
      expect.objectContaining({ genre: '战争军事' }),
      expect.anything()
    )
    expect(wrapper.get('.wizard-steps button:nth-child(4)').attributes('disabled')).toBeUndefined()
  })

  it('does not unlock the settings step for a removed template in a saved draft', async () => {
    const wrapper = await mountWizard({ ...selectedDraft, templateId: 'removed-template' })
    expect(wrapper.get('.wizard-steps button:nth-child(3)').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '继续')?.attributes('disabled')).toBeDefined()
  })

  it('offers a visible retry after reloading an unfinished planning request', async () => {
    const wrapper = await mountWizard({ ...selectedDraft, step: 3, highestStep: 3 })
    expect(wrapper.get('[role="alert"]').text()).toContain('故事骨架尚未完成')
    expect(buttonByText(wrapper, '重试生成')).toBeDefined()
    expect(buttonByText(wrapper, '确认故事骨架')?.attributes('disabled')).toBeDefined()
  })
})
