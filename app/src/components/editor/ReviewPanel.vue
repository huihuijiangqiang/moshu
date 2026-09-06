<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import type { ReviewAnchor, ReviewComment, ReviewRound, ReviewWorkspace } from '@/types'

const props = defineProps<{
  workspace?: ReviewWorkspace
  anchor?: ReviewAnchor
  loading?: boolean
  busy?: boolean
  error?: string
  submitDisabledReason?: string
}>()

const emit = defineEmits<{
  refresh: []
  submit: [note: string]
  addComment: [roundId: string, content: string]
  updateComment: [roundId: string, comment: ReviewComment, content: string]
  resolveComment: [roundId: string, comment: ReviewComment]
  decide: [round: ReviewRound, decision: 'approved' | 'changes_requested', note: string]
  locate: [comment: ReviewComment]
}>()

const selectedRoundId = ref('')
const submitNote = ref('')
const commentText = ref('')
const decisionNote = ref('')
const editingCommentId = ref('')
const editingText = ref('')

watch(
  () => [props.workspace?.chapterId, props.workspace?.rounds.map((round) => round.id).join(',')] as const,
  () => {
    const rows = props.workspace?.rounds ?? []
    if (!rows.some((round) => round.id === selectedRoundId.value)) selectedRoundId.value = rows[0]?.id ?? ''
    editingCommentId.value = ''
    editingText.value = ''
  },
  { immediate: true }
)

const selectedRound = computed(() => props.workspace?.rounds.find((round) => round.id === selectedRoundId.value))
const openComments = computed(() => selectedRound.value?.comments.filter((comment) => comment.status === 'open') ?? [])
const maxSubmittedRevision = computed(() => Math.max(0, ...(props.workspace?.rounds.map((round) => round.submittedBodyRevision) ?? [])))
const canSubmitRevision = computed(() => Boolean(
  props.workspace?.canSubmit
  && props.workspace.currentBodyRevision > maxSubmittedRevision.value
  && !props.submitDisabledReason
))

const statusLabels: Record<ReviewRound['status'], string> = {
  submitted: '待审',
  changes_requested: '退回修改',
  approved: '已通过',
  superseded: '已换版'
}

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'
  })
}

function beginEdit(comment: ReviewComment) {
  editingCommentId.value = comment.id
  editingText.value = comment.content
}

function saveEdit(comment: ReviewComment) {
  const content = editingText.value.trim()
  if (!selectedRound.value || !content) return
  emit('updateComment', selectedRound.value.id, comment, content)
  editingCommentId.value = ''
}

function addComment() {
  const content = commentText.value.trim()
  if (!selectedRound.value || !content || !props.anchor) return
  emit('addComment', selectedRound.value.id, content)
  commentText.value = ''
}
</script>

<template>
  <div class="review-panel">
    <div class="wk-head review-head">
      <span>章节审稿</span>
      <span v-if="workspace" class="wk-head-push">正文 v{{ workspace.currentBodyRevision }}</span>
      <button class="review-icon-button" type="button" title="刷新审稿状态" aria-label="刷新审稿状态" :disabled="loading" @click="emit('refresh')">
        <AppIcon name="restore" :size="14" />
      </button>
    </div>

    <p v-if="loading" class="review-message">正在读取审稿记录…</p>
    <p v-else-if="error" class="review-message review-message-error">{{ error }}</p>

    <template v-if="workspace && !loading">
      <section v-if="workspace.canSubmit && canSubmitRevision" class="review-submit">
        <label class="wk-label" for="review-submit-note">提交第 {{ workspace.currentBodyRevision }} 版</label>
        <textarea id="review-submit-note" v-model="submitNote" class="wk-input" rows="2" placeholder="本次改动摘要（可选）" />
        <button class="wk-btn wk-btn-block" data-primary="true" type="button" :disabled="busy" @click="emit('submit', submitNote)">提交审稿</button>
      </section>

      <p v-else-if="submitDisabledReason" class="review-disabled-reason">{{ submitDisabledReason }}</p>

      <div v-if="workspace.rounds.length" class="review-round-picker">
        <label for="review-round">审稿轮次</label>
        <select id="review-round" v-model="selectedRoundId" class="wk-input">
          <option v-for="round in workspace.rounds" :key="round.id" :value="round.id">
            第 {{ round.submittedBodyRevision }} 版 · {{ statusLabels[round.status] }}
          </option>
        </select>
      </div>

      <template v-if="selectedRound">
        <section class="review-round-summary" :data-status="selectedRound.status">
          <div class="row-between">
            <strong>正文第 {{ selectedRound.submittedBodyRevision }} 版</strong>
            <span class="review-status">{{ statusLabels[selectedRound.status] }}</span>
          </div>
          <p>{{ selectedRound.submittedByName || '作者' }} · {{ formatTime(selectedRound.submittedAt) }}</p>
          <blockquote v-if="selectedRound.submitNote">{{ selectedRound.submitNote }}</blockquote>
          <p v-if="selectedRound.stale" class="review-stale">当前正文已有更新，这些意见仍以提交版本为准。</p>
          <blockquote v-if="selectedRound.decisionNote" class="review-decision-note">{{ selectedRound.decisionNote }}</blockquote>
        </section>

        <p v-if="selectedRound.status === 'submitted' && selectedRound.stale" class="review-disabled-reason">
          正文已在提交后更新。请作者提交新版本，再继续批注或作出结论。
        </p>

        <section v-if="workspace.canReview && selectedRound.status === 'submitted' && !selectedRound.stale" class="review-compose">
          <div class="row-between">
            <span class="wk-label">段落批注</span>
            <span class="review-anchor-state" :data-ready="Boolean(anchor)">{{ anchor ? '已定位' : '未定位' }}</span>
          </div>
          <div v-if="anchor" class="review-anchor-preview">
            <span>{{ anchor.paragraphId }}</span>
            <q>{{ anchor.selectedText || anchor.paragraphText }}</q>
          </div>
          <textarea v-model="commentText" class="wk-input" rows="3" placeholder="审稿意见" />
          <button class="wk-btn wk-btn-block" type="button" :disabled="busy || !anchor || !commentText.trim()" @click="addComment">添加批注</button>
        </section>

        <section class="review-comments">
          <div class="review-section-heading">
            <span>批注意见</span>
            <span>{{ openComments.length }} 待处理 / {{ selectedRound.comments.length }} 条</span>
          </div>
          <article v-for="comment in selectedRound.comments" :key="comment.id" class="review-comment" :data-resolved="comment.status === 'resolved'">
            <button class="review-comment-anchor" type="button" @click="emit('locate', comment)">
              <span>{{ comment.paragraphId }} · 第 {{ comment.bodyRevision }} 版</span>
              <q>{{ comment.selectedText || comment.paragraphExcerpt }}</q>
            </button>
            <template v-if="editingCommentId === comment.id">
              <textarea v-model="editingText" class="wk-input" rows="3" />
              <div class="review-comment-actions">
                <button class="wk-btn wk-btn-xs" type="button" @click="editingCommentId = ''">取消</button>
                <button class="wk-btn wk-btn-xs" data-primary="true" type="button" :disabled="!editingText.trim()" @click="saveEdit(comment)">保存意见</button>
              </div>
            </template>
            <template v-else>
              <p class="review-comment-body">{{ comment.content }}</p>
              <div class="review-comment-meta">
                <span>{{ comment.authorName || '编辑' }} · {{ formatTime(comment.createdAt) }}</span>
                <span v-if="comment.status === 'resolved'">已处理</span>
              </div>
              <div v-if="comment.status === 'open'" class="review-comment-actions">
                <button v-if="workspace.canReview && selectedRound.status === 'submitted'" class="wk-btn wk-btn-xs" type="button" @click="beginEdit(comment)">修改</button>
                <button v-if="workspace.canResolve" class="wk-btn wk-btn-xs" type="button" :disabled="busy" @click="emit('resolveComment', selectedRound.id, comment)">标记已处理</button>
              </div>
            </template>
          </article>
          <p v-if="!selectedRound.comments.length" class="review-message">本轮还没有批注。</p>
        </section>

        <section v-if="workspace.canReview && selectedRound.status === 'submitted' && !selectedRound.stale" class="review-decision">
          <label class="wk-label" for="review-decision-note">审稿结论</label>
          <textarea id="review-decision-note" v-model="decisionNote" class="wk-input" rows="2" placeholder="打回说明或通过备注" />
          <div class="review-decision-actions">
            <button class="wk-btn" type="button" :disabled="busy || (!openComments.length && !decisionNote.trim())" @click="emit('decide', selectedRound, 'changes_requested', decisionNote)">退回修改</button>
            <button class="wk-btn" data-primary="true" type="button" :disabled="busy || openComments.length > 0" @click="emit('decide', selectedRound, 'approved', decisionNote)">批准本版</button>
          </div>
          <p v-if="openComments.length" class="review-disabled-reason">处理完 {{ openComments.length }} 条开放批注后才能批准。</p>
        </section>
      </template>

      <section v-else class="review-empty">
        <strong>本章尚未提交审稿</strong>
        <p>审稿记录会绑定提交时的正文版本。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.review-panel { min-width: 0; padding-bottom: var(--u6); }
.review-head { position: sticky; top: 0; z-index: 3; }
.review-icon-button { width: 24px; height: 24px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 4px; color: var(--ink-3); background: transparent; cursor: pointer; }
.review-icon-button:hover { color: var(--ink); background: var(--panel-sunken); }
.review-icon-button:focus-visible, .review-comment-anchor:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.review-submit, .review-compose, .review-decision { display: grid; gap: var(--u2); padding: var(--u3); border-bottom: var(--hair) solid var(--line-strong); }
.review-submit textarea, .review-compose textarea, .review-decision textarea, .review-comment textarea { width: 100%; padding: 7px 8px; line-height: 1.55; resize: vertical; }
.review-disabled-reason { margin: 0; padding: 8px var(--u3); border-bottom: var(--hair) solid var(--line); color: var(--alert-ink); background: var(--alert-soft); font-size: var(--fs-xs); line-height: 1.55; }
.review-round-picker { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--u2); align-items: center; padding: var(--u3); border-bottom: var(--hair) solid var(--line); }
.review-round-picker label { color: var(--ink-3); font-size: var(--fs-xs); }
.review-round-picker select { width: 100%; height: 28px; }
.review-round-summary { padding: var(--u3); border-bottom: var(--hair) solid var(--line-strong); box-shadow: inset 3px 0 0 var(--line-strong); }
.review-round-summary[data-status='submitted'], .review-round-summary[data-status='changes_requested'] { box-shadow: inset 3px 0 0 var(--alert); }
.review-round-summary[data-status='approved'] { box-shadow: inset 3px 0 0 var(--success); }
.review-round-summary strong { color: var(--ink); }
.review-round-summary > p { margin: 5px 0 0; color: var(--ink-4); font-size: var(--fs-xs); }
.review-status { color: var(--alert-ink); font-size: var(--fs-xs); font-weight: 700; }
.review-round-summary[data-status='approved'] .review-status { color: var(--success); }
.review-round-summary blockquote { margin: var(--u2) 0 0; padding-left: var(--u2); border-left: 2px solid var(--line-strong); color: var(--ink-2); font-size: var(--fs-sm); line-height: 1.6; }
.review-round-summary .review-stale { color: var(--alert-ink); }
.review-round-summary .review-decision-note { border-left-color: var(--alert); }
.review-anchor-state { color: var(--ink-4); font-size: var(--fs-xs); }
.review-anchor-state[data-ready='true'] { color: var(--primary); font-weight: 700; }
.review-anchor-preview { min-width: 0; display: grid; gap: 4px; padding: 7px 8px; border-left: 2px solid var(--alert); background: var(--alert-soft); }
.review-anchor-preview span { color: var(--alert-ink); font: 9px/1.4 var(--font-mono); }
.review-anchor-preview q { overflow: hidden; color: var(--ink-2); font-family: var(--font-prose); font-size: var(--fs-sm); line-height: 1.55; text-overflow: ellipsis; white-space: nowrap; }
.review-section-heading { min-height: 35px; display: flex; align-items: center; justify-content: space-between; gap: var(--u2); padding: 0 var(--u3); border-bottom: var(--hair) solid var(--line); color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.review-section-heading span:last-child { color: var(--ink-4); font-family: var(--font-mono); font-weight: 400; }
.review-comment { min-width: 0; padding: var(--u3); border-bottom: var(--hair) solid var(--line); box-shadow: inset 3px 0 0 var(--alert); }
.review-comment[data-resolved='true'] { box-shadow: inset 3px 0 0 var(--line); opacity: .72; }
.review-comment-anchor { width: 100%; min-width: 0; display: grid; gap: 5px; padding: 0; border: 0; color: var(--ink-2); background: transparent; text-align: left; cursor: pointer; }
.review-comment-anchor span { color: var(--alert-ink); font: 9px/1.4 var(--font-mono); }
.review-comment-anchor q { display: -webkit-box; overflow: hidden; font-family: var(--font-prose); font-size: var(--fs-sm); line-height: 1.55; white-space: normal; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.review-comment-body { margin: var(--u2) 0 0; color: var(--ink); font-size: var(--fs-sm); line-height: 1.7; }
.review-comment-meta { display: flex; justify-content: space-between; gap: var(--u2); margin-top: var(--u2); color: var(--ink-4); font-size: 9px; }
.review-comment-actions, .review-decision-actions { display: flex; justify-content: flex-end; gap: var(--u2); margin-top: var(--u2); }
.review-decision-actions .wk-btn { min-width: 0; flex: 1; }
.review-message, .review-empty { margin: 0; padding: var(--u5) var(--u3); color: var(--ink-3); font-size: var(--fs-sm); line-height: 1.7; }
.review-message-error { color: var(--alert-ink); }
.review-empty { border-top: 2px solid var(--line-strong); }
.review-empty strong { color: var(--ink); }
.review-empty p { margin: 4px 0 0; }
</style>
