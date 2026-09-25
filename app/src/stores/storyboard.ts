import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockApi } from '@/api/mock'
import { USE_MOCK } from '@/api/http'
import { storyboardApi } from '@/api/storyboard'
import type { ImageGenerationJob, ImageGenerationPreview, ProductionPackage, StoryboardAdaptation, StoryboardAsset, StoryboardEpisode, StoryboardScene, StoryboardShot, VisualProfile } from '@/types'

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
  const imagePreview = ref<ImageGenerationPreview | null>(null)
  const imageJobs = ref<ImageGenerationJob[]>([])
  const fullBodyPreview = ref<ImageGenerationPreview | null>(null)
  const fullBodyJobs = ref<ImageGenerationJob[]>([])
  const fullBodyLoading = ref(false)
  const fullBodyGenerating = ref(false)
  const generationLoading = ref(false)
  const generating = ref(false)
  const generationError = ref('')
  let pendingImageRequest: { shotId: string; hash: string; model: string; credits: number; id: string } | null = null
  const loadingImageJobs = new Set<string>()
  const loadingFullBodyJobs = new Set<string>()
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
      imagePreview.value = null
      imageJobs.value = []
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
    imagePreview.value = null
    pendingImageRequest = null
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
    generationError.value = ''
  }

  function imageErrorMessage(caught: unknown, fallback: string) {
    const raw = caught instanceof Error ? caught.message : ''
    try {
      const detail = JSON.parse(raw)?.detail
      if (detail?.code === 'image_preview_changed') return '镜头或人物档案已变更，请重新预览'
      if (detail?.code === 'insufficient_credits') return `积分不足：需要 ${detail.required}，当前剩余 ${detail.remaining}`
      if (detail?.code === 'image_generation_not_ready') return '请先补齐画面提示词和人物视觉档案'
      if (detail?.code === 'image_request_id_reused') return '任务请求冲突，请刷新页面后重试'
    } catch { /* Network errors are already readable messages. */ }
    return raw || fallback
  }

  async function loadImageJobs(shotId: string) {
    if (USE_MOCK || loadingImageJobs.has(shotId)) return
    loadingImageJobs.add(shotId)
    try {
      const rows = await storyboardApi.listImageGenerationJobs(shotId)
      if (selectedShotId.value !== shotId) return
      imageJobs.value = rows
      const completed = rows.some((job) => job.status === 'completed' && job.asset_id && !assets.value.some((asset) => asset.id === job.asset_id))
      if (completed && adaptation.value) {
        const nextAssets = await storyboardApi.listStoryboardAssets(adaptation.value.projectId, adaptation.value.id)
        if (selectedShotId.value !== shotId) return
        assets.value = nextAssets
        nextAssets.filter((asset) => asset.shotId === shotId).forEach((asset) => { void loadAssetPreview(adaptation.value!.projectId, asset) })
        invalidateProductionPackage()
      }
    } catch (caught) {
      generationError.value = imageErrorMessage(caught, '图片任务加载失败')
    } finally {
      loadingImageJobs.delete(shotId)
    }
  }

  async function prepareImageGeneration(shotId: string) {
    imagePreview.value = null
    generationError.value = ''
    if (USE_MOCK) {
      generationError.value = '图片生成需要连接真实服务'
      return null
    }
    generationLoading.value = true
    try {
      const preview = await storyboardApi.getImageGenerationPreview(shotId)
      if (pendingImageRequest && (pendingImageRequest.shotId !== shotId || pendingImageRequest.hash !== preview.prompt_sha256 || pendingImageRequest.model !== preview.model || pendingImageRequest.credits !== preview.credits)) pendingImageRequest = null
      if (selectedShotId.value === shotId) imagePreview.value = preview
      return preview
    } catch (caught) {
      generationError.value = imageErrorMessage(caught, '生成预览加载失败')
      return null
    } finally {
      generationLoading.value = false
    }
  }

  async function prepareFullBodyGeneration(profileId: string) {
    fullBodyPreview.value = null
    generationError.value = ''
    if (USE_MOCK) {
      generationError.value = '全身设定图需要连接真实服务'
      return null
    }
    fullBodyLoading.value = true
    try {
      const preview = await storyboardApi.getFullBodyImageGenerationPreview(profileId)
      if (adaptation.value?.visualProfiles.some((profile) => profile.id === profileId)) fullBodyPreview.value = preview
      return preview
    } catch (caught) {
      generationError.value = imageErrorMessage(caught, '全身设定图预览加载失败')
      return null
    } finally {
      fullBodyLoading.value = false
    }
  }

  async function confirmFullBodyGeneration(profileId: string) {
    const preview = fullBodyPreview.value
    if (!preview?.ready || fullBodyGenerating.value) return null
    fullBodyGenerating.value = true
    generationError.value = ''
    try {
      const requestId = `sheet_${crypto.randomUUID().replaceAll('-', '')}`
      const job = await storyboardApi.createFullBodyImageGenerationJob(profileId, requestId, preview)
      fullBodyJobs.value = [job, ...fullBodyJobs.value.filter((item) => item.id !== job.id)]
      fullBodyPreview.value = null
      return job
    } catch (caught) {
      generationError.value = imageErrorMessage(caught, '全身设定图任务创建失败')
      return null
    } finally {
      fullBodyGenerating.value = false
    }
  }

  async function loadFullBodyJobs(profileId: string) {
    if (USE_MOCK || loadingFullBodyJobs.has(profileId)) return
    loadingFullBodyJobs.add(profileId)
    try {
      const rows = await storyboardApi.listFullBodyImageGenerationJobs(profileId)
      fullBodyJobs.value = rows
      const completed = rows.some((job) => job.status === 'completed' && job.asset_id && !assets.value.some((asset) => asset.id === job.asset_id))
      if (completed && adaptation.value) {
        assets.value = await storyboardApi.listStoryboardAssets(adaptation.value.projectId, adaptation.value.id)
        assets.value.filter((asset) => asset.visualProfileId === profileId).forEach((asset) => { void loadAssetPreview(adaptation.value!.projectId, asset) })
        invalidateProductionPackage()
      }
    } catch (caught) {
      generationError.value = imageErrorMessage(caught, '全身设定图任务加载失败')
    } finally {
      loadingFullBodyJobs.delete(profileId)
    }
  }

  async function confirmImageGeneration(shotId: string) {
    const preview = imagePreview.value
    if (!preview?.ready || generating.value || selectedShotId.value !== shotId) return null
    if (!pendingImageRequest || pendingImageRequest.shotId !== shotId || pendingImageRequest.hash !== preview.prompt_sha256 || pendingImageRequest.model !== preview.model || pendingImageRequest.credits !== preview.credits) {
      pendingImageRequest = { shotId, hash: preview.prompt_sha256, model: preview.model, credits: preview.credits, id: crypto.randomUUID().replaceAll('-', '') }
    }
    generating.value = true
    generationError.value = ''
    try {
      const job = await storyboardApi.createImageGenerationJob(shotId, pendingImageRequest.id, preview)
      imageJobs.value = [job, ...imageJobs.value.filter((item) => item.id !== job.id)]
      imagePreview.value = null
      pendingImageRequest = null
      return job
    } catch (caught) {
      generationError.value = imageErrorMessage(caught, '生成任务创建失败')
      if (generationError.value === '镜头或人物档案已变更，请重新预览') {
        imagePreview.value = null
        pendingImageRequest = null
      }
      return null
    } finally {
      generating.value = false
    }
  }

  async function cancelImageGeneration(jobId: string) {
    if (generating.value) return null
    generating.value = true
    generationError.value = ''
    try {
      const job = await storyboardApi.cancelImageGenerationJob(jobId)
      imageJobs.value = imageJobs.value.map((item) => item.id === jobId ? job : item)
      return job
    } catch (caught) {
      generationError.value = imageErrorMessage(caught, '取消任务失败')
      return null
    } finally {
      generating.value = false
    }
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

  async function uploadCharacterSheet(projectId: string, file: File, profileId: string) {
    if (!adaptation.value) return null
    const asset = await mutate(
      () => api.uploadCharacterSheet(projectId, adaptation.value!.id, profileId, file),
      '人物全身图上传失败'
    )
    if (!asset) return null
    assets.value.push(asset)
    const profile = adaptation.value.visualProfiles.find((item) => item.id === profileId)
    if (profile && !profile.referenceAssetIds.includes(asset.id)) profile.referenceAssetIds.push(asset.id)
    return asset
  }

  async function approveAsset(projectId: string, assetId: string, status: StoryboardAsset['status'], rejectionReason?: string) {
    const updated = await mutate(() => api.updateStoryboardAsset(projectId, assetId, status, rejectionReason), '画面素材状态保存失败')
    if (updated) {
      const asset = assets.value.find((item) => item.id === assetId)
      if (asset) Object.assign(asset, updated)
      const shot = adaptation.value?.episodes.flatMap((episode) => episode.scenes).flatMap((scene) => scene.shots).find((item) => item.id === updated.shotId)
      if (shot && status === 'rejected') shot.referenceAssetIds = shot.referenceAssetIds.filter((id) => id !== assetId)
      if (shot && status === 'approved' && !shot.referenceAssetIds.includes(assetId)) shot.referenceAssetIds.push(assetId)
      const profile = adaptation.value?.visualProfiles.find((item) => item.id === updated.visualProfileId)
      if (profile && status === 'rejected') profile.referenceAssetIds = profile.referenceAssetIds.filter((id) => id !== assetId)
      if (profile && status === 'approved' && !profile.referenceAssetIds.includes(assetId)) profile.referenceAssetIds.push(assetId)
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

  return { adaptation, assets, assetPreviewUrls, imagePreview, imageJobs, fullBodyPreview, fullBodyJobs, fullBodyLoading, fullBodyGenerating, generationLoading, generating, generationError, loadedProjectId, loading, saving, checking, productionPackage, error, selectedEpisodeId, selectedSceneId, selectedShotId, selectedEpisode, selectedScene, selectedShot, totalShots, load, clearError, clearAssetPreviews, loadImageJobs, prepareImageGeneration, confirmImageGeneration, prepareFullBodyGeneration, confirmFullBodyGeneration, loadFullBodyJobs, cancelImageGeneration, checkProductionPackage, uploadAsset, uploadCharacterSheet, approveAsset, loadAssetPreview, createEpisode, updateEpisode, createScene, updateScene, moveScene, createShot, updateShot, moveShot, createVisualProfile, updateVisualProfile, selectEpisode, selectScene }
})
