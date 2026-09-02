import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { usageApi, type UsageSummary } from '@/api/usage'

export const useUsageStore = defineStore('usage', () => {
  const summary = ref<UsageSummary | null>(null)
  const loading = ref(false)
  const error = ref('')
  let inFlight: Promise<void> | null = null

  const remaining = computed(() => summary.value?.remaining ?? 0)
  const quota = computed(() => summary.value?.quota ?? 0)

  async function load(force = false) {
    if (inFlight) return inFlight
    if (!force && summary.value) return
    loading.value = true
    error.value = ''
    inFlight = usageApi.summary()
      .then((result) => { summary.value = result })
      .catch((caught) => {
        error.value = caught instanceof Error ? caught.message : '用量数据加载失败'
        throw caught
      })
      .finally(() => {
        loading.value = false
        inFlight = null
      })
    return inFlight
  }

  return { summary, loading, error, remaining, quota, load }
})
