<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import AppShell from '@/components/layout/AppShell.vue'
import CommandPalette from '@/components/layout/CommandPalette.vue'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const router = useRouter()
const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const shell = useShellStore()

/** 登录与开书向导是全屏的，不套外壳；其余每一屏都继承图标轨与状态条 */
const bare = computed(() => route.meta.bare === true)

// 设定库与守卫在应用级预载：图标轨计数、⌘K 搜索、@ 引用在任意页面都要可用
shell.initTheme()

const onUnauthorized = () => {
  void router.replace({ name: 'login', query: { redirect: route.fullPath } })
}
onMounted(() => window.addEventListener('moshu:unauthorized', onUnauthorized))
onUnmounted(() => window.removeEventListener('moshu:unauthorized', onUnauthorized))

watch(
  () => route.params.projectId,
  async (value) => {
    if (typeof value !== 'string' || route.meta.scope !== 'project') return
    // WorkspaceView consumes the chapter query after it positions the
    // editor, so capture the deep-link target before any async preload can
    // let that query disappear.
    const requestedChapterId = typeof route.query.chapter === 'string' ? route.query.chapter : null
    const codexLoad = codex.load(value)
    const guardLoad = guard.load(value)
    try {
      // The editor only needs the project structure and active body for first paint.
      // Sidebar counts and guard data continue in the background.
      await project.load(value)
      // A deep link may target a specific chapter (new-book creation does
      // this for the first chapter). Respect it so the app-level preload does
      // not race WorkspaceView and replace the requested body with the last
      // chapter chosen by the project list.
      const requestedChapter = requestedChapterId && project.chapters.some((chapter) => chapter.id === requestedChapterId)
        ? requestedChapterId
        : project.activeId
      if (requestedChapter) await project.openChapter(requestedChapter)
      await Promise.allSettled([codexLoad, guardLoad])
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        await router.replace({ name: 'shelf' })
        return
      }
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorized()
        return
      }
      console.error('加载作品失败', error)
    }
  },
  { immediate: true }
)

</script>

<template>
  <RouterView v-slot="{ Component }">
    <template v-if="bare">
      <component :is="Component" :key="`bare:${String(route.name)}`" />
    </template>
    <AppShell v-else key="app-shell">
      <KeepAlive :include="['WorkspaceView']">
        <component :is="Component" :key="String(route.name)" />
      </KeepAlive>
    </AppShell>
  </RouterView>

  <CommandPalette />
</template>
