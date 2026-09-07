import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { stylesApi, type StyleProfile } from '@/api/styles'

export const useStylesStore = defineStore('styles', () => {
  const profiles = ref<StyleProfile[]>([])
  const loaded = ref(false)
  const loading = ref(false)

  const byId = computed(() => new Map(profiles.value.map((profile) => [profile.id, profile])))

  async function load(force = false) {
    if (loaded.value && !force) return
    loading.value = true
    try {
      profiles.value = await stylesApi.list()
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  function replace(profile: StyleProfile) {
    const index = profiles.value.findIndex((item) => item.id === profile.id)
    if (index >= 0) profiles.value[index] = profile
    else profiles.value.unshift(profile)
  }

  async function create(input: { name: string; sampleText: string; isDefault?: boolean }) {
    const profile = await stylesApi.create(input)
    replace(profile)
    if (profile.isDefault) profiles.value.forEach((item) => { item.isDefault = item.id === profile.id })
    return profile
  }

  async function update(id: string, input: { name?: string; sampleText?: string; isDefault?: boolean }) {
    const profile = await stylesApi.update(id, input)
    replace(profile)
    if (profile.isDefault) profiles.value.forEach((item) => { item.isDefault = item.id === profile.id })
    return profile
  }

  async function extract(id: string) {
    const profile = await stylesApi.extract(id)
    replace(profile)
    return profile
  }

  async function remove(id: string) {
    await stylesApi.remove(id)
    profiles.value = profiles.value.filter((item) => item.id !== id)
    await load(true)
  }

  async function bind(projectId: string, id: string | null) {
    const result = await stylesApi.bind(projectId, id)
    profiles.value.forEach((item) => {
      item.boundProjectIds = item.boundProjectIds.filter((value) => value !== projectId)
      if (item.id === result.styleProfileId) item.boundProjectIds.push(projectId)
    })
    return result.styleProfileId
  }

  return { profiles, byId, loaded, loading, load, create, update, extract, remove, bind }
})
