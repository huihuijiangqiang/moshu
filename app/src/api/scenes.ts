import { delay, request, USE_MOCK } from './http'

export type SceneStatus = 'planning' | 'ready' | 'written' | 'needs_revision' | 'archived'
export type SceneBodyPolicy = 'plan_only' | 'mark_body_for_revision'

export interface ChapterScene {
  id: string
  chapterId: string
  order: number
  povEntryId?: string
  locationEntryId?: string
  goal: string
  obstacle: string
  turn: string
  infoGain: string
  emotionShift: string
  hook: string
  status: SceneStatus
  rev: number
  outlineRev: number
  bodyRev?: number
  archivedAt?: string
  createdAt: string
  updatedAt: string
}

export type ScenePatch = Partial<Pick<ChapterScene, 'povEntryId' | 'locationEntryId' | 'goal' | 'obstacle' | 'turn' | 'infoGain' | 'emotionShift' | 'hook' | 'status'>>

interface SceneDto {
  id: string; chapter_id: string; order: number; pov_entry_id: string | null; location_entry_id: string | null
  goal: string; obstacle: string; turn: string; info_gain: string; emotion_shift: string; hook: string; status: SceneStatus
  rev: number; outline_rev: number; body_rev: number | null; archived_at: string | null; created_at: string; updated_at: string
}

function fromDto(row: SceneDto): ChapterScene {
  return {
    id: row.id, chapterId: row.chapter_id, order: row.order, povEntryId: row.pov_entry_id ?? undefined, locationEntryId: row.location_entry_id ?? undefined,
    goal: row.goal ?? '', obstacle: row.obstacle ?? '', turn: row.turn ?? '', infoGain: row.info_gain ?? '', emotionShift: row.emotion_shift ?? '', hook: row.hook ?? '',
    status: row.status, rev: row.rev, outlineRev: row.outline_rev, bodyRev: row.body_rev ?? undefined, archivedAt: row.archived_at ?? undefined,
    createdAt: row.created_at, updatedAt: row.updated_at
  }
}

const mockScenes = new Map<string, ChapterScene[]>()
function mockList(chapterId: string) { return mockScenes.get(chapterId) ?? [] }
function mockScene(chapterId: string, order: number, input: ScenePatch): ChapterScene {
  const now = new Date().toISOString()
  return { id: `scene-${chapterId}-${Date.now().toString(36)}`, chapterId, order, goal: '', obstacle: '', turn: '', infoGain: '', emotionShift: '', hook: '', status: 'planning', rev: 1, outlineRev: 0, createdAt: now, updatedAt: now, ...input }
}

export const scenesApi = {
  async list(chapterId: string): Promise<ChapterScene[]> {
    if (USE_MOCK) { await delay(60); return structuredClone(mockList(chapterId).sort((a, b) => a.order - b.order)) }
    return (await request<SceneDto[]>(`/chapters/${chapterId}/scenes`)).map(fromDto)
  },
  async create(chapterId: string, input: ScenePatch & { order?: number; baseOutlineRev: number; baseBodyRev?: number; bodyPolicy?: SceneBodyPolicy }): Promise<ChapterScene> {
    if (USE_MOCK) { await delay(80); const items = mockList(chapterId); const item = mockScene(chapterId, input.order ?? items.length + 1, input); items.push(item); mockScenes.set(chapterId, items); return structuredClone(item) }
    const { baseOutlineRev, baseBodyRev, bodyPolicy, order, ...fields } = input
    return fromDto(await request<SceneDto>(`/chapters/${chapterId}/scenes`, { method: 'POST', body: JSON.stringify({ ...toDto(fields), order: order ?? 1, base_outline_rev: baseOutlineRev, base_body_rev: baseBodyRev ?? 0, body_policy: bodyPolicy }) }))
  },
  async update(sceneId: string, patch: ScenePatch & { expectedRev: number; baseOutlineRev: number; baseBodyRev?: number; bodyPolicy?: SceneBodyPolicy }): Promise<ChapterScene> {
    if (USE_MOCK) {
      await delay(80); const item = [...mockScenes.values()].flat().find((scene) => scene.id === sceneId); if (!item) throw new Error('scene_not_found'); if (item.rev !== patch.expectedRev) throw new Error('scene_revision_conflict');
      Object.assign(item, patch); delete (item as unknown as Record<string, unknown>).expectedRev; delete (item as unknown as Record<string, unknown>).baseOutlineRev; delete (item as unknown as Record<string, unknown>).baseBodyRev; delete (item as unknown as Record<string, unknown>).bodyPolicy; item.rev += 1; item.updatedAt = new Date().toISOString(); return structuredClone(item)
    }
    const { expectedRev, baseOutlineRev, baseBodyRev, bodyPolicy, ...fields } = patch
    return fromDto(await request<SceneDto>(`/scenes/${sceneId}`, { method: 'PATCH', body: JSON.stringify({ ...toDto(fields), expected_rev: expectedRev, base_outline_rev: baseOutlineRev, base_body_rev: baseBodyRev ?? 0, body_policy: bodyPolicy }) }))
  },
  async reorder(chapterId: string, sceneIds: string[], baseOutlineRev: number, baseBodyRev?: number, bodyPolicy?: SceneBodyPolicy): Promise<ChapterScene[]> {
    if (USE_MOCK) { await delay(80); const items = mockList(chapterId); sceneIds.forEach((id, index) => { const item = items.find((scene) => scene.id === id); if (item) item.order = index + 1 }); return structuredClone(items.sort((a, b) => a.order - b.order)) }
    return (await request<SceneDto[]>(`/chapters/${chapterId}/scenes/reorder`, { method: 'POST', body: JSON.stringify({ scene_ids: sceneIds, base_outline_rev: baseOutlineRev, base_body_rev: baseBodyRev ?? 0, body_policy: bodyPolicy }) })).map(fromDto)
  },
  async archive(scene: ChapterScene, baseOutlineRev: number, baseBodyRev?: number, bodyPolicy?: SceneBodyPolicy): Promise<ChapterScene> {
    if (USE_MOCK) { await delay(60); scene.status = 'archived'; scene.archivedAt = new Date().toISOString(); scene.rev += 1; return structuredClone(scene) }
    return fromDto(await request<SceneDto>(`/scenes/${scene.id}/archive`, { method: 'POST', body: JSON.stringify({ expected_rev: scene.rev, base_outline_rev: baseOutlineRev, base_body_rev: baseBodyRev ?? 0, body_policy: bodyPolicy }) }))
  }
}

function toDto(fields: Record<string, unknown>): Record<string, unknown> {
  const map: Record<string, string> = { povEntryId: 'pov_entry_id', locationEntryId: 'location_entry_id', infoGain: 'info_gain', emotionShift: 'emotion_shift' }
  return Object.fromEntries(Object.entries(fields).map(([key, value]) => [map[key] ?? key, value]))
}
