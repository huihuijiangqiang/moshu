import { USE_MOCK, request } from './http'
import { shelfApi as mockShelfApi, type ShelfBook } from './mock/shelf'

export type { ShelfBook } from './mock/shelf'

export interface CreateBookInput {
  title: string
  genre: string
  inspiration: string
  synopsis: string
  protagonist: string
  coreHook: string
  audience: string
  template: string
  tags: string[]
  volumes: Array<{ title: string; summary: string }>
  chapters?: Array<{ title: string; outline: string[]; volumeIndex?: number }>
  targetPlatform?: 'fanqie' | 'qimao' | 'qidian' | 'general'
}

export interface UpdateBookInput {
  title: string
  genre: string
  status: 'ongoing' | 'finished' | 'archived'
  dailyGoal: number
}

interface ProjectListDto {
  id: string
  title: string
  genre: string | null
  status: string
  words: number
  chapters: number
  codex_count: number
  guard_open: number
  last_chapter_title: string | null
  updated_at: string
  target_words_daily: number
  today_words: number
}

interface ProjectCreateDto {
  id: string
  title: string
  genre: string | null
  target_words_daily: number
  volumes: Array<{ id: string; title: string; idx: number }>
}

const coverTones: ShelfBook['coverTone'][] = ['mountain', 'city', 'river', 'spring', 'space']

function statusFromDto(status: string, chapters: number): ShelfBook['status'] {
  if (status === 'archived') return 'archived'
  if (status === 'finished') return 'finished'
  return chapters === 0 ? 'planning' : 'ongoing'
}

function shelfBookFromDto(dto: ProjectListDto, index: number): ShelfBook {
  const status = statusFromDto(dto.status, dto.chapters)
  const targetWords = status === 'finished'
    ? Math.max(dto.words, 1)
    : Math.max(100_000, Math.ceil(dto.words / 100_000) * 100_000)
  const touched = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' })
    .format(new Date(dto.updated_at))

  return {
    id: dto.id,
    title: dto.title,
    genre: dto.genre ?? '题材待补充',
    status,
    words: dto.words,
    chapters: dto.chapters,
    codexCount: dto.codex_count,
    guardOpen: dto.guard_open,
    lastTouched: `${touched} · ${dto.last_chapter_title ?? '更新作品资料'}`,
    targetWords,
    dailyGoal: dto.target_words_daily,
    progress: status === 'finished' ? 100 : Math.min(99, Math.round((dto.words / targetWords) * 100)),
    todayWords: dto.today_words,
    coverTone: coverTones[index % coverTones.length]
  }
}

const realShelfApi = {
  async listBooks(): Promise<ShelfBook[]> {
    const rows = await request<ProjectListDto[]>('/projects')
    return rows.map(shelfBookFromDto)
  },

  async createBook(input: CreateBookInput): Promise<ShelfBook> {
    const project = await request<ProjectCreateDto>('/projects', {
      method: 'POST',
      body: JSON.stringify({
        title: input.title,
        genre: input.genre,
        inspiration: input.inspiration,
        synopsis: input.synopsis,
        protagonist: input.protagonist,
        core_hook: input.coreHook,
        audience: input.audience,
        template: input.template,
        tags: input.tags,
        volumes: input.volumes,
        chapters: input.chapters ?? [],
        target_platform: input.targetPlatform ?? 'general'
      })
    })
    return {
      id: project.id,
      title: project.title,
      genre: project.genre ?? '题材待补充',
      status: 'planning',
      words: 0,
      chapters: 1,
      codexCount: [input.protagonist, input.coreHook].filter((value) => value.trim()).length,
      guardOpen: 0,
      lastTouched: '刚刚 · 创建故事骨架',
      targetWords: 600_000,
      dailyGoal: project.target_words_daily,
      progress: 0,
      todayWords: 0,
      coverTone: 'mountain'
    }
  },

  async updateBook(id: string, input: UpdateBookInput): Promise<ShelfBook> {
    const project = await request<{
      id: string; title: string; genre: string | null; status: string; target_words_daily: number
    }>(`/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        title: input.title,
        genre: input.genre,
        status: input.status,
        target_words_daily: input.dailyGoal
      })
    })
    const existing = (await realShelfApi.listBooks()).find((book) => book.id === id)
    if (!existing) throw new Error('book_not_found')
    return {
      ...existing,
      title: project.title,
      genre: project.genre ?? '题材待补充',
      status: statusFromDto(project.status, existing.chapters),
      dailyGoal: project.target_words_daily
    }
  }
}

export const shelfApi = USE_MOCK ? mockShelfApi : realShelfApi
