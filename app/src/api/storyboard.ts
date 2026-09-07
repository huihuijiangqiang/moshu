import { request } from './http'
import type {
  StoryboardAdaptation,
  StoryboardEpisode,
  StoryboardScene,
  StoryboardShot,
  VisualProfile
} from '@/types'

interface AdaptationDto {
  id: string
  project_id: string
  title: string
  format: 'comic_drama'
  aspect_ratio: '9:16' | '16:9'
  style_profile: Record<string, unknown>
  status: StoryboardAdaptation['status']
}

interface EpisodeDto {
  id: string
  adaptation_id: string
  number: number
  title: string
  source_chapter_ids: string[]
  target_duration: number
  status: StoryboardEpisode['status']
}

interface SceneDto {
  id: string
  episode_id: string
  order: number
  purpose: string
  location_entry_id: string | null
  time_anchor: string
  character_entry_ids: string[]
  summary: string
}

interface ShotDto {
  id: string
  scene_id: string
  order: number
  shot_type: StoryboardShot['shotType']
  camera: string
  duration_target: number
  action: string
  dialogue: string
  narration: string
  visual_prompt: string
  reference_asset_ids: string[]
  status: StoryboardShot['status']
}

interface VisualProfileDto {
  id: string
  adaptation_id: string
  codex_entry_id: string
  display_name: string
  style: string
  appearance: string
  costume: string
  palette: string[]
  reference_asset_ids: string[]
  version: number
  locked: boolean
  notes: string
}

function shotFromDto(row: ShotDto): StoryboardShot {
  return {
    id: row.id,
    sceneId: row.scene_id,
    order: row.order,
    shotType: row.shot_type,
    camera: row.camera,
    durationTarget: row.duration_target,
    action: row.action,
    dialogue: row.dialogue,
    narration: row.narration,
    visualPrompt: row.visual_prompt,
    referenceAssetIds: row.reference_asset_ids,
    status: row.status
  }
}

async function loadScene(scene: SceneDto): Promise<StoryboardScene> {
  const shots = await request<ShotDto[]>(`/scenes/${scene.id}/shots`)
  return {
    id: scene.id,
    episodeId: scene.episode_id,
    order: scene.order,
    purpose: scene.purpose,
    locationEntryId: scene.location_entry_id ?? undefined,
    timeAnchor: scene.time_anchor,
    characterEntryIds: scene.character_entry_ids,
    summary: scene.summary,
    shots: shots.map(shotFromDto)
  }
}

async function loadEpisode(episode: EpisodeDto): Promise<StoryboardEpisode> {
  const scenes = await request<SceneDto[]>(`/episodes/${episode.id}/scenes`)
  return {
    id: episode.id,
    adaptationId: episode.adaptation_id,
    number: episode.number,
    title: episode.title,
    sourceChapterIds: episode.source_chapter_ids,
    targetDuration: episode.target_duration,
    status: episode.status,
    scenes: await Promise.all(scenes.map(loadScene))
  }
}

function adaptationFromDto(row: AdaptationDto, episodes: StoryboardEpisode[], visualProfiles: VisualProfile[]): StoryboardAdaptation {
  const profile = row.style_profile ?? {}
  return {
    id: row.id,
    projectId: row.project_id,
    title: row.title,
    format: row.format,
    aspectRatio: row.aspect_ratio,
    styleProfile: {
      label: typeof profile.label === 'string' ? profile.label : '待设定画风',
      description: typeof profile.description === 'string' ? profile.description : ''
    },
    status: row.status,
    episodes,
    visualProfiles
  }
}

export const storyboardApi = {
  async getStoryboard(projectId: string): Promise<StoryboardAdaptation> {
    const adaptations = await request<AdaptationDto[]>(`/projects/${projectId}/adaptations`)
    let adaptation = adaptations[0]
    if (!adaptation) {
      adaptation = await request<AdaptationDto>(`/projects/${projectId}/adaptations`, {
        method: 'POST',
        body: JSON.stringify({ title: '漫剧改编', format: 'comic_drama', aspect_ratio: '9:16', style_profile: {} })
      })
    }
    const [episodeRows, profileRows] = await Promise.all([
      request<EpisodeDto[]>(`/adaptations/${adaptation.id}/episodes`),
      request<VisualProfileDto[]>(`/projects/${projectId}/adaptations/${adaptation.id}/visual-profiles`)
    ])
    const episodes = await Promise.all(episodeRows.map(loadEpisode))
    return adaptationFromDto(
      adaptation,
      episodes,
      profileRows.map((row) => ({
        id: row.id,
        adaptationId: row.adaptation_id,
        codexEntryId: row.codex_entry_id,
        displayName: row.display_name,
        style: row.style,
        appearance: row.appearance,
        costume: row.costume,
        palette: row.palette,
        referenceAssetIds: row.reference_asset_ids,
        version: row.version,
        locked: row.locked,
        notes: row.notes
      }))
    )
  },

  async createStoryboardEpisode(projectId: string, input: Pick<StoryboardEpisode, 'title' | 'sourceChapterIds' | 'targetDuration'>): Promise<StoryboardEpisode> {
    const adaptation = await this.getStoryboard(projectId)
    const row = await request<EpisodeDto>(`/adaptations/${adaptation.id}/episodes`, {
      method: 'POST',
      body: JSON.stringify({
        number: adaptation.episodes.length + 1,
        title: input.title,
        source_chapter_ids: input.sourceChapterIds,
        target_duration: input.targetDuration
      })
    })
    return {
      id: row.id,
      adaptationId: row.adaptation_id,
      number: row.number,
      title: row.title,
      sourceChapterIds: row.source_chapter_ids,
      targetDuration: row.target_duration,
      status: row.status,
      scenes: []
    }
  },

  async createStoryboardScene(_projectId: string, episodeId: string, input: Pick<StoryboardScene, 'purpose' | 'summary' | 'timeAnchor' | 'locationEntryId' | 'characterEntryIds'>): Promise<StoryboardScene> {
    const existing = await request<SceneDto[]>(`/episodes/${episodeId}/scenes`)
    const row = await request<SceneDto>(`/episodes/${episodeId}/scenes`, {
      method: 'POST',
      body: JSON.stringify({
        order: existing.length + 1,
        purpose: input.purpose,
        summary: input.summary,
        time_anchor: input.timeAnchor,
        location_entry_id: input.locationEntryId ?? null,
        character_entry_ids: input.characterEntryIds
      })
    })
    return { id: row.id, episodeId: row.episode_id, order: row.order, purpose: row.purpose, locationEntryId: row.location_entry_id ?? undefined, timeAnchor: row.time_anchor, characterEntryIds: row.character_entry_ids, summary: row.summary, shots: [] }
  },

  async createStoryboardShot(_projectId: string, sceneId: string, input: Partial<StoryboardShot>): Promise<StoryboardShot> {
    const existing = await request<ShotDto[]>(`/scenes/${sceneId}/shots`)
    const row = await request<ShotDto>(`/scenes/${sceneId}/shots`, {
      method: 'POST',
      body: JSON.stringify({
        order: existing.length + 1,
        shot_type: input.shotType ?? 'medium',
        camera: input.camera ?? 'static',
        duration_target: input.durationTarget ?? 4,
        action: input.action ?? '',
        dialogue: input.dialogue ?? '',
        narration: input.narration ?? '',
        visual_prompt: input.visualPrompt ?? '',
        reference_asset_ids: input.referenceAssetIds ?? []
      })
    })
    return shotFromDto(row)
  },

  async updateStoryboardShot(_projectId: string, shotId: string, patch: Partial<StoryboardShot>): Promise<StoryboardShot> {
    const body: Record<string, unknown> = {}
    if (patch.order !== undefined) body.order = patch.order
    if (patch.shotType !== undefined) body.shot_type = patch.shotType
    if (patch.camera !== undefined) body.camera = patch.camera
    if (patch.durationTarget !== undefined) body.duration_target = patch.durationTarget
    if (patch.action !== undefined) body.action = patch.action
    if (patch.dialogue !== undefined) body.dialogue = patch.dialogue
    if (patch.narration !== undefined) body.narration = patch.narration
    if (patch.visualPrompt !== undefined) body.visual_prompt = patch.visualPrompt
    if (patch.referenceAssetIds !== undefined) body.reference_asset_ids = patch.referenceAssetIds
    if (patch.status !== undefined) body.status = patch.status
    return shotFromDto(await request<ShotDto>(`/shots/${shotId}`, { method: 'PATCH', body: JSON.stringify(body) }))
  },

  async updateVisualProfile(_projectId: string, profileId: string, patch: Partial<VisualProfile>): Promise<VisualProfile> {
    const body: Record<string, unknown> = {}
    if (patch.displayName !== undefined) body.display_name = patch.displayName
    if (patch.style !== undefined) body.style = patch.style
    if (patch.appearance !== undefined) body.appearance = patch.appearance
    if (patch.costume !== undefined) body.costume = patch.costume
    if (patch.palette !== undefined) body.palette = patch.palette
    if (patch.referenceAssetIds !== undefined) body.reference_asset_ids = patch.referenceAssetIds
    if (patch.notes !== undefined) body.notes = patch.notes
    if (patch.locked !== undefined) body.locked = patch.locked
    const row = await request<VisualProfileDto>(`/visual-profiles/${profileId}`, { method: 'PATCH', body: JSON.stringify(body) })
    return {
      id: row.id,
      adaptationId: row.adaptation_id,
      codexEntryId: row.codex_entry_id,
      displayName: row.display_name,
      style: row.style,
      appearance: row.appearance,
      costume: row.costume,
      palette: row.palette,
      referenceAssetIds: row.reference_asset_ids,
      version: row.version,
      locked: row.locked,
      notes: row.notes
    }
  }
}
