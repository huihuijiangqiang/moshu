import { delay } from '../http'

export interface ShelfBook {
  id: string
  title: string
  genre: string
  status: 'ongoing' | 'finished'
  words: number
  chapters: number
  codexCount: number
  guardOpen: number
  lastTouched: string
}

export interface UsageBreakdown { label: string; count: string; credits: number | 'free' }

export const shelfApi = {
  async listBooks(): Promise<ShelfBook[]> {
    await delay()
    return [
      { id: 'p1', title: '剑起山河', genre: '男频 · 边关权谋', status: 'ongoing', words: 783000, chapters: 88, codexCount: 142, guardOpen: 3, lastTouched: '12 分钟前 · 第 87 章 断刃' },
      { id: 'p2', title: '城南旧事簿', genre: '男频 · 都市异闻', status: 'finished', words: 521000, chapters: 61, codexCount: 88, guardOpen: 0, lastTouched: '完结于 3 月 12 日 · 已作为风格档样本' }
    ]
  },

  async dailySeries(): Promise<number[]> {
    await delay(140)
    return [3100, 4100, 6000, 2650, 0, 4560, 5280, 3600, 4320, 5760, 2880, 3840, 4800, 4280]
  },

  async usage(): Promise<{ remaining: number; quota: number; plan: string; price: string; items: UsageBreakdown[] }> {
    await delay(160)
    return {
      remaining: 2840,
      quota: 5000,
      plan: '作者版',
      price: '69 元 / 月',
      items: [
        { label: '一键成章', count: '46 次', credits: 1610 },
        { label: '行内续写与润色', count: '218 次', credits: 392 },
        { label: '一致性守卫扫描', count: '88 章', credits: 96 },
        { label: '风格档抽取', count: '1 次', credits: 62 },
        { label: '章摘要生成（自动）', count: '每章', credits: 'free' }
      ]
    }
  }
}
