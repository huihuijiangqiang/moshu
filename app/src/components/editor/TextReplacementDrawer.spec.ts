import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { textReplacementApi } from '@/api/text-replacement'
import TextReplacementDrawer from './TextReplacementDrawer.vue'

const preview = {
  previewToken: 'a'.repeat(64),
  totalMatches: 2,
  chapters: [{ id: 'ch-1', title: '落脚', index: 1, volumeId: 'v-1', rev: 3, matchCount: 2 }],
  matches: [
    { id: 'm-1', chapterId: 'ch-1', chapterTitle: '落脚', chapterIndex: 1, paragraphId: 'p-1', before: '她叫', matched: '许知微', after: '。', replacement: '许掌柜' },
    { id: 'm-2', chapterId: 'ch-1', chapterTitle: '落脚', chapterIndex: 1, paragraphId: 'p-2', before: '众人看向', matched: '许知微', after: '。', replacement: '许掌柜' }
  ],
  warnings: [{ entryId: 'cx-1', name: '许知微', kind: 'character', matchedTerm: '许知微', referencedChapters: 4 }]
}

function mountDrawer() {
  return mount(TextReplacementDrawer, {
    props: {
      projectId: 'p-1',
      activeChapterId: 'ch-1',
      activeVolumeId: 'v-1'
    }
  })
}

describe('TextReplacementDrawer', () => {
  afterEach(() => vi.restoreAllMocks())

  it('previews every occurrence and requires codex-risk acknowledgement', async () => {
    vi.spyOn(textReplacementApi, 'preview').mockResolvedValue(preview)
    const execute = vi.spyOn(textReplacementApi, 'execute').mockResolvedValue({
      id: 'run-1', status: 'applied', totalMatches: 2,
      affectedChapters: [{ chapterId: 'ch-1', chapterTitle: '落脚', beforeRev: 3, afterRev: 4, matchCount: 2 }]
    })
    const wrapper = mountDrawer()
    const inputs = wrapper.findAll('.replace-controls input')
    await inputs[0]!.setValue('许知微')
    await inputs[1]!.setValue('许掌柜')
    await wrapper.findAll('.replace-control-row button').at(-1)!.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.replace-proof')).toHaveLength(2)
    expect(wrapper.text()).toContain('查找内容涉及设定名')
    const executeButton = wrapper.findAll('.replace-actions button').at(-1)!
    expect(executeButton.attributes('disabled')).toBeDefined()

    await wrapper.get('.replace-warning input').setValue(true)
    expect(executeButton.attributes('disabled')).toBeUndefined()
    await executeButton.trigger('click')
    await flushPromises()

    expect(execute).toHaveBeenCalledWith('p-1', expect.objectContaining({ scope: 'project' }), preview.previewToken, ['m-1', 'm-2'], true)
    expect(wrapper.emitted('changed')?.[0]?.[0]).toMatchObject({ id: 'run-1', status: 'applied' })
    expect(wrapper.text()).toContain('已替换 2 处')
  })

  it('lets the author exclude an individual occurrence from the batch', async () => {
    vi.spyOn(textReplacementApi, 'preview').mockResolvedValue({ ...preview, warnings: [] })
    const execute = vi.spyOn(textReplacementApi, 'execute').mockResolvedValue({
      id: 'run-2', status: 'applied', totalMatches: 1,
      affectedChapters: [{ chapterId: 'ch-1', chapterTitle: '落脚', beforeRev: 3, afterRev: 4, matchCount: 1 }]
    })
    const wrapper = mountDrawer()
    const inputs = wrapper.findAll('.replace-controls input')
    await inputs[0]!.setValue('许知微')
    await inputs[1]!.setValue('许掌柜')
    await wrapper.findAll('.replace-control-row button').at(-1)!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.replace-proof input')[0]!.setValue(false)
    await wrapper.findAll('.replace-actions button').at(-1)!.trigger('click')
    await flushPromises()

    expect(execute).toHaveBeenCalledWith('p-1', expect.anything(), preview.previewToken, ['m-2'], false)
  })

  it('opens the exact source paragraph from a preview row', async () => {
    vi.spyOn(textReplacementApi, 'preview').mockResolvedValue({ ...preview, warnings: [] })
    const wrapper = mountDrawer()
    const inputs = wrapper.findAll('.replace-controls input')
    await inputs[0]!.setValue('许知微')
    await inputs[1]!.setValue('许掌柜')
    await wrapper.findAll('.replace-control-row button').at(-1)!.trigger('click')
    await flushPromises()

    await wrapper.findAll('.replace-open')[0]!.trigger('click')
    expect(wrapper.emitted('locate')).toEqual([['ch-1', 'p-1']])
  })
})
