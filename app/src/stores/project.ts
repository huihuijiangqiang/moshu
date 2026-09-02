import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { contentApi } from '@/api/content'
import type { Chapter, ChapterPlanPatch, Project } from '@/types'

export const useProjectStore = defineStore('project', () => {
  const project = ref<Project | null>(null)
  const chapters = ref<Chapter[]>([])
  const activeId = ref<string | null>(null)
  const loadedProjectId = ref<string | null>(null)
  const loading = ref(false)

  const active = computed(() => chapters.value.find((c) => c.id === activeId.value) ?? null)

  const byVolume = computed(() =>
    (project.value?.volumes ?? []).map((v) => ({
      volume: v,
      chapters: chapters.value.filter((c) => c.volumeId === v.id).sort((a, b) => a.index - b.index)
    }))
  )

  const totalWords = computed(() => project.value?.wordCount ?? chapters.value.reduce((s, c) => s + c.words, 0))
  const totalChapters = computed(() => project.value?.chapterCount ?? chapters.value.length)

  async function load(projectId = 'p1') {
    if (loadedProjectId.value === projectId && project.value) return
    loading.value = true
    try {
      const [p, list] = await Promise.all([contentApi.getProject(projectId), contentApi.listChapters(projectId)])
      p.wordCount = list.reduce((sum, chapter) => sum + chapter.words, 0)
      p.chapterCount = list.length
      project.value = p
      chapters.value = list
      loadedProjectId.value = projectId
      activeId.value = list.find((c) => c.status === 'drafting')?.id ?? list.at(-1)?.id ?? null
    } finally {
      loading.value = false
    }
  }

  /** 正文按需拉取，切章后不保留旧章正文——避免整本书驻留内存 */
  async function openChapter(id: string) {
    activeId.value = id
    const target = chapters.value.find((c) => c.id === id)
    if (!target || target.content !== undefined) return
    const full = await contentApi.getChapter(id)
    if (full) target.content = full.content ?? ''
  }

  function setWords(id: string, words: number) {
    const c = chapters.value.find((x) => x.id === id)
    if (!c) return
    const delta = words - c.words
    c.words = words
    if (project.value?.wordCount !== undefined) project.value.wordCount += delta
  }

  function setContent(id: string, content: string, rev?: number) {
    const chapter = chapters.value.find((item) => item.id === id)
    if (!chapter) return
    chapter.content = content
    if (rev !== undefined) chapter.rev = rev
  }

  function setStyleProfile(id: string | null) {
    if (project.value) project.value.styleProfile = id
  }

  async function updateChapterPlan(id: string, patch: ChapterPlanPatch) {
    const updated = await contentApi.updateChapterPlan(id, patch)
    if (!updated) throw new Error('chapter_not_found')
    const index = chapters.value.findIndex((chapter) => chapter.id === id)
    if (index >= 0) chapters.value[index] = { ...chapters.value[index], ...updated }
    return updated
  }

  async function insertChapterAfter(volumeId: string, afterIndex: number) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    const created = await contentApi.insertChapter(projectId, volumeId, afterIndex)
    chapters.value.forEach((chapter) => {
      if (chapter.index > afterIndex) chapter.index += 1
    })
    chapters.value.push(created)
    if (project.value?.chapterCount !== undefined) project.value.chapterCount += 1
    return created
  }

  return {
    project, chapters, activeId, active, byVolume, totalWords, totalChapters, loading, loadedProjectId,
    load, openChapter, setWords, setContent, setStyleProfile, updateChapterPlan, insertChapterAfter
  }
})
