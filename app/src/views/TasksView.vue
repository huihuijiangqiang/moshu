<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '@/components/ui/AppIcon.vue'
import { tasksApi, type TaskItem, type TaskState } from '@/api/tasks'

const router = useRouter()
const overview = ref<Awaited<ReturnType<typeof tasksApi.overview>> | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const filter = ref<'all' | 'active' | 'failed' | 'succeeded'>('all')
const retrying = ref<string | null>(null)
let timer: number | undefined

const stateLabel: Record<TaskState, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已撤回'
}

const kindLabel: Record<TaskItem['kind'], string> = {
  generation: '正文生成',
  consistency: '一致性',
  embedding: '语义索引',
  outbox: '后台投递'
}

const filteredItems = computed(() => {
  const items = overview.value?.items ?? []
  if (filter.value === 'active') return items.filter((item) => item.state === 'queued' || item.state === 'running')
  if (filter.value === 'failed') return items.filter((item) => item.state === 'failed')
  if (filter.value === 'succeeded') return items.filter((item) => item.state === 'succeeded' || item.state === 'cancelled')
  return items
})

async function load(initial = false) {
  if (initial) loading.value = true
  else refreshing.value = true
  try {
    overview.value = await tasksApi.overview()
    error.value = ''
  } catch {
    error.value = '任务状态暂时无法获取，请检查服务连接后重试。'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return '时间未知'
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function openTask(item: TaskItem) {
  if (item.open_path) void router.push(item.open_path)
}

async function retryTask(item: TaskItem) {
  if (!item.retry || retrying.value) return
  // A continuation is an SSE stream and belongs in the writing editor, where its
  // candidate can be reviewed. JSON retries (scan/index) are safe to fire here.
  if (item.kind === 'generation') {
    openTask(item)
    return
  }
  retrying.value = item.id
  try {
    await tasksApi.trigger(item.retry)
    await load()
  } catch {
    error.value = '重试请求未发送成功，请稍后再试。'
  } finally {
    retrying.value = null
  }
}

onMounted(() => {
  void load(true)
  timer = window.setInterval(() => { void load() }, 8000)
})
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <main class="tasks-page">
    <header class="tasks-heading">
      <div>
        <span class="wk-label">WORK QUEUE</span>
        <h1>任务中心</h1>
        <p>所有作品的生成、校验和索引活动都在这里留下可追溯状态。</p>
      </div>
      <button class="wk-btn tasks-refresh" type="button" :disabled="refreshing" @click="load()">
        <AppIcon name="restore" :size="14" />{{ refreshing ? '刷新中' : '刷新' }}
      </button>
    </header>

    <div v-if="error" class="tasks-alert" role="alert">
      <span>{{ error }}</span>
      <button class="text-button" type="button" @click="load(true)">再试一次</button>
    </div>

    <section v-if="overview" class="tasks-counts" aria-label="任务统计">
      <button class="count-block" :data-active="filter === 'all'" type="button" @click="filter = 'all'">
        <span>全部活动</span><strong>{{ overview.counts.total }}</strong>
      </button>
      <button class="count-block" :data-active="filter === 'active'" type="button" @click="filter = 'active'">
        <span>进行中</span><strong>{{ overview.counts.queued + overview.counts.running }}</strong>
      </button>
      <button class="count-block count-warn" :data-active="filter === 'failed'" type="button" @click="filter = 'failed'">
        <span>需要处理</span><strong>{{ overview.counts.failed }}</strong>
      </button>
      <button class="count-block" :data-active="filter === 'succeeded'" type="button" @click="filter = 'succeeded'">
        <span>已完成</span><strong>{{ overview.counts.succeeded }}</strong>
      </button>
    </section>

    <section class="tasks-list" aria-labelledby="tasks-list-title">
      <div class="tasks-list-heading">
        <h2 id="tasks-list-title">活动记录</h2>
        <span v-if="overview?.has_more" class="muted">仅显示最近 {{ overview.limit }} 项</span>
      </div>

      <div v-if="loading" class="tasks-empty">正在读取任务状态…</div>
      <div v-else-if="error && !overview" class="tasks-empty">
        <strong>任务状态不可用</strong>
        <span>恢复连接后点击上方“再试一次”。</span>
      </div>
      <div v-else-if="filteredItems.length === 0" class="tasks-empty">
        <AppIcon name="history" :size="22" />
        <strong>{{ filter === 'all' ? '还没有任务记录' : '这个筛选条件下没有记录' }}</strong>
        <span>{{ filter === 'all' ? '生成正文或运行一次一致性检查后，活动会出现在这里。' : '切换筛选条件查看其他活动。' }}</span>
      </div>
      <div v-else class="tasks-table-wrap">
        <table class="tasks-table">
          <thead>
            <tr><th>任务</th><th>作品 / 章节</th><th>状态</th><th>进度</th><th>更新时间</th><th><span class="sr-only">操作</span></th></tr>
          </thead>
          <tbody>
            <tr v-for="item in filteredItems" :key="item.id">
              <td>
                <div class="task-title"><span class="task-kind">{{ kindLabel[item.kind] }}</span><strong>{{ item.title }}</strong></div>
                <small class="task-id">{{ item.id }}</small>
              </td>
              <td>
                <button class="task-location" type="button" @click="openTask(item)">
                  <strong>{{ item.project_title }}</strong>
                  <span v-if="item.chapter_index !== null">第 {{ item.chapter_index }} 章 · {{ item.chapter_title }}</span>
                  <span v-else>作品级任务</span>
                </button>
              </td>
              <td><span class="task-state" :data-state="item.state">{{ stateLabel[item.state] }}</span><small v-if="item.error_code" class="task-error-code">{{ item.error_code }}</small></td>
              <td class="task-progress-cell">
                <div v-if="item.progress !== null" class="task-progress" :aria-label="`进度 ${item.progress}%`"><span :style="{ width: `${item.progress}%` }" /></div>
                <span>{{ item.progress_label || '—' }}</span>
                <small v-if="item.error_detail" class="task-error">{{ item.error_detail }}</small>
              </td>
              <td class="task-time">{{ formatTime(item.updated_at) }}</td>
              <td class="task-actions">
                <button v-if="item.retry" class="text-button" type="button" :disabled="retrying !== null" @click="retryTask(item)">{{ retrying === item.id ? '处理中…' : item.retry.label }}</button>
                <button v-else class="text-button muted" type="button" @click="openTask(item)">打开</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>

<style scoped>
.tasks-page { max-width: 1180px; margin: 0 auto; padding: 42px 48px 80px; color: var(--ink); }
.tasks-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding-bottom: 28px; border-bottom: var(--rule) solid var(--line-strong); }
.tasks-heading h1 { margin: 8px 0 6px; font: 600 32px/1.1 var(--font-prose); letter-spacing: 0; }
.tasks-heading p { margin: 0; color: var(--ink-3); font-size: 12px; }
.tasks-refresh { flex: none; margin-top: 8px; }
.tasks-alert { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; padding: 12px 14px; border-left: 3px solid var(--alert); background: var(--alert-soft); color: var(--alert-ink); font-size: 12px; }
.tasks-counts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 26px 0 36px; border-top: var(--hair) solid var(--line-strong); border-bottom: var(--hair) solid var(--line-strong); }
.count-block { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; min-height: 76px; padding: 14px 18px; border: 0; border-right: var(--hair) solid var(--line); background: transparent; color: var(--ink-3); text-align: left; cursor: pointer; }
.count-block:last-child { border-right: 0; }
.count-block strong { color: var(--ink); font: 700 25px/1 var(--font-mono); }
.count-block[data-active="true"] { background: var(--primary-soft); color: var(--ink); box-shadow: inset 0 -3px 0 var(--primary); }
.count-warn strong { color: var(--alert-ink); }
.tasks-list-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.tasks-list-heading h2 { margin: 0; font: 600 19px/1.2 var(--font-prose); }
.tasks-table-wrap { overflow-x: auto; border-top: var(--rule) solid var(--ink); }
.tasks-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.tasks-table th { padding: 11px 12px; border-bottom: var(--hair) solid var(--line-strong); color: var(--ink-3); font: 700 9px/1 var(--font-mono); letter-spacing: .08em; text-align: left; white-space: nowrap; }
.tasks-table td { min-height: 70px; padding: 14px 12px; border-bottom: var(--hair) solid var(--line); vertical-align: middle; }
.task-title { display: flex; flex-direction: column; gap: 5px; min-width: 150px; }
.task-title strong { font-weight: 600; }
.task-kind, .task-id, .task-error-code { color: var(--ink-3); font: 9px/1.2 var(--font-mono); }
.task-location { display: flex; flex-direction: column; gap: 4px; max-width: 190px; padding: 0; border: 0; background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
.task-location strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-location span { overflow: hidden; color: var(--ink-3); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.task-state { display: inline-block; min-width: 52px; padding: 5px 7px; border-left: 2px solid var(--line-strong); color: var(--ink-2); font-size: 11px; white-space: nowrap; }
.task-state[data-state="running"] { border-color: var(--primary); color: var(--primary); }
.task-state[data-state="queued"] { border-color: var(--line-strong); color: var(--ink-3); }
.task-state[data-state="failed"] { border-color: var(--alert); color: var(--alert-ink); }
.task-state[data-state="succeeded"] { border-color: var(--success); color: var(--success); }
.task-error-code { display: block; margin-top: 5px; }
.task-progress-cell { min-width: 150px; color: var(--ink-3); font: 10px/1.3 var(--font-mono); }
.task-progress { width: 100%; height: 4px; margin-bottom: 6px; background: var(--line); }
.task-progress span { display: block; height: 100%; background: var(--primary); transition: width .25s ease; }
.task-error { display: block; max-width: 220px; margin-top: 6px; color: var(--alert-ink); font: 10px/1.4 var(--font-ui); }
.task-time { min-width: 100px; color: var(--ink-3); font: 10px/1.2 var(--font-mono); white-space: nowrap; }
.task-actions { text-align: right; white-space: nowrap; }
.text-button { padding: 0; border: 0; background: transparent; color: var(--primary); font-size: 11px; cursor: pointer; }
.text-button:disabled { opacity: .55; cursor: wait; }
.tasks-empty { display: grid; justify-items: center; gap: 9px; padding: 70px 20px; border-bottom: var(--hair) solid var(--line); color: var(--ink-3); text-align: center; }
.tasks-empty strong { color: var(--ink); font: 600 17px/1.2 var(--font-prose); }
.tasks-empty span { font-size: 11px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 760px) {
  .tasks-page { padding: 28px 18px 56px; }
  .tasks-heading { flex-direction: column; }
  .tasks-heading h1 { font-size: 27px; }
  .tasks-counts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .count-block:nth-child(2) { border-right: 0; }
  .count-block:nth-child(-n + 2) { border-bottom: var(--hair) solid var(--line); }
}
@media (prefers-reduced-motion: reduce) { .task-progress span { transition: none; } }
</style>
