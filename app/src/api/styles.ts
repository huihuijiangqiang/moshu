import { USE_MOCK, delay, request } from './http'

export type StyleStatus = 'pending' | 'processing' | 'ready' | 'failed'
export type StyleConfidence = 'insufficient' | 'low' | 'standard'

export interface StyleDimension {
  title: string
  score: number
  summary: string
  traits: string[]
  avoid: string[]
}

export interface StyleProfile {
  id: string
  name: string
  sampleWords: number
  isDefault: boolean
  dimensions: Record<string, StyleDimension>
  status: StyleStatus
  confidence: StyleConfidence
  errorDetail?: string
  extractedAt?: string
  createdAt: string
  updatedAt: string
  boundProjectIds: string[]
}

interface StyleProfileDto {
  id: string
  name: string
  sample_words: number
  is_default: boolean
  dimensions: Record<string, StyleDimension>
  status: StyleStatus
  confidence: StyleConfidence
  error_detail: string | null
  extracted_at: string | null
  created_at: string
  updated_at: string
  bound_project_ids: string[]
}

function fromDto(dto: StyleProfileDto): StyleProfile {
  return {
    id: dto.id,
    name: dto.name,
    sampleWords: dto.sample_words,
    isDefault: dto.is_default,
    dimensions: dto.dimensions,
    status: dto.status,
    confidence: dto.confidence,
    errorDetail: dto.error_detail ?? undefined,
    extractedAt: dto.extracted_at ?? undefined,
    createdAt: dto.created_at,
    updatedAt: dto.updated_at,
    boundProjectIds: dto.bound_project_ids
  }
}

const demoDimensions: Record<string, StyleDimension> = {
  sentence_rhythm: { title: '句式与节奏', score: 78, summary: '短句承担动作，中句交代环境，段尾常用单句停顿。', traits: ['短句推进', '段尾留白'], avoid: ['连续长句'] },
  dialogue: { title: '对白习惯', score: 64, summary: '对白直接，较少使用情绪副词，人物态度由动作承接。', traits: ['动作承接'], avoid: ['解释性对白'] },
  description_density: { title: '描写密度', score: 42, summary: '描写克制，优先选择能推进劳作和生活节奏的细节。', traits: ['有效细节'], avoid: ['堆叠形容词'] },
  imagery: { title: '意象与感官', score: 71, summary: '偏向泥土、炊烟、木器等可触摸的日常意象。', traits: ['触觉', '烟火气'], avoid: ['空泛宏大意象'] },
  chapter_hooks: { title: '章末钩子', score: 83, summary: '以未落定的选择或新出现的生活难题收束章节。', traits: ['行动悬念'], avoid: ['旁白预告'] },
  recurring_language: { title: '惯用与禁用表达', score: 55, summary: '动词具体，少用感叹号，不重复样文中的标志性原句。', traits: ['具体动词'], avoid: ['网络套话', '感叹号'] }
}

let demoProfiles: StyleProfile[] = [{
  id: 'style_demo',
  name: '田园白描',
  sampleWords: 52100,
  isDefault: true,
  dimensions: demoDimensions,
  status: 'ready',
  confidence: 'standard',
  extractedAt: new Date().toISOString(),
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  boundProjectIds: []
}]

const mockStylesApi = {
  async list() { await delay(); return structuredClone(demoProfiles) },
  async create(input: { name: string; sampleText: string; isDefault?: boolean }) {
    await delay()
    const now = new Date().toISOString()
    if (input.isDefault) demoProfiles.forEach((item) => { item.isDefault = false })
    const profile: StyleProfile = {
      id: crypto.randomUUID(), name: input.name, sampleWords: input.sampleText.replace(/\s/g, '').length,
      isDefault: input.isDefault || demoProfiles.length === 0, dimensions: {}, status: 'pending',
      confidence: input.sampleText.length >= 50000 ? 'standard' : input.sampleText.length >= 5000 ? 'low' : 'insufficient',
      createdAt: now, updatedAt: now, boundProjectIds: []
    }
    demoProfiles.unshift(profile)
    return structuredClone(profile)
  },
  async update(id: string, input: { name?: string; sampleText?: string; isDefault?: boolean }) {
    await delay()
    const profile = demoProfiles.find((item) => item.id === id)
    if (!profile) throw new Error('style_profile_not_found')
    if (input.name) profile.name = input.name
    if (input.sampleText) {
      profile.sampleWords = input.sampleText.replace(/\s/g, '').length
      profile.status = 'pending'; profile.dimensions = {}; profile.extractedAt = undefined
    }
    if (input.isDefault) { demoProfiles.forEach((item) => { item.isDefault = item.id === id }) }
    profile.updatedAt = new Date().toISOString()
    return structuredClone(profile)
  },
  async extract(id: string) {
    await delay(500)
    const profile = demoProfiles.find((item) => item.id === id)
    if (!profile) throw new Error('style_profile_not_found')
    profile.status = 'ready'; profile.dimensions = demoDimensions; profile.extractedAt = new Date().toISOString()
    return structuredClone(profile)
  },
  async remove(id: string) { await delay(); demoProfiles = demoProfiles.filter((item) => item.id !== id) },
  async bind(projectId: string, styleProfileId: string | null) {
    await delay()
    demoProfiles.forEach((item) => {
      item.boundProjectIds = item.boundProjectIds.filter((id) => id !== projectId)
      if (item.id === styleProfileId) item.boundProjectIds.push(projectId)
    })
    return { projectId, styleProfileId }
  }
}

const realStylesApi = {
  async list(): Promise<StyleProfile[]> {
    return (await request<StyleProfileDto[]>('/styles')).map(fromDto)
  },
  async create(input: { name: string; sampleText: string; isDefault?: boolean }): Promise<StyleProfile> {
    return fromDto(await request<StyleProfileDto>('/styles', {
      method: 'POST',
      body: JSON.stringify({ name: input.name, sample_text: input.sampleText, is_default: input.isDefault ?? false })
    }))
  },
  async update(id: string, input: { name?: string; sampleText?: string; isDefault?: boolean }): Promise<StyleProfile> {
    return fromDto(await request<StyleProfileDto>(`/styles/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        ...(input.name !== undefined ? { name: input.name } : {}),
        ...(input.sampleText !== undefined ? { sample_text: input.sampleText } : {}),
        ...(input.isDefault !== undefined ? { is_default: input.isDefault } : {})
      })
    }))
  },
  async extract(id: string): Promise<StyleProfile> {
    return fromDto(await request<StyleProfileDto>(`/styles/${id}/extract`, { method: 'POST' }))
  },
  async remove(id: string): Promise<void> {
    await request(`/styles/${id}`, { method: 'DELETE' })
  },
  async bind(projectId: string, styleProfileId: string | null): Promise<{ projectId: string; styleProfileId: string | null }> {
    const result = await request<{ project_id: string; style_profile_id: string | null }>(`/projects/${projectId}/style-profile`, {
      method: 'PUT',
      body: JSON.stringify({ style_profile_id: styleProfileId })
    })
    return { projectId: result.project_id, styleProfileId: result.style_profile_id }
  }
}

export const stylesApi = USE_MOCK ? mockStylesApi : realStylesApi
