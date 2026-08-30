<script setup lang="ts">
import { ICON_PATHS, type IconName } from './icons'

const props = defineProps<{ name: IconName; size?: number }>()
</script>

<template>
  <svg
    :width="props.size ?? 17"
    :height="props.size ?? 17"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    stroke-width="1.4"
    stroke-linecap="square"
    stroke-linejoin="miter"
    aria-hidden="true"
  >
    <path :d="ICON_PATHS[props.name]" />
  </svg>
</template>
