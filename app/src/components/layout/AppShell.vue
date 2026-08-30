<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { IconName } from '@/components/ui/icons'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { useShellStore } from '@/stores/shell'

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

interface RailEntry {
  to: string
  icon: IconName
  label: string
  shortLabel: string
  dot?: boolean
}

const rail = computed<RailEntry[]>(() => [
  { to: '/', icon: 'shelf', label: '作品库', shortLabel: '作品' },
  { to: '/write', icon: 'write', label: '写作台', shortLabel: '正文' },
  { to: '/outline', icon: 'outline', label: '大纲', shortLabel: '大纲' },
  { to: '/codex', icon: 'codex', label: `设定库 · ${codex.entries.length}`, shortLabel: '设定', dot: codex.pending.length > 0 },
  { to: '/guard', icon: 'guard', label: `一致性守卫 · ${guard.open.length}`, shortLabel: '守卫', dot: guard.open.length > 0 },
  { to: '/style', icon: 'style', label: '风格档', shortLabel: '风格' },
  { to: '/ai-ratio', icon: 'ratio', label: 'AI 占比自查', shortLabel: 'AI 检测' }
])

const railFoot: RailEntry[] = [
  { to: '/export', icon: 'export', label: '导出', shortLabel: '导出' },
  { to: '/usage', icon: 'usage', label: '用量与计费', shortLabel: '用量' }
]

const library = computed(() => route.path === '/')

const goalPct = computed(() => {
  const p = project.project
  if (!p || !p.dailyGoal) return 0
  return Math.min(100, Math.round((p.dailyWords / p.dailyGoal) * 100))
})

const remaining = computed(() =>
  Math.max(0, (project.project?.dailyGoal ?? 0) - (project.project?.dailyWords ?? 0))
)

function isCurrent(to: string) {
  return to === '/' ? route.path === '/' : route.path.startsWith(to)
}
</script>

<template>
  <div class="shell">
    <nav class="rail" aria-label="主导航">
      <RouterLink to="/" class="rail-mark" :style="{ border: 0 }" aria-label="书架">墨</RouterLink>

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
      </div>
    </nav>

    <div class="shell-main" :data-library="library">
      <header v-if="!library" class="topbar">
        <span class="topbar-title">{{ project.project?.title ?? '墨枢' }}</span>
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
          <span class="topbar-sep" />
          <span :style="{ color: 'var(--chrome-ink-dim)', whiteSpace: 'nowrap' }">
            今日 <b :style="{ color: 'var(--chrome-ink)' }">{{ (project.project?.dailyWords ?? 0).toLocaleString() }}</b>
            / {{ (project.project?.dailyGoal ?? 0).toLocaleString() }}
          </span>
          <span class="meter" :data-hit="goalPct >= 100" role="img" :aria-label="`今日目标完成 ${goalPct}%`">
            <span :style="{ width: goalPct + '%' }" />
          </span>
          <span class="avatar" aria-hidden="true">沈</span>
        </div>
      </header>

      <div class="shell-body">
        <slot />
      </div>

      <footer v-if="!library" class="statusbar">
        <span>{{ project.chapters.length }} 章</span>
        <span>{{ (project.totalWords / 10000).toFixed(1) }} 万字</span>
        <span>设定 {{ codex.entries.length }}</span>
        <span v-if="codex.pending.length">待确认 {{ codex.pending.length }}</span>
        <span v-if="guard.open.length" class="statusbar-alert">
          <span class="dot" /> 守卫 {{ guard.open.length }} 条待处理
        </span>
        <span v-else>守卫无告警</span>
        <span id="statusbar-slot" class="row" :style="{ gap: 'var(--u4)' }" />
        <span class="statusbar-push">距今日目标 {{ remaining.toLocaleString() }} 字</span>
        <span>积分 <b>2,840</b> / 5,000</span>
      </footer>
    </div>
  </div>
</template>
