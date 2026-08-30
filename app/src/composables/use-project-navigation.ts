import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { projectPath, routeProjectId, type ProjectSection } from '@/router/project-route'

export function useProjectNavigation() {
  const route = useRoute()
  const projectId = computed(() => routeProjectId(route) ?? 'p1')
  const inProject = computed(() => route.meta.scope === 'project')
  const toProject = (section: ProjectSection) => projectPath(projectId.value, section)

  return { projectId, inProject, toProject }
}
