<script setup lang="ts">
const actions = ['续写', '扩写', '润色', '改写语气', '按我的风格'] as const
const emit = defineEmits<{ run: [action: string] }>()
</script>

<template>
  <div class="ai-bar">
    <button
      v-for="(a, i) in actions"
      :key="a"
      type="button"
      :data-primary="i === 0"
      @click="emit('run', a)"
    >{{ a }}</button>
  </div>
</template>
