<script setup lang="ts">
import { suggestState } from '@/editor/extensions/codex-suggestion'
import { CODEX_KIND_LABEL } from '@/types'
</script>

<template>
  <div
    v-if="suggestState.open"
    class="suggest"
    :style="{ left: suggestState.rect.left + 'px', top: suggestState.rect.top + 'px' }"
  >
    <div
      v-for="(item, i) in suggestState.items"
      :key="item.id"
      class="suggest-item"
      :aria-selected="i === suggestState.index"
      @mousedown.prevent="suggestState.select(i)"
      @mouseenter="suggestState.index = i"
    >
      <span>{{ item.name }}</span>
      <span :style="{ opacity: 0.7 }">{{ CODEX_KIND_LABEL[item.kind] }}</span>
    </div>
  </div>
</template>
