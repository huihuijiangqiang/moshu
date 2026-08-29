<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'

const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()

// 设定库与守卫在应用级预载：侧栏计数与 @ 引用在任意页面都要可用
onMounted(async () => {
  await Promise.all([project.load(), codex.load(), guard.load()])
  if (project.activeId) await project.openChapter(project.activeId)
})
</script>

<template>
  <RouterView v-slot="{ Component }">
    <KeepAlive :include="['WorkspaceView']">
      <component :is="Component" />
    </KeepAlive>
  </RouterView>
</template>
