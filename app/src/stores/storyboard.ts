import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockApi } from '@/api/mock'
import { USE_MOCK } from '@/api/http'
import { storyboardApi } from '@/api/storyboard'
import type { ProductionPackage, StoryboardAdaptation, StoryboardAsset, StoryboardEpisode, StoryboardScene, StoryboardShot, VisualProfile } from '@/types'

export const useStoryboardStore = defineStore('storyboard', () => {
  const api = USE_MOCK ? mockApi : storyboardApi
  const adaptation = ref<StoryboardAdaptation | null>(null)
  const loadedProjectId = ref<string | null>(null)
  const selectedEpisodeId = ref<string | null>(null)
  const selectedSceneId = ref<string | null>(null)
  const selectedShotId = ref<string | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const checking = ref(false)
  const productionPackage = ref<ProductionPackage | null>(null)
  const assets = ref<StoryboardAsset[]>([])
  const assetPreviewUrls = ref<Record<string, string>>({})
  const error = ref('')
  let packageRevision = 0

  const selectedEpisode = computed(() => adaptation.value?.episodes.find((episode) => episode.id === selectedEpisodeId.value) ?? adaptation.value?.episodes[0] ?? null)
  const selectedScene = computed(() => selectedEpisode.value?.scenes.find((scene) => scene.id === selectedSceneId.value) ?? selectedEpisode.value?.scenes[0] ?? null)
  const selectedShot = computed(() => selectedScene.value?.shots.find((shot) => shot.id === selectedShotId.value) ?? selectedScene.value?.shots[0] ?? null)
  const totalShots = computed(() => adaptation.value?.episodes.reduce((total, episode) => total + episode.scenes.reduce((sceneTotal, scene) => sceneTotal + scene.shots.length, 0), 0) ?? 0)

  function selectDefaults() {
    selectedEpisodeId.value = selectedEpisodeId.value ?? adaptation.value?.episodes[0]?.id ?? null
    selectedSceneId.value = selectedSceneId.value ?? selectedEpisode.value?.scenes[0]?.id ?? null
    selectedShotId.value = selectedShotId.value ?? selectedScene.value?.shots[0]?.id ?? null
  }

  function invalidateProductionPackage() {
    packageRevision += 1
    productionPackage.value = null
  }

  function clearAssetPreviews() {
    Object.values(assetPreviewUrls.value).forEach((url) => URL.revokeObjectURL(url))
    assetPreviewUrls.value = {}
  }

  async function load(projectId: string) {
    if (loadedProjectId.value === projectId && adaptation.value) return
    loading.value = true
    error.value = ''
    try {
      const nextAdaptation = await api.getStoryboard(projectId)
      const nextAssets = await api.listStoryboardAssets(projectId, nextAdaptation.id)
      clearAssetPreviews()
      adaptation.value = nextAdaptation
      assets.value = nextAssets
      invalidateProductionPackage()
      loadedProjectId.value = projectId
      selectedEpisodeId.value = null
      selectedSceneId.value = null
      selectedShotId.value = null
      selectDefaults()
    } catch (caught) {
      adaptation.value = null
      error.value = caught instanceof Error && caught.message ? caught.message : '漫剧工作台加载失败'
    } finally {
      loading.value = false
    }
  }

  async function mutate<T>(action: () => Promise<T>, fallback: string): Promise<T | null> {
    if (saving.value) return null
    invalidateProductionPackage()
    saving.value = true
    error.value = ''
    try {
      return await action()
    } catch (caught) {
      error.value = caught instanceof Error && caught.message ? caught.message : fallback
      return null
    } finally {
      saving.value = false
    }
  }

  function clearError() {
    error.value = ''
  }

  async function checkProductionPackage(projectId: string) {
    const episodeId = selectedEpisode.value?.id
    if (!episodeId || loading.value || checking.value || saving.value) return null
    const revision = ++packageRevision
    checking.value = true
    error.value = ''
    try {
      const result = await api.getProductionPackage(projectId, episodeId)
      if (revision === packageRevision && selectedEpisode.value?.id === episodeId) {
        productionPackage.value = result
      }
      return result
    } catch (caught) {
      if (revision === packageRevision) error.value = caught instanceof Error && caught.message ? caught.message : '制作检查失败'
      return null
    } finally {
      checking.value = false
    }
  }

  async function uploadAsset(projectId: string, file: File, shotId?: string) {
    if (!adaptation.value) return null
    const asset = await mutate(
      () => api.uploadStoryboardAsset(projectId, adaptation.value!.id, file, shotId, selectedEpisode.value?.id),
      '画面素材上传失败'
    )
    if (!asset) return null
    assets.value.push(asset)
    const shot = adaptation.value?.episodes.flatMap((episode) => episode.scenes).flatMap((scene) => scene.shots).find((item) => item.id === shotId)
    if (shot && !shot.referenceAssetIds.includes(asset.id)) shot.referenceAssetIds.push(asset.id)
    return asset
  }

  async function approveAsset(projectId: string, assetId: string, status: StoryboardAsset['status']) {
    const updated = await mutate(() => api.updateStoryboardAsset(projectId, assetId, status), '画面素材状态保存失败')
    if (updated) {
      const asset = assets.value.find((item) => item.id === assetId)
      if (asset) Object.assign(asset, updated)
    }
    return updated
  }

  async function loadAssetPreview(projectId: string, asset: StoryboardAsset) {
    if (assetPreviewUrls.value[asset.id] || !asset.contentUrl) return assetPreviewUrls.value[asset.id] ?? ''
    try {
      const url = await api.getStoryboardAssetPreview(projectId, asset)
      if (url) assetPreviewUrls.value = { ...assetPreviewUrls.value, [asset.id]: url }
      return url
    } catch {
      return ''
    }
  }

  async function createEpisode(projectId: string, input: Pick<StoryboardEpisode, 'title' | 'sourceChapterIds' | 'targetDuration'>) {
    const episode = await mutate(() => api.createStoryboardEpisode(projectId, input), '漫剧集创建失败')
    if (!episode) return null
    adaptation.value?.episodes.push(episode)
    selectedEpisodeId.value = episode.id
    selectedSceneId.value = null
    selectedShotId.value = null
    return episode
  }

  async function updateEpisode(projectId: string, patch: Partial<StoryboardEpisode>) {
    const current = selectedEpisode.value
    if (!current) return null
    const updated = await mutate(() => api.updateStoryboardEpisode(projectId, current.id, patch), '漫剧集保存失败')
    if (updated) Object.assign(current, updated)
    return updated
  }

  async function createScene(projectId: string, input?: Pick<StoryboardScene, 'purpose' | 'summary' | 'timeAnchor' | 'locationEntryId' | 'characterEntryIds'>) {
    if (!selectedEpisode.value) return
    const scene = await mutate(() => api.createStoryboardScene(projectId, selectedEpisode.value!.id, input ?? { purpose: '填写这一场要完成的叙事任务', summary: '', timeAnchor: '', locationEntryId: undefined, characterEntryIds: [] }), '场景创建失败')
    if (!scene) return null
    selectedEpisode.value.scenes.push(scene)
    selectedSceneId.value = scene.id
    selectedShotId.value = null
    return scene
  }

  async function updateScene(projectId: string, patch: Partial<StoryboardScene>) {
    const current = selectedScene.value
    if (!current) return null
    const updated = await mutate(() => api.updateStoryboardScene(projectId, current.id, patch), '场景保存失败')
    if (updated) Object.assign(current, updated)
    return updated
  }

  async function moveScene(projectId: string, sceneId: string, direction: -1 | 1) {
    const episode = selectedEpisode.value
    if (!episode) return null
    const ids = episode.scenes.map((scene) => scene.id)
    const index = ids.indexOf(sceneId)
    const next = index + direction
    if (index < 0 || next < 0 || next >= ids.length) return null
    const currentId = ids[index]!
    ids[index] = ids[next]!
    ids[next] = currentId
    const result = await mutate(() => api.reorderStoryboardScenes(projectId, episode.id, ids), '场景顺序保存失败')
    if (!result) return null
    const byId = new Map(episode.scenes.map((scene) => [scene.id, scene]))
    episode.scenes = result.map((row) => Object.assign(byId.get(row.id)!, { order: row.order }))
    return result
  }

  async function createShot(projectId: string) {
    if (!selectedScene.value) return
    const shot = await mutate(() => api.createStoryboardShot(projectId, selectedScene.value!.id, {}), '镜头创建失败')
    if (!shot) return null
    selectedScene.value.shots.push(shot)
    selectedShotId.value = shot.id
    return shot
  }

  async function updateShot(projectId: string, patch: Partial<StoryboardShot>) {
    if (!selectedShot.value) return
    const current = selectedShot.value
    const updated = await mutate(() => api.updateStoryboardShot(projectId, current.id, patch), '镜头保存失败')
    if (updated) Object.assign(current, updated)
    return updated
  }

  async function moveShot(projectId: string, shotId: string, direction: -1 | 1) {
    const scene = selectedScene.value
    if (!scene) return null
    const ids = scene.shots.map((shot) => shot.id)
    const index = ids.indexOf(shotId)
    const next = index + direction
    if (index < 0 || next < 0 || next >= ids.length) return null
    const currentId = ids[index]!
    ids[index] = ids[next]!
    ids[next] = currentId
    const result = await mutate(() => api.reorderStoryboardShots(projectId, scene.id, ids), '镜头顺序保存失败')
    if (!result) return null
    const byId = new Map(scene.shots.map((shot) => [shot.id, shot]))
    scene.shots = result.map((row) => Object.assign(byId.get(row.id)!, { order: row.order }))
    return result
  }

  async function updateVisualProfile(projectId: string, profileId: string, patch: Partial<VisualProfile>) {
    const updated = await mutate(() => api.updateVisualProfile(projectId, profileId, patch), '视觉档案保存失败')
    if (!updated) return null
    const profile = adaptation.value?.visualProfiles.find((item) => item.id === profileId)
    if (profile) Object.assign(profile, updated)
    return updated
  }

  async function createVisualProfile(projectId: string, input: Pick<VisualProfile, 'codexEntryId' | 'displayName'> & Partial<VisualProfile>) {
    const profile = await mutate(() => api.createVisualProfile(projectId, input), '视觉档案创建失败')
    if (!profile) return null
    adaptation.value?.visualProfiles.push(profile)
    return profile
  }

  function selectEpisode(id: string) {
    invalidateProductionPackage()
    selectedEpisodeId.value = id
    selectedSceneId.value = adaptation.value?.episodes.find((episode) => episode.id === id)?.scenes[0]?.id ?? null
    selectedShotId.value = selectedScene.value?.shots[0]?.id ?? null
  }
  function selectScene(id: string) {
    selectedSceneId.value = id
    selectedShotId.value = selectedScene.value?.shots[0]?.id ?? null
  }

  return { adaptation, assets, assetPreviewUrls, loadedProjectId, loading, saving, checking, productionPackage, error, selectedEpisodeId, selectedSceneId, selectedShotId, selectedEpisode, selectedScene, selectedShot, totalShots, load, clearError, clearAssetPreviews, checkProductionPackage, uploadAsset, approveAsset, loadAssetPreview, createEpisode, updateEpisode, createScene, updateScene, moveScene, createShot, updateShot, moveShot, createVisualProfile, updateVisualProfile, selectEpisode, selectScene }
})
