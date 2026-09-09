<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { IconName } from '@/components/ui/icons'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { useShellStore } from '@/stores/shell'
import { useUsageStore } from '@/stores/usage'
import { useOrgStore } from '@/stores/orgs'
import { projectPath, routeProjectId } from '@/router/project-route'
import { authApi } from '@/api/auth'
import { getSessionUser } from '@/api/session'

/**
 * 全局外壳：图标轨 + 顶栏 + 内容槽 + 底部状态条。
 * 挂在 App.vue 上，各屏只填 body —— 旧版每个视图各自声明一遍三栏 grid，
 * 全局导航塞在 88 章列表下面滚不到，根源就是缺这一层。
 *
 * 各屏往顶栏加按钮的方式：Teleport 到 #topbar-actions。
 * 面包屑：shell.setCrumb()。两者都不需要外壳知道具体是哪一屏。
 */
const route = useRoute()
const router = useRouter()
const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const shell = useShellStore()
const usage = useUsageStore()
const orgs = useOrgStore()
const sessionUser = getSessionUser()
const isSystemAdmin = ['admin', 'super_admin'].includes(sessionUser?.system_role ?? 'user')

interface RailEntry {
  to: string
  icon: IconName
  label: string
  shortLabel: string
  dot?: boolean
}

const projectId = computed(() => routeProjectId(route))
const inProject = computed(() => route.meta.scope === 'project' && !!projectId.value)

const rail = computed<RailEntry[]>(() => {
  const entries: RailEntry[] = [{ to: '/workspace', icon: 'shelf', label: '作品库', shortLabel: '作品' }]
  if (!projectId.value) {
    entries.push({ to: '/deconstruct', icon: 'outline', label: '拆书分析', shortLabel: '拆书' })
    entries.push({ to: '/teams', icon: 'team', label: orgs.teamLabel, shortLabel: '团队' })
    entries.push({ to: '/usage', icon: 'usage', label: '用量与计费', shortLabel: '用量' })
    entries.push({ to: '/tasks', icon: 'history', label: '任务中心', shortLabel: '任务' })
    entries.push({ to: '/agent', icon: 'agent', label: 'AI 助手 · 受控任务', shortLabel: '助手' })
    entries.push({ to: '/model-settings', icon: 'key', label: '我的模型服务', shortLabel: '模型' })
    entries.push({ to: '/account/security', icon: 'key', label: '账户安全', shortLabel: '安全' })
    if (isSystemAdmin) entries.push({ to: '/admin', icon: 'guard', label: '系统管理', shortLabel: '系统' })
    return entries
  }
  return entries.concat([
    { to: projectPath(projectId.value, 'write'), icon: 'write', label: '写作台', shortLabel: '正文' },
    { to: projectPath(projectId.value, 'outline'), icon: 'outline', label: '大纲', shortLabel: '大纲' },
    { to: projectPath(projectId.value, 'timeline'), icon: 'timeline', label: '故事时间线', shortLabel: '时间' },
    { to: projectPath(projectId.value, 'storyboard'), icon: 'storyboard', label: '漫剧分镜', shortLabel: '漫剧' },
    { to: projectPath(projectId.value, 'codex'), icon: 'codex', label: `设定库 · ${codex.entries.length}`, shortLabel: '设定', dot: codex.pending.length > 0 },
    { to: projectPath(projectId.value, 'guard'), icon: 'guard', label: `一致性守卫 · ${guard.open.length}`, shortLabel: '守卫', dot: guard.open.length > 0 },
    { to: projectPath(projectId.value, 'style'), icon: 'style', label: '风格档', shortLabel: '风格' },
    { to: projectPath(projectId.value, 'ai-ratio'), icon: 'ratio', label: 'AI 来源账本', shortLabel: 'AI 来源' },
    { to: projectPath(projectId.value, 'tools'), icon: 'tools', label: '写作工具箱', shortLabel: '工具' }
  ])
})

const railFoot = computed<RailEntry[]>(() => projectId.value ? [
  { to: '/deconstruct', icon: 'outline', label: '拆书分析', shortLabel: '拆书' },
  { to: projectPath(projectId.value, 'export'), icon: 'export', label: '导出', shortLabel: '导出' },
  { to: projectPath(projectId.value, 'access'), icon: 'team', label: orgs.teamLabel, shortLabel: '团队' },
  { to: '/usage', icon: 'usage', label: '用量与计费', shortLabel: '用量' },
  { to: '/tasks', icon: 'history', label: '任务中心', shortLabel: '任务' },
  { to: '/agent', icon: 'agent', label: 'AI 助手 · 受控任务', shortLabel: '助手' },
  { to: '/model-settings', icon: 'key', label: '我的模型服务', shortLabel: '模型' },
  { to: '/account/security', icon: 'key', label: '账户安全', shortLabel: '安全' },
  ...(isSystemAdmin ? [{ to: '/admin', icon: 'guard' as IconName, label: '系统管理', shortLabel: '系统' }] : [])
] : [])

const library = computed(() => route.name === 'shelf')

const goalPct = computed(() => {
  const p = project.project
  if (!p || !p.dailyGoal) return 0
  return Math.min(100, Math.round((p.dailyWords / p.dailyGoal) * 100))
})

const remaining = computed(() =>
  Math.max(0, (project.project?.dailyGoal ?? 0) - (project.project?.dailyWords ?? 0))
)

const refreshUsage = () => { void usage.load(true).catch(() => undefined) }
onMounted(() => {
  void usage.load().catch(() => undefined)
  void orgs.load().catch(() => undefined)
  window.addEventListener('moshu:usage-changed', refreshUsage)
})
onUnmounted(() => window.removeEventListener('moshu:usage-changed', refreshUsage))

function isCurrent(to: string) {
  return route.path === to || route.path.startsWith(to + '/')
}

async function signOut() {
  try {
    await authApi.logout()
  } finally {
    await router.replace({ name: 'login' })
  }
}
</script>

<template>
  <div class="shell">
    <nav class="rail" aria-label="主导航">
      <RouterLink to="/workspace" class="rail-mark" :style="{ border: 0 }" aria-label="书架">
        <img src="/brand/moshu-icon.svg" alt="" />
      </RouterLink>

      <div class="rail-nav">
        <button
          v-for="r in rail"
          :key="r.to"
          class="rail-item"
          type="button"
          :aria-current="isCurrent(r.to)"
          :aria-label="r.label"
          @click="router.push(r.to)"
        >
          <AppIcon :name="r.icon" />
          <span v-if="r.dot" class="rail-dot" />
          <span class="rail-label">{{ r.shortLabel }}</span>
          <span class="rail-tip">{{ r.label }}</span>
        </button>
      </div>

      <div class="rail-foot">
        <button
          v-for="r in railFoot"
          :key="r.to"
          class="rail-item"
          type="button"
          :aria-current="isCurrent(r.to)"
          :aria-label="r.label"
          @click="router.push(r.to)"
        >
          <AppIcon :name="r.icon" />
          <span class="rail-label">{{ r.shortLabel }}</span>
          <span class="rail-tip">{{ r.label }}</span>
        </button>
        <button class="rail-item" type="button" aria-label="退出登录" @click="signOut">
          <AppIcon name="collapse" />
          <span class="rail-label">退出</span>
          <span class="rail-tip">退出登录</span>
        </button>
      </div>
    </nav>

    <div class="shell-main" :data-library="library" :data-project="inProject">
      <header v-if="!library" class="topbar">
        <span class="topbar-title">{{ inProject ? (project.project?.title ?? '加载作品…') : route.name === 'tasks' ? '任务中心' : '账户与用量' }}</span>
        <template v-if="shell.crumb">
          <span class="topbar-sep" />
          <span class="topbar-crumb">{{ shell.crumb }}</span>
        </template>

        <div id="topbar-actions" class="topbar-right">
          <!-- 各屏 Teleport 的落点，必须排在通用控件前面 -->
        </div>

        <div class="topbar-right" :style="{ marginLeft: 0 }">
          <button class="topbar-btn" type="button" title="命令面板 ⌘K" @click="shell.paletteOpen = true">
            <AppIcon name="search" :size="14" />
            <span class="kbd">⌘K</span>
          </button>
          <template v-if="inProject">
            <span class="topbar-sep" />
            <span :style="{ color: 'var(--chrome-ink-dim)', whiteSpace: 'nowrap' }">
              今日 <b :style="{ color: 'var(--chrome-ink)' }">{{ (project.project?.dailyWords ?? 0).toLocaleString() }}</b>
              / {{ (project.project?.dailyGoal ?? 0).toLocaleString() }}
            </span>
            <span class="meter" :data-hit="goalPct >= 100" role="img" :aria-label="`今日目标完成 ${goalPct}%`">
              <span :style="{ width: goalPct + '%' }" />
            </span>
          </template>
          <span class="avatar" :title="sessionUser?.name ?? '当前账号'">{{ (sessionUser?.name ?? '用').slice(0, 1) }}</span>
        </div>
      </header>

      <div class="shell-body">
        <slot />
      </div>

      <footer v-if="inProject" class="statusbar">
        <span>{{ project.totalChapters }} 章</span>
        <span>{{ (project.totalWords / 10000).toFixed(1) }} 万字</span>
        <span>设定 {{ codex.entries.length }}</span>
        <span v-if="codex.pending.length">待确认 {{ codex.pending.length }}</span>
        <span v-if="guard.open.length" class="statusbar-alert">
          <span class="dot" /> 守卫 {{ guard.open.length }} 条待处理
        </span>
        <span v-else>守卫无告警</span>
        <span id="statusbar-slot" class="row" :style="{ gap: 'var(--u4)' }" />
        <span class="statusbar-push">距今日目标 {{ remaining.toLocaleString() }} 字</span>
        <span>积分 <b>{{ usage.remaining.toLocaleString() }}</b> / {{ usage.quota.toLocaleString() }}</span>
      </footer>
    </div>
  </div>
</template>
