import { delay } from '../http'

export interface ShelfBook {
  id: string
  title: string
  genre: string
  status: 'ongoing' | 'finished' | 'planning' | 'archived'
  words: number
  chapters: number
  codexCount: number
  guardOpen: number
  lastTouched: string
  targetWords: number
  dailyGoal?: number
  progress: number
  todayWords: number
  coverTone: 'mountain' | 'city' | 'river' | 'spring' | 'space'
}

export interface CreatedBook extends ShelfBook {
  inspiration: string
  synopsis: string
  protagonist: string
  coreHook: string
  audience: string
  template: string
  tags: string[]
  targetPlatform: 'fanqie' | 'qimao' | 'qidian' | 'general'
  draftVolumes: Array<{ title: string; summary: string }>
  draftChapters: Array<{ title: string; outline: string[]; volumeIndex?: number }>
}

const CREATED_BOOKS_KEY = 'moshu:created-books'

export const SHELF_BOOKS: ShelfBook[] = [
  {
    id: 'p1', title: '剑起山河', genre: '男频 · 边关权谋', status: 'ongoing',
    words: 783000, chapters: 89, codexCount: 142, guardOpen: 3,
    lastTouched: '今天 10:23 · 第 88 章 边关雪夜', targetWords: 1200000,
    progress: 72, todayWords: 2780, coverTone: 'mountain'
  },
  {
    id: 'p2', title: '城南旧事簿', genre: '都市 · 异闻', status: 'finished',
    words: 521000, chapters: 61, codexCount: 88, guardOpen: 0,
    lastTouched: '2026-05-18 · 第 61 章 无人知晓', targetWords: 500000,
    progress: 100, todayWords: 0, coverTone: 'city'
  },
  {
    id: 'p3', title: '长夜渡舟', genre: '悬疑 · 民俗', status: 'ongoing',
    words: 126000, chapters: 16, codexCount: 47, guardOpen: 1,
    lastTouched: '今天 08:15 · 第 16 章 纸船灯影', targetWords: 300000,
    progress: 42, todayWords: 932, coverTone: 'river'
  },
  {
    id: 'p4', title: '春风不度', genre: '古言 · 群像', status: 'planning',
    words: 0, chapters: 0, codexCount: 26, guardOpen: 0,
    lastTouched: '昨天 18:42 · 更新人物关系图', targetWords: 600000,
    progress: 0, todayWords: 0, coverTone: 'spring'
  },
  {
    id: 'p5', title: '失重花园', genre: '科幻 · 悬疑', status: 'ongoing',
    words: 87000, chapters: 12, codexCount: 39, guardOpen: 2,
    lastTouched: '昨天 23:56 · 第 12 章 失重边界', targetWords: 250000,
    progress: 35, todayWords: 1108, coverTone: 'space'
  }
]

function readCreatedBooks(): CreatedBook[] {
  if (typeof localStorage === 'undefined' || typeof localStorage.getItem !== 'function') return []
  try {
    const value = JSON.parse(localStorage.getItem(CREATED_BOOKS_KEY) ?? '[]')
    return Array.isArray(value) ? value : []
  } catch {
    return []
  }
}

function writeCreatedBooks(books: CreatedBook[]) {
  if (typeof localStorage === 'undefined' || typeof localStorage.setItem !== 'function') return
  localStorage.setItem(CREATED_BOOKS_KEY, JSON.stringify(books))
}

export function findShelfBook(id: string): CreatedBook | ShelfBook | undefined {
  return readCreatedBooks().find((book) => book.id === id) ?? SHELF_BOOKS.find((book) => book.id === id)
}

export const shelfApi = {
  async listBooks(): Promise<ShelfBook[]> {
    await delay()
    return structuredClone([...readCreatedBooks(), ...SHELF_BOOKS])
  },

  async createBook(input: {
    title: string
    genre: string
    inspiration?: string
    synopsis?: string
    protagonist?: string
    coreHook?: string
    audience?: string
    template?: string
    tags?: string[]
    volumes?: Array<{ title: string; summary: string }>
    chapters?: Array<{ title: string; outline: string[]; volumeIndex?: number }>
    targetPlatform?: 'fanqie' | 'qimao' | 'qidian' | 'general'
  }): Promise<ShelfBook> {
    await delay(160)
    const draftVolumes = (input.volumes ?? []).map((volume) => ({ title: volume.title, summary: volume.summary }))
    const draftChapters = (input.chapters ?? []).map((chapter) => ({
      title: chapter.title,
      outline: [...chapter.outline],
      volumeIndex: chapter.volumeIndex
    }))
    const book: CreatedBook = {
      id: `draft-${Date.now().toString(36)}`,
      title: input.title.trim() || '未命名作品',
      genre: input.genre,
      status: 'planning',
      words: 0,
      chapters: draftChapters.length,
      codexCount: 0,
      guardOpen: 0,
      lastTouched: '刚刚 · 创建故事骨架',
      targetWords: 600000,
      progress: 0,
      todayWords: 0,
      coverTone: 'mountain',
      inspiration: input.inspiration ?? '',
      synopsis: input.synopsis ?? '',
      protagonist: input.protagonist ?? '',
      coreHook: input.coreHook ?? '',
      audience: input.audience ?? '通用',
      template: input.template ?? '',
      tags: [...(input.tags ?? [])],
      targetPlatform: input.targetPlatform ?? 'general',
      draftVolumes,
      draftChapters
    }
    writeCreatedBooks([book, ...readCreatedBooks()])
    return structuredClone(book)
  },

  async updateBook(id: string, patch: { title: string; genre: string; status: 'ongoing' | 'finished' | 'archived'; dailyGoal: number }): Promise<ShelfBook> {
    await delay(120)
    const created = readCreatedBooks()
    const createdIndex = created.findIndex((book) => book.id === id)
    const book = createdIndex >= 0 ? created[createdIndex] : SHELF_BOOKS.find((item) => item.id === id)
    if (!book) throw new Error('book_not_found')
    Object.assign(book, patch)
    if (createdIndex >= 0) writeCreatedBooks(created)
    return structuredClone(book)
  },

  async dailySeries(): Promise<number[]> {
    await delay(140)
    return [3100, 4100, 6000, 2650, 0, 4560, 5280, 3600, 4320, 5760, 2880, 3840, 4800, 4280]
  },

}
