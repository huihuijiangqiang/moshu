import { request, requestResponse, USE_MOCK } from './http'
import { mockApi } from './mock'
import { getAccessToken } from './session'
import type { GenerateOptions, GenerationCoverageReport, GenerationDraftDecision, GenerationDraftDetail, GenerationDraftSummary, GenerationMeta, InlineGenerateOptions } from '@/types'

export interface GenerationPreview {
  chapterId: string
  projectId: string
  chapterTitle: string
  task: string
  targetWords: number
  model: { id: string; tier: string }
  provider: { source: 'platform' | 'user'; configId: string | null }
  tokenBudget: {
    total: number
    prompt: number
    context: number
    trimmedLayers: string[]
  }
  preflight: {
    status: 'ready' | 'attention' | 'blocked'
    blocking: boolean
    promptTokens: number
    budget: number
    warningCount: number
    checks: Array<{ id: string; status: string; message: string }>
  }
  skills: Array<{ id: string; version: string; category: string; priority: number }>
  scene: string
  coverage: GenerationCoverageReport
  layers: Array<{ key: string; tokens: number; items: Array<Record<string, unknown>>; content: string }>
  messages: Array<{ role: 'system' | 'user'; content: string }>
}

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

export async function previewGeneration(opts: GenerateOptions | InlineGenerateOptions): Promise<GenerationPreview> {
  if (USE_MOCK) {
    return {
      chapterId: opts.chapterId,
      projectId: 'mock-project',
      chapterTitle: '当前章节',
      task: 'chapter',
      targetWords: opts.targetWords,
      model: { id: 'mock', tier: opts.model },
      provider: { source: 'platform', configId: null },
      tokenBudget: { total: 25000, prompt: 0, context: 0, trimmedLayers: [] },
      preflight: { status: 'ready', blocking: false, promptTokens: 0, budget: 25000, warningCount: 0, checks: [] },
      skills: [],
      scene: 'general',
      coverage: {
        stage: 'prompt',
        blocking: false,
        status: 'ready',
        summary: { total: 0, confirmed: 0, attention: 0, message: '模拟模式没有可核对的生成依据。' },
        checks: []
      },
      layers: [],
      messages: [
        { role: 'system', content: '模拟模式不会调用模型。' },
        { role: 'user', content: '请切换到真实 API 后查看完整提示词。' }
      ]
    }
  }
  return request<GenerationPreview>('/generate/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts)
  })
}

export const generationDraftApi = {
  async list(chapterId: string): Promise<GenerationDraftSummary[]> {
    if (USE_MOCK) return []
    const result = await request<{ items: GenerationDraftSummary[] }>(
      `/generate/drafts?chapterId=${encodeURIComponent(chapterId)}`
    )
    return result.items
  },

  async get(id: string): Promise<GenerationDraftDetail> {
    if (USE_MOCK) throw new GenerationError('mock_draft_missing', '模拟模式没有已保存候选')
    return request<GenerationDraftDetail>(`/generate/drafts/${encodeURIComponent(id)}`)
  },

  async accept(id: string): Promise<GenerationDraftDetail> {
    return request<GenerationDraftDetail>(`/generate/drafts/${encodeURIComponent(id)}/accept`, { method: 'POST' })
  },

  async review(
    id: string,
    segmentIds: string[],
    decision: GenerationDraftDecision,
    baseVersion: number
  ): Promise<GenerationDraftDetail> {
    return request<GenerationDraftDetail>(`/generate/drafts/${encodeURIComponent(id)}/review`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ segmentIds, decision, baseVersion })
    })
  },

  async reject(id: string): Promise<void> {
    await requestResponse(`/generate/drafts/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },

  continue(id: string, options: {
    targetWords: number
    model?: GenerateOptions['model']
    useStyleProfile?: boolean
    dialogueDensity?: GenerateOptions['dialogueDensity']
    instruction?: string
  }, handlers: StreamHandlers): () => void {
    if (USE_MOCK) throw new GenerationError('continuation_not_available', '当前演示数据没有可继续的候选')
    return startStream(`/generate/drafts/${encodeURIComponent(id)}/continue`, options as GenerateOptions, handlers)
  }
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

export async function responseError(res: Response): Promise<GenerationError> {
  const text = await res.text()
  const statusCode = res.status === 401
    ? 'AUTH_REQUIRED'
    : res.status === 402
      ? 'INSUFFICIENT_CREDITS'
      : res.status === 409
        ? 'GENERATION_CONFLICT'
        : undefined
  try {
    const payload = JSON.parse(text) as {
      detail?: string | { code?: string; message?: string; required?: number; remaining?: number }
    }
    const detail = payload.detail
    if (typeof detail === 'object' && (detail?.code === 'INSUFFICIENT_CREDITS' || statusCode === 'INSUFFICIENT_CREDITS')) {
      return new GenerationError(
        detail.code ?? statusCode ?? 'INSUFFICIENT_CREDITS',
        `积分不足：本次最多需要 ${detail.required ?? 0}，当前剩余 ${detail.remaining ?? 0}。`,
        res.status
      )
    }
    const message = typeof detail === 'string' ? detail : detail?.message
    return new GenerationError(
      typeof detail === 'object' && detail?.code ? detail.code : statusCode ?? 'http_error',
      message || `生成请求失败（${res.status}）`,
      res.status
    )
  } catch {
    return new GenerationError(statusCode ?? 'http_error', text || `生成请求失败（${res.status}）`, res.status)
  }
}

export function normalizeGenerationError(error: unknown): GenerationError | Error {
  if (error instanceof GenerationError) return error
  if (error instanceof TypeError) return new GenerationError('network_error', '网络连接失败，请检查网络后重试。')
  return error instanceof Error ? error : new Error('生成失败，请重试')
}

async function sseStream(
  path: string,
  opts: GenerateOptions | InlineGenerateOptions,
  signal: AbortSignal,
  h: StreamHandlers
) {
  let completed = false
  try {
    const token = getAccessToken()
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
          window.dispatchEvent(new CustomEvent('moshu:usage-changed'))
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
    if (!signal.aborted) {
      h.onError?.(normalizeGenerationError(error))
    }
  }
}
