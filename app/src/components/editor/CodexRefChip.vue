<script setup lang="ts">
import { computed } from 'vue'
import { nodeViewProps, NodeViewWrapper } from '@tiptap/vue-3'
import { useCodexStore } from '@/stores/codex'

const props = defineProps(nodeViewProps)
const codex = useCodexStore()

const entry = computed(() => codex.byId.get(props.node.attrs.id as string))
const label = computed(() => entry.value?.name ?? '未知设定')
</script>

<template>
  <NodeViewWrapper
    as="span"
    class="codex-ref"
    :data-missing="!entry"
    :title="entry?.summary ?? '该条目已删除'"
  >{{ label }}</NodeViewWrapper>
</template>
