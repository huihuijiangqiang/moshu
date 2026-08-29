import { delay } from '../http'

export type SegmentSource = 'ai-raw' | 'ai-edited' | 'human' | 'suspect'

export interface RatioSegment { text: string; source: SegmentSource; note?: string }

export interface SuspectLine { id: string; text: string; reason: string }

export const ratioApi = {
  async report(_chapterId: string) {
    await delay(200)
    return {
      aiRaw: 39,
      aiEdited: 22,
      human: 39,
      suspectCount: 11,
      paragraphs: [
        [
          { text: '风雪压着城墙走了三日，第四日清晨忽然停了。', source: 'ai-raw' },
          { text: '沈砚站在垛口上，望着远处那片被踩得发黑的雪原，', source: 'human' },
          { text: '手里的残锋还在渗血——不是敌人的。', source: 'ai-edited' }
        ],
        [
          { text: '他的内心涌起一阵复杂的情绪，仿佛有千言万语却无从说起。', source: 'suspect', note: '抽象情绪堆叠，与你的白描风格冲突' }
        ],
        [{ text: '「将军，兵器坊那边说……」亲卫的声音在身后停住了。', source: 'human' }],
        [
          { text: '沈砚没有回头。他把残锋横过来，指腹从断口上一路抹到剑柄。', source: 'ai-edited' },
          { text: '二十年前师父把这把剑交到他手上时说过，剑断了不必换，人断了才要紧。那时他不懂。', source: 'human' }
        ]
      ] as RatioSegment[][],
      suspects: [
        { id: 'r1', text: '「内心涌起一阵复杂的情绪」', reason: '抽象情绪堆叠。你的旧作里从不这样写。' },
        { id: 'r2', text: '「仿佛有千言万语却无从说起」', reason: '高频套语，在网文语料里出现率极高。' },
        { id: 'r3', text: '「空气仿佛凝固了一般」', reason: '场面描写套语，缺少具体物象。' }
      ] as SuspectLine[]
    }
  }
}
