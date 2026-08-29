import { USE_MOCK } from './http'
import { mockApi } from './mock'
import type { GenerateOptions } from '@/types'

export interface StreamHandlers {
  /** 文本增量。'\n' 表示分段。 */
  onChunk: (text: string) => void
  onNode?: (nodeIndex: number) => void
  onDone?: () => void
  onError?: (e: unknown) => void
}

/**
 * 一键成章。mock 下逐字回放固定文本；真实环境走后端 SSE。
 * 返回 abort 函数——生成必须可中断，这是硬需求。
 */
export function streamChapter(opts: GenerateOptions, h: StreamHandlers): () => void {
  const ctrl = new AbortController()
  if (USE_MOCK) {
    void mockStream(ctrl.signal, h)
  } else {
    void sseStream(opts, ctrl.signal, h)
  }
  return () => ctrl.abort()
}

async function mockStream(signal: AbortSignal, h: StreamHandlers) {
  try {
    const paras = mockApi.draftParagraphs
    for (let p = 0; p < paras.length; p++) {
      if (p > 0) h.onChunk('\n')
      h.onNode?.(p)
      for (const ch of paras[p]) {
        if (signal.aborted) return
        h.onChunk(ch)
        await new Promise((r) => setTimeout(r, 26))
      }
    }
    h.onDone?.()
  } catch (e) {
    if (!signal.aborted) h.onError?.(e)
  }
}

/** 原生 fetch 读 SSE：比 EventSource 好，因为要带 POST body 和鉴权头。 */
async function sseStream(opts: GenerateOptions, signal: AbortSignal, h: StreamHandlers) {
  try {
    const res = await fetch(`${import.meta.env.VITE_API_BASE ?? '/api'}/generate/chapter`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
      signal
    })
    if (!res.body) throw new Error('no stream body')
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const frames = buf.split('\n\n')
      buf = frames.pop() ?? ''
      for (const frame of frames) {
        const line = frame.split('\n').find((l) => l.startsWith('data:'))
        if (!line) continue
        const payload = line.slice(5).trim()
        if (payload === '[DONE]') { h.onDone?.(); return }
        const evt = JSON.parse(payload) as { text?: string; node?: number }
        if (typeof evt.node === 'number') h.onNode?.(evt.node)
        if (evt.text) h.onChunk(evt.text)
      }
    }
    h.onDone?.()
  } catch (e) {
    if (!signal.aborted) h.onError?.(e)
  }
}
