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
    try {
      adaptation.value = await mockApi.getStoryboard(projectId)
      loadedProjectId.value = projectId
      selectedEpisodeId.value = null
      selectedSceneId.value = null
      selectedShotId.value = null
      selectDefaults()
    } finally {
      loading.value = false
    }
  }

  async function createEpisode(projectId: string) {
    const episode = await mockApi.createStoryboardEpisode(projectId, { title: `第 ${(adaptation.value?.episodes.length ?? 0) + 1} 集 · 待命名`, sourceChapterIds: [], targetDuration: 90 })
    adaptation.value?.episodes.push(episode)
    selectedEpisodeId.value = episode.id
    selectedSceneId.value = null
    selectedShotId.value = null
  }

  async function createScene(projectId: string) {
    if (!selectedEpisode.value) return
    const scene = await mockApi.createStoryboardScene(projectId, selectedEpisode.value.id, { purpose: '填写这一场要完成的叙事任务', summary: '', timeAnchor: '', locationEntryId: undefined, characterEntryIds: [] })
    selectedEpisode.value.scenes.push(scene)
    selectedSceneId.value = scene.id
    selectedShotId.value = null
  }

  async function createShot(projectId: string) {
    if (!selectedScene.value) return
    const shot = await mockApi.createStoryboardShot(projectId, selectedScene.value.id, {})
    selectedScene.value.shots.push(shot)
    selectedShotId.value = shot.id
  }

  async function updateShot(projectId: string, patch: Partial<StoryboardShot>) {
    if (!selectedShot.value) return
    const updated = await mockApi.updateStoryboardShot(projectId, selectedShot.value.id, patch)
    Object.assign(selectedShot.value, updated)
  }

  async function updateVisualProfile(projectId: string, profileId: string, patch: Partial<VisualProfile>) {
    const updated = await mockApi.updateVisualProfile(projectId, profileId, patch)
    const profile = adaptation.value?.visualProfiles.find((item) => item.id === profileId)
    if (profile) Object.assign(profile, updated)
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

  return { adaptation, loadedProjectId, loading, selectedEpisodeId, selectedSceneId, selectedShotId, selectedEpisode, selectedScene, selectedShot, totalShots, load, createEpisode, createScene, createShot, updateShot, updateVisualProfile, selectEpisode, selectScene }
})
