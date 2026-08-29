import { delay } from '../http'

export interface StyleProfile {
  id: string
  name: string
  source: string
  sampleWords: number
  isDefault: boolean
  extractedAt: string
  alignment: number
  recent: number[]
}

export const styleApi = {
  async list(): Promise<StyleProfile[]> {
    await delay()
    return [
      { id: 's1', name: '沈氏白描', source: '《城南旧事簿》全 61 章', sampleWords: 521000, isDefault: true, extractedAt: '8 月 20 日', alignment: 82, recent: [74, 81, 69, 88, 91, 58, 79, 85, 83, 82] },
      { id: 's2', name: '早期热血', source: '早期短篇合集', sampleWords: 182000, isDefault: false, extractedAt: '7 月 2 日', alignment: 71, recent: [66, 70, 74, 69, 72, 68, 75, 71, 70, 71] },
      { id: 's3', name: '女频试写', source: '试写稿 3 篇', sampleWords: 32000, isDefault: false, extractedAt: '8 月 25 日', alignment: 0, recent: [] }
    ]
  },

  async dimensions(_profileId: string) {
    await delay(150)
    return [
      { key: 'sentence', title: '句长分布', chart: [30, 72, 100, 62, 28, 12], note: '中位 17 字，短句为主，极少超过 40 字的长句。' },
      { key: 'dialogue', title: '对白 / 叙述', ratio: 44, note: '对白偏多，且几乎不带情绪副词（「他冷冷地说」这类被剔除）。' },
      { key: 'adjective', title: '形容词密度', value: '1.8', unit: '/ 百字', note: '显著低于同类作者均值（4.1），这是「白描」的来源。' },
      { key: 'imagery', title: '高频意象', tags: ['雪', '铁', '火光', '断口', '指腹'], note: '生成时优先复用这组意象，而非泛用的「星辰」「命运」。' },
      { key: 'hook', title: '章末钩子写法', note: '76% 的章以一句未答的问话或一个未解释的动作结束，不用「欲知后事」式旁白。' },
      { key: 'tic', title: '口头禅与习惯', note: '「那时他不懂」「没有回头」反复出现；从不使用感叹号。' }
    ]
  }
}
