import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StoryboardView from './StoryboardView.vue'

describe('storyboard static editing workflow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('creates an episode from novel chapters, binds a scene and creates its character profile', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/storyboard', component: StoryboardView }]
    })
    await router.push('/projects/p1/storyboard')
    await router.isReady()
    const wrapper = mount(StoryboardView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] }
    })
    await vi.waitFor(() => {
      expect(wrapper.findAll('.storyboard-episode').length).toBeGreaterThan(0)
      expect(wrapper.findAll('.storyboard-episode').length).toBeGreaterThan(0)
    }, { timeout: 3000 })

    await wrapper.get('button[aria-label="新建集"]').trigger('click')
    const episodeDialog = wrapper.get('[aria-labelledby="episode-create-title"]')
    await vi.waitFor(() => expect(episodeDialog.findAll('.episode-chapters label').length).toBeGreaterThan(0), { timeout: 3000 })
    await episodeDialog.findAll('input')[0]!.setValue('第二集 · 玄铁令')
    await episodeDialog.findAll('.episode-chapters input')[0]!.setValue(true)
    await episodeDialog.trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('漫剧集已创建'), { timeout: 3000 })
    expect(wrapper.text()).toContain('第二集 · 玄铁令')

    const addScene = wrapper.findAll('.storyboard-add-scene').find((button) => button.isVisible())
    if (!addScene) throw new Error('add scene button not found')
    await addScene.trigger('click')
    const sceneForm = wrapper.get('.storyboard-scene-editor')
    await sceneForm.get('input[placeholder="这一场必须完成什么"]').setValue('老周头揭示玄铁令来历')
    const location = sceneForm.get('select')
    const locationValue = location.findAll('option').find((option) => option.text().includes('雁回关'))?.attributes('value')
    if (!locationValue) throw new Error('location option not found')
    await location.setValue(locationValue)
    const oldZhou = sceneForm.findAll('.scene-character-option').find((label) => label.text().includes('老周头'))
    if (!oldZhou) throw new Error('character option not found')
    await oldZhou.get('input').setValue(true)
    await sceneForm.trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('场景已创建'), { timeout: 3000 })
    expect(wrapper.get('.storyboard-binding').text()).toContain('雁回关')
    expect(wrapper.get('.storyboard-binding').text()).toContain('老周头')

    await wrapper.get('.inspector-empty button').trigger('click')
    const profileDialog = wrapper.get('[aria-labelledby="profile-create-title"]')
    await profileDialog.get('input[placeholder="半厚涂 / 赛璐璐 / 写实"]').setValue('水墨半厚涂')
    await profileDialog.trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('人物视觉档案已创建'), { timeout: 3000 })
    expect(wrapper.get('.visual-profile').text()).toContain('老周头')
    expect((wrapper.get('.visual-profile input').element as HTMLInputElement).value).toBe('水墨半厚涂')
    wrapper.unmount()
    await flushPromises()
  })
})
