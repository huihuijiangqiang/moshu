import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StoryboardView from './StoryboardView.vue'
import { useStoryboardStore } from '@/stores/storyboard'
import { mockApi } from '@/api/mock'

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

  it('shows actionable readiness issues and requires a fresh check after editing', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/storyboard', component: StoryboardView }]
    })
    await router.push('/projects/p1/storyboard')
    await router.isReady()
    const wrapper = mount(StoryboardView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await vi.waitFor(() => expect(wrapper.find('.storyboard-shot-editor').exists()).toBe(true))

    await wrapper.get('.storyboard-production-actions button:first-child').trigger('click')
    await vi.waitFor(() => expect(wrapper.get('.storyboard-production-result').text()).toContain('镜头'))
    expect(wrapper.get('.storyboard-production-actions button:last-child').attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('.storyboard-production-issues li').length).toBeGreaterThan(0)

    await wrapper.get('.storyboard-editor-grid textarea[placeholder^="人物、环境"]').setValue('新的画面锚点')
    await vi.waitFor(() => expect(wrapper.find('.storyboard-production-result').exists()).toBe(false))
    expect(wrapper.get('.storyboard-production-actions button:last-child').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('reorders scenes from the outline and shots from the filmstrip', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useStoryboardStore()
    await store.load('storyboard-ui-order')
    const firstSceneId = store.selectedScene!.id
    await store.createScene('storyboard-ui-order', {
      purpose: '第二场', summary: '', timeAnchor: '', locationEntryId: undefined, characterEntryIds: []
    })
    const secondSceneId = store.selectedScene!.id
    store.selectScene(firstSceneId)
    await store.createShot('storyboard-ui-order')
    const lastShotId = store.selectedShot!.id
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/storyboard', component: StoryboardView }]
    })
    await router.push('/projects/storyboard-ui-order/storyboard')
    await router.isReady()
    const wrapper = mount(StoryboardView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await flushPromises()

    await wrapper.get(`[aria-label="上移场 2"]`).trigger('click')
    await vi.waitFor(() => expect(store.selectedEpisode?.scenes[0]?.id).toBe(secondSceneId))
    expect(wrapper.findAll('.storyboard-scene-row')[0]?.text()).toContain('第二场')
    await wrapper.findAll('.storyboard-shot-order button')[store.selectedScene!.shots.length * 2 - 2]!.trigger('click')
    await vi.waitFor(() => expect(store.selectedScene?.shots.at(-2)?.id).toBe(lastShotId))
    wrapper.unmount()
  })

  it('opens a full image review before approving or rejecting a frame', async () => {
    vi.spyOn(mockApi, 'getStoryboardAssetPreview').mockResolvedValue('data:image/png;base64,dGVzdA==')
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useStoryboardStore()
    await store.load('storyboard-review-ui')
    const shotId = store.selectedShot!.id
    const asset = await store.uploadAsset('storyboard-review-ui', new File(['image'], 'frame.png', { type: 'image/png' }), shotId)
    expect(asset).toBeTruthy()
    asset!.contentUrl = '/assets/test/content'
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/storyboard', component: StoryboardView }]
    })
    await router.push('/projects/storyboard-review-ui/storyboard')
    await router.isReady()
    const wrapper = mount(StoryboardView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await vi.waitFor(() => expect(wrapper.find('.storyboard-asset-status').exists()).toBe(true))

    await wrapper.get('.storyboard-asset-status').trigger('click')
    expect(wrapper.get('[aria-labelledby="asset-review-title"]').text()).toContain('frame.png')
    await wrapper.get('.storyboard-asset-review textarea').setValue('人物面部需要更清晰')
    await wrapper.get('.storyboard-asset-review button:nth-last-child(2)').trigger('click')
    await vi.waitFor(() => expect(store.assets.find((item) => item.id === asset!.id)?.status).toBe('rejected'))
    expect(store.assets.find((item) => item.id === asset!.id)?.rejectionReason).toBe('人物面部需要更清晰')
    expect(store.selectedShot?.referenceAssetIds).not.toContain(asset!.id)

    asset!.contentUrl = '/assets/test/content'
    await wrapper.get('.storyboard-asset-status').trigger('click')
    await vi.waitFor(() => expect(store.assetPreviewUrls[asset!.id]).toBeTruthy())
    expect(wrapper.get('.storyboard-asset-review footer button:last-child').attributes('disabled')).toBeUndefined()
    await wrapper.get('.storyboard-asset-review footer button:last-child').trigger('click')
    await vi.waitFor(() => expect(store.assets.find((item) => item.id === asset!.id)?.status).toBe('approved'))
    expect(store.selectedShot?.referenceAssetIds).toContain(asset!.id)
    wrapper.unmount()
  })

  it('uploads an existing full-body sheet from the character profile panel', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useStoryboardStore()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/storyboard', component: StoryboardView }]
    })
    await router.push('/projects/p1/storyboard')
    await router.isReady()
    const wrapper = mount(StoryboardView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await vi.waitFor(() => expect(wrapper.find('.visual-profile').exists()).toBe(true))

    const input = wrapper.get('.visual-profile-sheet-actions input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [new File(['sheet'], 'hero-sheet.png', { type: 'image/png' })]
    })
    await input.trigger('change')
    await vi.waitFor(() => expect(wrapper.text()).toContain('全身设定图已上传，待确认'), { timeout: 3000 })
    expect(store.assets.some((asset) => asset.kind === 'character_sheet')).toBe(true)
    wrapper.unmount()
  })
})
