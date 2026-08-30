<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import CommandPalette from '@/components/layout/CommandPalette.vue'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const shell = useShellStore()

/** 登录与开书向导是全屏的，不套外壳；其余每一屏都继承图标轨与状态条 */
const bare = computed(() => route.meta.bare === true)

// 设定库与守卫在应用级预载：图标轨计数、⌘K 搜索、@ 引用在任意页面都要可用
onMounted(async () => {
  shell.initTheme()
  await Promise.all([project.load(), codex.load(), guard.load()])
  if (project.activeId) await project.openChapter(project.activeId)
})
</script>

<template>
  <RouterView v-slot="{ Component }">
    <template v-if="bare">
      <component :is="Component" />
    </template>
    <AppShell v-else>
      <KeepAlive :include="['WorkspaceView']">
        <component :is="Component" />
      </KeepAlive>
    </AppShell>
  </RouterView>

  <CommandPalette />
</template>
