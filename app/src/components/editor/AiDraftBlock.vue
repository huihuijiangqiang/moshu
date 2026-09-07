<script setup lang="ts">
import { computed } from 'vue'
import { nodeViewProps, NodeViewContent, NodeViewWrapper } from '@tiptap/vue-3'
import type { DraftStatus } from '@/editor/extensions/AiDraft'

const props = defineProps(nodeViewProps)

const status = computed(() => props.node.attrs.status as DraftStatus)
const labelText = computed(() =>
  status.value === 'streaming' ? '正在写入' : status.value === 'locked' ? '已锁定' : '待采纳'
)

// TipTap 3 的 getPos() 在节点已从文档移除时返回 undefined，
// 例如流式写入过程中用户手动删掉了草稿块。此时不能把 undefined 传给命令。
function accept() {
  const pos = props.getPos()
  if (pos === undefined) return
  if (typeof props.node.attrs.draftId === 'string') {
    window.dispatchEvent(new CustomEvent('moshu:draft-action', {
      detail: { action: 'accept', draftId: props.node.attrs.draftId }
    }))
    return
  }
  props.editor.chain().focus().acceptDraftAt(pos).run()
}
function reject() {
  const pos = props.getPos()
  if (pos === undefined) return
  if (typeof props.node.attrs.draftId === 'string') {
    window.dispatchEvent(new CustomEvent('moshu:draft-action', {
      detail: { action: 'reject', draftId: props.node.attrs.draftId }
    }))
    return
  }
  props.editor.chain().focus().rejectDraftAt(pos).run()
}
function toggleLock() {
  props.updateAttributes({ status: status.value === 'locked' ? 'pending' : 'locked' })
}
</script>

<template>
  <NodeViewWrapper class="ai-draft" :data-status="status">
    <div class="ai-draft-bar" contenteditable="false">
      <span class="ai-draft-label" aria-live="polite">{{ labelText }}</span>
      <span v-if="status === 'streaming'" class="muted" aria-live="polite">生成中，可随时停止</span>
      <span v-else class="row" style="gap: 8px">
        <button class="chip chip-strong" type="button" @click="accept">采纳</button>
        <button class="chip" type="button" @click="reject">丢弃</button>
        <button class="chip" type="button" @click="toggleLock">
          {{ status === 'locked' ? '解锁' : '锁定' }}
        </button>
      </span>
    </div>
    <NodeViewContent />
  </NodeViewWrapper>
</template>
