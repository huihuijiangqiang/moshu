import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockApi } from '@/api/mock'
import { USE_MOCK } from '@/api/http'
import { storyboardApi } from '@/api/storyboard'
import type { StoryboardAdaptation, StoryboardEpisode, StoryboardScene, StoryboardShot, VisualProfile } from '@/types'

export const useStoryboardStore = defineStore('storyboard', () => {
  const api = USE_MOCK ? mockApi : storyboardApi
  const adaptation = ref<StoryboardAdaptation | null>(null)
  const loadedProjectId = ref<string | null>(null)
  const selectedEpisodeId = ref<string | null>(null)
  const selectedSceneId = ref<string | null>(null)
  const selectedShotId = ref<string | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  const selectedEpisode = computed(() => adaptation.value?.episodes.find((episode) => episode.id === selectedEpisodeId.value) ?? adaptation.value?.episodes[0] ?? null)
  const selectedScene = computed(() => selectedEpisode.value?.scenes.find((scene) => scene.id === selectedSceneId.value) ?? selectedEpisode.value?.scenes[0] ?? null)
  const selectedShot = computed(() => selectedScene.value?.shots.find((shot) => shot.id === selectedShotId.value) ?? selectedScene.value?.shots[0] ?? null)
  const totalShots = computed(() => adaptation.value?.episodes.reduce((total, episode) => total + episode.scenes.reduce((sceneTotal, scene) => sceneTotal + scene.shots.length, 0), 0) ?? 0)

  function selectDefaults() {
    selectedEpisodeId.value = selectedEpisodeId.value ?? adaptation.value?.episodes[0]?.id ?? null
    selectedSceneId.value = selectedSceneId.value ?? selectedEpisode.value?.scenes[0]?.id ?? null
    selectedShotId.value = selectedShotId.value ?? selectedScene.value?.shots[0]?.id ?? null
  }

  async function load(projectId: string) {
    if (loadedProjectId.value === projectId && adaptation.value) return
    loading.value = true
    error.value = ''
    try {
      adaptation.value = await api.getStoryboard(projectId)
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
    selectedEpisodeId.value = id
    selectedSceneId.value = adaptation.value?.episodes.find((episode) => episode.id === id)?.scenes[0]?.id ?? null
    selectedShotId.value = selectedScene.value?.shots[0]?.id ?? null
  }
  function selectScene(id: string) {
    selectedSceneId.value = id
    selectedShotId.value = selectedScene.value?.shots[0]?.id ?? null
  }

  return { adaptation, loadedProjectId, loading, saving, error, selectedEpisodeId, selectedSceneId, selectedShotId, selectedEpisode, selectedScene, selectedShot, totalShots, load, clearError, createEpisode, updateEpisode, createScene, updateScene, createShot, updateShot, createVisualProfile, updateVisualProfile, selectEpisode, selectScene }
})
