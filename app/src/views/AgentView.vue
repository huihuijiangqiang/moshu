<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { agentApi, type AgentAction, type AgentMessage } from '@/api/agent'
import { useShellStore } from '@/stores/shell'

const route = useRoute()
const shell = useShellStore()
const sessionId = ref<string | null>(null)
const messages = ref<AgentMessage[]>([])
const actions = ref<AgentAction[]>([])
const draft = ref('')
const busy = ref(false)
const error = ref('')
const transcript = ref<HTMLElement | null>(null)

const pendingActions = computed(() => actions.value.filter((action) => action.status === 'proposed'))
const projectId = computed(() => typeof route.query.project === 'string' ? route.query.project : undefined)

async function load() {
  const session = await agentApi.createSession(projectId.value)
  sessionId.value = session.id
  const history = await agentApi.getSession(session.id)
  messages.value = history.messages
  actions.value = history.actions
}

async function send() {
  const content = draft.value.trim()
  if (!content || !sessionId.value || busy.value) return
  busy.value = true
  error.value = ''
  messages.value.push({ id: `local-${Date.now()}`, role: 'user', content, sequence: messages.value.length + 1, metadata: {}, created_at: new Date().toISOString() })
  draft.value = ''
  try {
    const result = await agentApi.send(sessionId.value, content)
    messages.value.push(result.message)
    actions.value.push(...result.actions)
    await nextTick()
    transcript.value?.scrollTo({ top: transcript.value.scrollHeight, behavior: 'smooth' })
  } catch {
    error.value = 'Agent 暂时无法响应，请稍后重试。'
  } finally {
    busy.value = false
  }
}

async function decide(action: AgentAction, decision: 'approve' | 'reject') {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const updated = await agentApi.decide(action.id, decision)
    const index = actions.value.findIndex((item) => item.id === action.id)
    if (index >= 0) actions.value[index] = updated
  } catch {
    error.value = '动作状态更新失败，请刷新后重试。'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  shell.setCrumb('AI 助手')
  try { await load() } catch { error.value = 'Agent 会话创建失败，请确认已登录。' }
})
</script>

<template>
  <main class="agent-page">
    <header class="agent-head">
      <div>
        <div class="kicker">受控工作台</div>
        <h1>AI 助手</h1>
        <p>描述目标，Agent 只会提出可审核的任务；任何写入都需要你逐项批准。</p>
      </div>
      <span class="agent-badge">{{ pendingActions.length }} 个待确认动作</span>
    </header>

    <section ref="transcript" class="agent-transcript" aria-live="polite">
      <div v-if="!messages.length" class="agent-empty">
        例如：为当前作品创建三章章纲，或新增一个“人物”设定。<br>
        Agent 会先说明计划，再等待批准。
      </div>
      <article v-for="message in messages" :key="message.id" class="agent-message" :data-role="message.role">
        <div class="agent-role">{{ message.role === 'user' ? '你' : 'Agent' }}</div>
        <div class="agent-bubble">{{ message.content }}</div>
      </article>
    </section>

    <section v-if="actions.length" class="agent-actions">
      <div class="kicker">任务提案</div>
      <article v-for="action in actions" :key="action.id" class="agent-action" :data-status="action.status">
        <div class="agent-action-main">
          <strong>{{ action.title }}</strong>
          <span class="agent-action-type">{{ action.type }}</span>
          <span class="agent-action-status">{{ action.status === 'proposed' ? '待确认' : action.status === 'succeeded' ? '已完成' : action.status === 'rejected' ? '已拒绝' : action.status === 'failed' ? '失败' : action.status }}</span>
        </div>
        <pre>{{ JSON.stringify(action.parameters, null, 2) }}</pre>
        <div v-if="action.status === 'proposed'" class="agent-action-buttons">
          <button class="wk-btn" type="button" :disabled="busy" @click="decide(action, 'reject')">拒绝</button>
          <button class="wk-btn" data-primary="true" type="button" :disabled="busy" @click="decide(action, 'approve')">批准并执行</button>
        </div>
        <p v-if="action.result?.message" class="agent-result">{{ action.result.message }}</p>
      </article>
    </section>

    <form class="agent-compose" @submit.prevent="send">
      <textarea v-model="draft" :disabled="busy || !sessionId" rows="3" placeholder="告诉 Agent 你要完成什么…" @keydown.meta.enter.prevent="send" @keydown.ctrl.enter.prevent="send" />
      <div class="agent-compose-foot">
        <span class="muted">{{ busy ? '处理中…' : '动作执行前会再次校验权限' }}</span>
        <button class="wk-btn" data-primary="true" type="submit" :disabled="busy || !draft.trim() || !sessionId">发送</button>
      </div>
      <p v-if="error" class="agent-error">{{ error }}</p>
    </form>
  </main>
</template>

<style scoped>
.agent-page { height: 100%; overflow: auto; max-width: 980px; margin: 0 auto; padding: 40px 48px 72px; color: var(--ink); }
.agent-head { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; border-bottom: 1px solid var(--line); padding-bottom: 24px; }
.agent-head h1 { margin: 6px 0 8px; font-size: 30px; font-weight: 700; letter-spacing: 0; }
.agent-head p { margin: 0; color: var(--ink-3); line-height: 1.7; }
.agent-badge { border: 1px solid var(--primary-line); color: var(--primary); padding: 7px 10px; white-space: nowrap; font-size: var(--fs-sm); }
.agent-transcript { min-height: 240px; max-height: 52vh; overflow: auto; padding: 28px 0; }
.agent-empty { color: var(--ink-3); line-height: 1.8; padding: 24px; background: var(--panel-sunken); border-left: 3px solid var(--primary-line); }
.agent-message { display: flex; gap: 12px; margin: 0 0 18px; align-items: flex-start; }
.agent-message[data-role="user"] { justify-content: flex-end; }
.agent-message[data-role="user"] .agent-role { order: 2; }
.agent-role { width: 42px; flex: 0 0 42px; color: var(--ink-3); font-size: var(--fs-sm); padding-top: 9px; }
.agent-bubble { max-width: min(760px, 84%); padding: 11px 14px; background: var(--panel); border: 1px solid var(--line); line-height: 1.75; white-space: pre-wrap; }
.agent-message[data-role="user"] .agent-bubble { background: var(--primary-soft); border-color: var(--primary-line); }
.agent-actions { border-top: 1px solid var(--line); padding-top: 22px; }
.agent-action { margin-top: 12px; padding: 14px; border: 1px solid var(--line-strong); background: var(--panel); }
.agent-action[data-status="succeeded"] { border-color: var(--success-line); }
.agent-action[data-status="failed"] { border-color: var(--alert-line); }
.agent-action-main { display: flex; gap: 9px; align-items: center; flex-wrap: wrap; }
.agent-action-type, .agent-action-status { font-size: var(--fs-sm); color: var(--ink-3); }
.agent-action-status { margin-left: auto; color: var(--primary); }
.agent-action pre { margin: 10px 0; padding: 10px; overflow: auto; background: var(--panel-sunken); color: var(--ink-2); font: 12px/1.55 var(--font-mono); white-space: pre-wrap; }
.agent-action-buttons { display: flex; justify-content: flex-end; gap: 8px; }
.agent-result, .agent-error { margin: 8px 0 0; color: var(--ink-3); font-size: var(--fs-sm); }
.agent-compose { margin-top: 26px; border-top: 1px solid var(--line); padding-top: 18px; }
.agent-compose textarea { width: 100%; resize: vertical; min-height: 84px; padding: 12px; border: 1px solid var(--line-strong); background: var(--panel); color: var(--ink); font: inherit; line-height: 1.6; box-sizing: border-box; }
.agent-compose textarea:focus { outline: 2px solid var(--primary-line); outline-offset: 1px; }
.agent-compose-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 9px; }
@media (max-width: 700px) { .agent-page { padding: 24px 18px 48px; } .agent-head { display: block; } .agent-badge { display: inline-block; margin-top: 16px; } }
</style>
