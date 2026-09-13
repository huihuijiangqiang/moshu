import { delay, request, USE_MOCK } from './http'
import type { StoryHook, StoryHookCreate, StoryHookPatch, StoryHookStatus } from '@/types'

interface StoryHookDto {
  id: string
  project_id: string
  source_chapter_id: string
  payoff_chapter_id: string | null
  hook_type: string
  concrete_event: string
  unresolved_question: string
  payoff_by_chapter: number | null
  status: StoryHookStatus
  novelty_signature: string
  resolution: string
  revision: number
  created_at: string
  updated_at: string
}

function fromDto(row: StoryHookDto): StoryHook {
  return {
    id: row.id,
    projectId: row.project_id,
    sourceChapterId: row.source_chapter_id,
    payoffChapterId: row.payoff_chapter_id ?? undefined,
    hookType: row.hook_type,
    concreteEvent: row.concrete_event,
    unresolvedQuestion: row.unresolved_question,
    payoffByChapter: row.payoff_by_chapter ?? undefined,
    status: row.status,
    noveltySignature: row.novelty_signature,
    resolution: row.resolution,
    revision: row.revision,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  }
}

const localHooks = new Map<string, StoryHook[]>()

function localList(projectId: string) {
  const hooks = localHooks.get(projectId) ?? []
  localHooks.set(projectId, hooks)
  return hooks
}

function cloneHooks(hooks: StoryHook[]) {
  return structuredClone(hooks)
}

export const storyHooksApi = {
  async list(projectId: string, statuses: StoryHookStatus[] = []): Promise<StoryHook[]> {
    if (USE_MOCK) {
      await delay(50)
      const hooks = localList(projectId)
      return cloneHooks(statuses.length ? hooks.filter((hook) => statuses.includes(hook.status)) : hooks)
    }
    const query = new URLSearchParams()
    statuses.forEach((status) => query.append('status', status))
    const suffix = query.size ? `?${query.toString()}` : ''
    return (await request<StoryHookDto[]>(`/projects/${projectId}/hooks${suffix}`)).map(fromDto)
  },

  async create(projectId: string, input: StoryHookCreate): Promise<StoryHook> {
    if (USE_MOCK) {
      await delay(70)
      const now = new Date().toISOString()
      const hook: StoryHook = {
        id: `hook-${Date.now().toString(36)}`,
        projectId,
        sourceChapterId: input.sourceChapterId,
        hookType: input.hookType,
        concreteEvent: input.concreteEvent,
        unresolvedQuestion: input.unresolvedQuestion,
        payoffByChapter: input.payoffByChapter,
        status: 'open',
        noveltySignature: '',
        resolution: '',
        revision: 1,
        createdAt: now,
        updatedAt: now
      }
      localList(projectId).push(hook)
      return structuredClone(hook)
    }
    return fromDto(await request<StoryHookDto>(`/projects/${projectId}/hooks`, {
      method: 'POST',
      body: JSON.stringify(input)
    }))
  },

  async update(projectId: string, hookId: string, patch: StoryHookPatch): Promise<StoryHook> {
    if (USE_MOCK) {
      await delay(70)
      const hook = localList(projectId).find((item) => item.id === hookId)
      if (!hook) throw new Error('story_hook_not_found')
      if (hook.revision !== patch.expectedRevision) throw new Error('story_hook_revision_conflict')
      const { expectedRevision: _expectedRevision, ...changes } = patch
      Object.assign(hook, changes, {
        payoffByChapter: changes.payoffByChapter === null ? undefined : changes.payoffByChapter ?? hook.payoffByChapter,
        payoffChapterId: changes.payoffChapterId === null ? undefined : changes.payoffChapterId ?? hook.payoffChapterId,
        revision: hook.revision + 1,
        updatedAt: new Date().toISOString()
      })
      return structuredClone(hook)
    }
    return fromDto(await request<StoryHookDto>(`/projects/${projectId}/hooks/${hookId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }))
  }
}
