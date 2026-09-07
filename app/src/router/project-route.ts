import type { RouteLocationNormalizedLoaded } from 'vue-router'

export type ProjectSection = 'write' | 'outline' | 'timeline' | 'codex' | 'storyboard' | 'guard' | 'style' | 'ai-ratio' | 'tools' | 'export' | 'access'

export function projectPath(projectId: string, section: ProjectSection = 'write') {
  return `/projects/${encodeURIComponent(projectId)}/${section}`
}

export function routeProjectId(route: RouteLocationNormalizedLoaded) {
  const value = route.params.projectId
  return typeof value === 'string' && value ? value : null
}
