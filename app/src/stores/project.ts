import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { contentApi } from '@/api/content'
import type { Chapter, ChapterPlanPatch, Project, ProjectPatch, ProjectTrash } from '@/types'

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

  async function refreshStructure() {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    // The structure endpoint deliberately omits bodies and body revisions. Preserve each
    // locally loaded body with its matching revision while refreshing only metadata. A
    // revision changed in another tab is intentionally resolved by the save-time 409 flow.
    const loadedBodiesById = new Map(
      chapters.value
        .filter((chapter) => chapter.content !== undefined)
        .map((chapter) => [chapter.id, { content: chapter.content, rev: chapter.rev }])
    )
    const [nextProject, list] = await Promise.all([
      contentApi.getProject(projectId),
      contentApi.listChapters(projectId)
    ])
    list.forEach((chapter) => Object.assign(chapter, loadedBodiesById.get(chapter.id)))
    nextProject.wordCount = list.reduce((sum, chapter) => sum + chapter.words, 0)
    nextProject.chapterCount = list.length
    project.value = nextProject
    chapters.value = list
    if (!list.some((chapter) => chapter.id === activeId.value)) activeId.value = list.at(-1)?.id ?? null
  }

  /** 正文按需拉取，切章后不保留旧章正文——避免整本书驻留内存 */
  async function openChapter(id: string) {
    activeId.value = id
    const target = chapters.value.find((c) => c.id === id)
    if (!target || target.content !== undefined) return
    const full = await contentApi.getChapter(id)
    if (full) Object.assign(target, { content: full.content ?? '', rev: full.rev })
  }

  async function reloadReplacedChapters(ids: string[]) {
    const affected = new Set(ids)
    chapters.value.forEach((chapter) => {
      if (!affected.has(chapter.id)) return
      chapter.content = undefined
      chapter.rev = undefined
    })
    await refreshStructure()
    if (activeId.value && affected.has(activeId.value)) await openChapter(activeId.value)
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

  async function updateProject(patch: ProjectPatch) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    const updated = await contentApi.updateProject(projectId, patch)
    project.value = { ...project.value, ...updated }
  }

  async function createVolume(title: string, summary = '') {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    await contentApi.createVolume(projectId, title, summary)
    await refreshStructure()
  }

  async function updateVolume(volumeId: string, patch: { title?: string; summary?: string }) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    await contentApi.updateVolume(projectId, volumeId, patch)
    await refreshStructure()
  }

  async function reorderVolumes(volumeIds: string[]) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    await contentApi.reorderVolumes(projectId, volumeIds)
    await refreshStructure()
  }

  async function moveChapter(chapterId: string, volumeId: string, placement: 'first' | 'last' | 'after', afterChapterId?: string) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    await contentApi.moveChapter(projectId, chapterId, volumeId, placement, afterChapterId)
    await refreshStructure()
  }

  async function trashChapter(chapterId: string) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    await contentApi.trashChapter(projectId, chapterId)
    await refreshStructure()
  }

  async function trashVolume(volumeId: string, targetVolumeId?: string) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    await contentApi.trashVolume(projectId, volumeId, targetVolumeId)
    await refreshStructure()
  }

  async function getTrash(): Promise<ProjectTrash> {
    if (!loadedProjectId.value) throw new Error('project_not_loaded')
    return contentApi.getTrash(loadedProjectId.value)
  }

  async function restoreTrash(kind: 'volumes' | 'chapters', id: string, volumeId?: string) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    if (kind === 'volumes') await contentApi.restoreVolume(projectId, id)
    else await contentApi.restoreChapter(projectId, id, volumeId)
    await refreshStructure()
  }

  async function deleteTrash(kind: 'volumes' | 'chapters', id: string) {
    const projectId = loadedProjectId.value
    if (!projectId) throw new Error('project_not_loaded')
    await contentApi.deleteTrashItem(projectId, kind, id)
  }

  return {
    project, chapters, activeId, active, byVolume, totalWords, totalChapters, loading, loadedProjectId,
    load, refreshStructure, openChapter, reloadReplacedChapters, setWords, setContent, setStyleProfile, updateChapterPlan,
    insertChapterAfter, updateProject, createVolume, updateVolume, reorderVolumes, moveChapter,
    trashChapter, trashVolume, getTrash, restoreTrash, deleteTrash
  }
})
