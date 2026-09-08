import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'
import { USE_MOCK } from '@/api/http'
import { getSessionUser, hasSession } from '@/api/session'

/** meta.bare = 不套 AppShell 的全屏页（登录、开书向导） */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'shelf', component: () => import('@/views/ShelfView.vue') },
    { path: '/deconstruct', name: 'deconstruct', component: () => import('@/views/DeconstructView.vue') },
    { path: '/projects/:projectId/write', name: 'workspace', component: () => import('@/views/WorkspaceView.vue'), meta: { scope: 'project', section: 'write' } },
    { path: '/projects/:projectId/outline', name: 'outline', component: () => import('@/views/OutlineView.vue'), meta: { scope: 'project', section: 'outline' } },
    { path: '/projects/:projectId/timeline', name: 'timeline', component: () => import('@/views/TimelineView.vue'), meta: { scope: 'project', section: 'timeline' } },
    { path: '/projects/:projectId/codex', name: 'codex', component: () => import('@/views/CodexView.vue'), meta: { scope: 'project', section: 'codex' } },
    { path: '/projects/:projectId/storyboard', name: 'storyboard', component: () => import('@/views/StoryboardView.vue'), meta: { scope: 'project', section: 'storyboard' } },
    { path: '/projects/:projectId/guard', name: 'guard', component: () => import('@/views/GuardView.vue'), meta: { scope: 'project', section: 'guard' } },
    { path: '/projects/:projectId/style', name: 'style', component: () => import('@/views/StyleView.vue'), meta: { scope: 'project', section: 'style' } },
    { path: '/projects/:projectId/ai-ratio', name: 'ai-ratio', component: () => import('@/views/AiRatioView.vue'), meta: { scope: 'project', section: 'ai-ratio' } },
    { path: '/projects/:projectId/tools', name: 'tools', component: () => import('@/views/ToolsView.vue'), meta: { scope: 'project', section: 'tools' } },
    { path: '/projects/:projectId/export', name: 'export', component: () => import('@/views/ExportView.vue'), meta: { scope: 'project', section: 'export' } },
    { path: '/projects/:projectId/access', name: 'access', component: () => import('@/views/AccessView.vue'), meta: { scope: 'project', section: 'access' } },
    { path: '/usage', name: 'usage', component: () => import('@/views/UsageView.vue') },
    { path: '/tasks', name: 'tasks', component: () => import('@/views/TasksView.vue') },
    { path: '/account/security', name: 'account-security', component: () => import('@/views/AccountSecurityView.vue') },
    { path: '/password-reset', name: 'password-reset', component: () => import('@/views/PasswordResetView.vue'), meta: { bare: true } },
    { path: '/agent', name: 'agent', component: () => import('@/views/AgentView.vue') },
    { path: '/teams', name: 'teams', component: () => import('@/views/TeamView.vue') },
    { path: '/model-settings', name: 'model-settings', component: () => import('@/views/ModelSettingsView.vue') },
    { path: '/admin', name: 'admin', component: () => import('@/views/AdminView.vue'), meta: { admin: true } },
    { path: '/projects/new', name: 'wizard', component: () => import('@/views/WizardView.vue'), meta: { bare: true } },
    { path: '/write', redirect: '/projects/p1/write' },
    { path: '/outline', redirect: '/projects/p1/outline' },
    { path: '/timeline', redirect: '/projects/p1/timeline' },
    { path: '/codex', redirect: '/projects/p1/codex' },
    { path: '/storyboard', redirect: '/projects/p1/storyboard' },
    { path: '/guard', redirect: '/projects/p1/guard' },
    { path: '/style', redirect: '/projects/p1/style' },
    { path: '/ai-ratio', redirect: '/projects/p1/ai-ratio' },
    { path: '/tools', redirect: '/projects/p1/tools' },
    { path: '/export', redirect: '/projects/p1/export' },
    { path: '/wizard', redirect: '/projects/new' },
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { bare: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export function authGuard(to: RouteLocationNormalized, useMock = USE_MOCK) {
  if (useMock) return true
  if (to.name !== 'login' && to.name !== 'password-reset' && !hasSession()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && hasSession()) return { name: 'shelf' }
  if (to.meta.admin === true && !['admin', 'super_admin'].includes(getSessionUser()?.system_role ?? 'user')) {
    return { name: 'shelf' }
  }
  return true
}

router.beforeEach((to) => authGuard(to))
