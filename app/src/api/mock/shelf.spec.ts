import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from './index'
import { shelfApi } from './shelf'

describe('mock shelf project creation', () => {
  const storage = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key)
  })

  beforeEach(() => {
    storage.clear()
  })

  it('keeps a created female farming project and its chapter outlines', async () => {
    const book = await shelfApi.createBook({
      title: '春日田园记',
      genre: '女频 · 穿越种田 · 经营',
      inspiration: '她穿越到荒年农家，从一亩薄田开始翻身。',
      synopsis: '女频穿越种田故事，事业与感情双线成长。',
      protagonist: '林知微 · 农家长女',
      coreHook: '会看农时的旧账本',
      audience: '女频',
      template: '穿越种田',
      tags: ['穿越', '经营', '感情线'],
      volumes: [
        { title: '第一卷 · 荒年起步', summary: '保住田地和家人。' },
        { title: '第二卷 · 小铺开张', summary: '把手艺变成生计。' }
      ],
      chapters: [
        { title: '第 1 章 · 醒在荒年', outline: ['确认穿越处境', '盘点家中余粮'] },
        { title: '第 2 章 · 先种一畦菜', outline: ['寻找种子', '说服家人'], volumeIndex: 0 },
        { title: '第 3 章 · 小铺开张', outline: ['试做腌菜', '谈下铺面'], volumeIndex: 1 }
      ],
      targetPlatform: 'fanqie'
    })

    const project = await mockApi.getProject(book.id)
    const chapters = await mockApi.listChapters(book.id)

    expect(project.title).toBe('春日田园记')
    expect(project.genre).toContain('穿越种田')
    expect(project.volumes).toEqual([
      { id: `${book.id}-v1`, index: 1, title: '第一卷 · 荒年起步' },
      { id: `${book.id}-v2`, index: 2, title: '第二卷 · 小铺开张' }
    ])
    expect(chapters).toHaveLength(3)
    expect(chapters[0]).toMatchObject({ title: '第 1 章 · 醒在荒年', outline: ['确认穿越处境', '盘点家中余粮'], volumeId: `${book.id}-v1` })
    expect(chapters[1]).toMatchObject({ title: '第 2 章 · 先种一畦菜', outline: ['寻找种子', '说服家人'], volumeId: `${book.id}-v1` })
    expect(chapters[2]).toMatchObject({ title: '第 3 章 · 小铺开张', volumeId: `${book.id}-v2` })
  })
})
