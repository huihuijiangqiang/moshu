import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'shelf', component: () => import('@/views/ShelfView.vue') },
    { path: '/write', name: 'workspace', component: () => import('@/views/WorkspaceView.vue') },
    { path: '/outline', name: 'outline', component: () => import('@/views/OutlineView.vue') },
    { path: '/codex', name: 'codex', component: () => import('@/views/CodexView.vue') },
    { path: '/guard', name: 'guard', component: () => import('@/views/GuardView.vue') },
    { path: '/style', name: 'style', component: () => import('@/views/StyleView.vue') },
    { path: '/ai-ratio', name: 'ai-ratio', component: () => import('@/views/AiRatioView.vue') },
    { path: '/export', name: 'export', component: () => import('@/views/ExportView.vue') },
    { path: '/usage', name: 'usage', component: () => import('@/views/UsageView.vue') },
    { path: '/wizard', name: 'wizard', component: () => import('@/views/WizardView.vue') },
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})
