import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockApi } from '@/api/mock'
import type { Chapter, Project } from '@/types'

export const useProjectStore = defineStore('project', () => {
  const project = ref<Project | null>(null)
  const chapters = ref<Chapter[]>([])
  const activeId = ref<string | null>(null)
  const loading = ref(false)

  const active = computed(() => chapters.value.find((c) => c.id === activeId.value) ?? null)

  const byVolume = computed(() =>
    (project.value?.volumes ?? []).map((v) => ({
      volume: v,
      chapters: chapters.value.filter((c) => c.volumeId === v.id).sort((a, b) => a.index - b.index)
    }))
  )

  const totalWords = computed(() => chapters.value.reduce((s, c) => s + c.words, 0))

  async function load() {
    loading.value = true
    try {
      const [p, list] = await Promise.all([mockApi.getProject(), mockApi.listChapters()])
      project.value = p
      chapters.value = list
      activeId.value ??= list.find((c) => c.status === 'drafting')?.id ?? list[0]?.id ?? null
    } finally {
      loading.value = false
    }
  }

  /** 正文按需拉取，切章后不保留旧章正文——避免整本书驻留内存 */
  async function openChapter(id: string) {
    activeId.value = id
    const target = chapters.value.find((c) => c.id === id)
    if (!target || target.content !== undefined) return
    const full = await mockApi.getChapter(id)
    if (full) target.content = full.content ?? ''
  }

  function setWords(id: string, words: number) {
    const c = chapters.value.find((x) => x.id === id)
    if (c) c.words = words
  }

  return { project, chapters, activeId, active, byVolume, totalWords, loading, load, openChapter, setWords }
})
