import { USE_MOCK } from './http'
import { mockApi } from './mock'
import type { GenerateOptions, GenerationMeta, InlineGenerateOptions } from '@/types'

export interface StreamHandlers {
  onChunk: (text: string) => void
  onNode?: (nodeIndex: number) => void
  onMeta?: (meta: GenerationMeta) => void
  onDone?: (result?: Record<string, unknown>) => void
  onError?: (e: unknown) => void
}

export class GenerationError extends Error {
  constructor(public code: string, message: string, public status?: number) {
    super(message)
    this.name = 'GenerationError'
  }
}

export function streamChapter(opts: GenerateOptions, h: StreamHandlers): () => void {
  return startStream('/generate/chapter', opts, h)
}

export function streamInline(opts: InlineGenerateOptions, h: StreamHandlers): () => void {
  return startStream('/generate/inline', opts, h)
}

function startStream(path: string, opts: GenerateOptions | InlineGenerateOptions, h: StreamHandlers): () => void {
  const ctrl = new AbortController()
  if (USE_MOCK) void mockStream(ctrl.signal, h)
  else void sseStream(path, opts, ctrl.signal, h)
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
        await new Promise((resolve) => setTimeout(resolve, 26))
      }
    }
    h.onDone?.()
  } catch (error) {
    if (!signal.aborted) h.onError?.(error)
  }
}

async function responseError(res: Response): Promise<GenerationError> {
  const text = await res.text()
  try {
    const payload = JSON.parse(text) as { detail?: string | { message?: string } }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message
    return new GenerationError('http_error', detail || `生成请求失败（${res.status}）`, res.status)
  } catch {
    return new GenerationError('http_error', text || `生成请求失败（${res.status}）`, res.status)
  }
}

async function sseStream(
  path: string,
  opts: GenerateOptions | InlineGenerateOptions,
  signal: AbortSignal,
  h: StreamHandlers
) {
  let completed = false
  try {
    const token = import.meta.env.VITE_API_TOKEN as string | undefined
    const res = await fetch(`${import.meta.env.VITE_API_BASE ?? '/api'}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      body: JSON.stringify(opts),
      signal
    })
    if (!res.ok) throw await responseError(res)
    if (!res.body) throw new GenerationError('empty_stream', '生成服务没有返回数据流')

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const payload = frame.split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n')
        if (!payload) continue
        if (payload === '[DONE]') {
          if (!completed) throw new GenerationError('missing_done_event', '生成流缺少完成信息')
          return
        }
        const event = JSON.parse(payload) as Record<string, unknown>
        if (event.type === 'chunk' && typeof event.text === 'string') h.onChunk(event.text)
        else if (event.type === 'meta') h.onMeta?.(event as unknown as GenerationMeta)
        else if (event.type === 'done') {
          completed = true
          h.onDone?.(event)
        } else if (event.type === 'error') {
          throw new GenerationError(
            typeof event.code === 'string' ? event.code : 'stream_error',
            typeof event.message === 'string' ? event.message : '生成失败'
          )
        } else if (typeof event.node === 'number') h.onNode?.(event.node)
      }
      if (done) break
    }
    if (!completed) throw new GenerationError('incomplete_stream', '生成连接提前结束，请重试')
  } catch (error) {
    if (!signal.aborted) h.onError?.(error)
  }
}
