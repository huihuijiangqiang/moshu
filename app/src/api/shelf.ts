import { USE_MOCK, request } from './http'
import { shelfApi as mockShelfApi, type ShelfBook } from './mock/shelf'

export type { ShelfBook } from './mock/shelf'

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
}

const coverTones: ShelfBook['coverTone'][] = ['mountain', 'city', 'river', 'spring', 'space']

function statusFromDto(status: string, chapters: number): ShelfBook['status'] {
  if (status === 'finished' || status === 'archived') return 'finished'
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
    todayWords: 0,
    coverTone: coverTones[index % coverTones.length]
  }
}

const realShelfApi = {
  async listBooks(): Promise<ShelfBook[]> {
    const rows = await request<ProjectListDto[]>('/projects')
    return rows.map(shelfBookFromDto)
  }
}

export const shelfApi = USE_MOCK ? mockShelfApi : realShelfApi
