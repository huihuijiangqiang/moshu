<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { adminApi, type AdminOverview, type AdminSettings, type AdminUser, type PlatformUsage } from '@/api/admin'
import { getSessionUser } from '@/api/session'
import { useShellStore } from '@/stores/shell'

const shell = useShellStore()
const overview = ref<AdminOverview | null>(null)
const users = ref<AdminUser[]>([])
const settings = ref<AdminSettings | null>(null)
const costs = ref<PlatformUsage | null>(null)
const tab = ref<'users' | 'costs' | 'settings'>('users')
const search = ref('')
const loading = ref(true)
const message = ref('')
const currentUser = getSessionUser()
const canPromote = computed(() => currentUser?.system_role === 'super_admin')
const number = new Intl.NumberFormat('zh-CN')
const tokenSegments = computed(() => {
  const totals = costs.value?.totals
  if (!totals) return []
  const values = [
    { key: 'input', label: '未缓存输入', value: Math.max(0, totals.prompt_tokens - totals.cached_tokens) },
    { key: 'cached', label: '缓存输入', value: totals.cached_tokens },
    { key: 'output', label: '模型输出', value: totals.completion_tokens }
  ]
  const total = values.reduce((sum, item) => sum + item.value, 0)
  return values.map(item => ({ ...item, width: total ? Math.max(2, item.value / total * 100) : 0 }))
})

async function load() {
  loading.value = true
  message.value = ''
  try {
    ;[overview.value, users.value, settings.value, costs.value] = await Promise.all([
      adminApi.overview(), adminApi.users(search.value), adminApi.settings(), adminApi.platformUsage()
    ])
  } catch (error) {
    message.value = error instanceof Error ? error.message : '管理数据加载失败'
  } finally {
    loading.value = false
  }
}

async function searchUsers() {
  users.value = await adminApi.users(search.value)
}

async function saveUser(user: AdminUser) {
  message.value = ''
  try {
    const updated = await adminApi.updateUser(user.id, {
      plan: user.plan,
      system_role: user.system_role,
      is_active: user.is_active,
      quota_remaining: user.quota_remaining,
      quota_total: user.quota_total
    })
    Object.assign(user, updated)
    message.value = `已保存 ${user.name}`
  } catch (error) {
    message.value = error instanceof Error ? error.message : '保存失败'
  }
}

async function saveSettings() {
  if (!settings.value) return
  try {
    settings.value = await adminApi.updateSettings({
      registration_enabled: settings.value.registration_enabled,
      default_plan: settings.value.default_plan,
      default_monthly_quota: settings.value.default_monthly_quota,
      basic_input_credits: settings.value.credit_rates.basic_input,
      basic_output_credits: settings.value.credit_rates.basic_output,
      advanced_input_credits: settings.value.credit_rates.advanced_input,
      advanced_output_credits: settings.value.credit_rates.advanced_output,
      cached_input_percent: settings.value.credit_rates.cached_percent
    })
    message.value = '运行配置已保存'
  } catch (error) {
    message.value = error instanceof Error ? error.message : '保存失败'
  }
}

onMounted(() => {
  shell.setCrumb('系统管理')
  void load()
})
</script>

<template>
  <div class="admin-page">
    <header class="admin-summary">
      <div v-for="item in [
        ['用户', overview?.users ?? 0], ['作品', overview?.projects ?? 0],
        ['活动会话', overview?.active_sessions ?? 0], ['运行任务', overview?.active_consistency_runs ?? 0],
        ['待处理告警', overview?.open_guard_issues ?? 0]
      ]" :key="item[0]" class="admin-metric">
        <strong>{{ item[1] }}</strong><span>{{ item[0] }}</span>
      </div>
    </header>

    <div class="admin-tabs" role="tablist" aria-label="管理视图">
      <button type="button" :aria-selected="tab === 'users'" @click="tab = 'users'">用户与权限</button>
      <button type="button" :aria-selected="tab === 'costs'" @click="tab = 'costs'">模型成本</button>
      <button type="button" :aria-selected="tab === 'settings'" @click="tab = 'settings'">运行配置</button>
      <span v-if="message" class="admin-message">{{ message }}</span>
    </div>

    <main v-if="!loading && tab === 'users'" class="admin-content">
      <form class="admin-search" @submit.prevent="searchUsers">
        <input v-model="search" type="search" placeholder="搜索姓名或邮箱">
        <button class="wk-btn" type="submit">搜索</button>
      </form>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>账号</th><th>系统角色</th><th>套餐</th><th>剩余额度</th><th>总额度</th><th>状态</th><th /></tr></thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td><strong>{{ user.name }}</strong><small>{{ user.email ?? user.id }}</small></td>
              <td>
                <select v-model="user.system_role" :disabled="!canPromote">
                  <option value="user">用户</option><option value="admin">管理员</option><option value="super_admin">超级管理员</option>
                </select>
              </td>
              <td><select v-model="user.plan"><option value="free">免费</option><option value="author">作者</option><option value="studio">工作室</option></select></td>
              <td><input v-model.number="user.quota_remaining" type="number" min="0"></td>
              <td><input v-model.number="user.quota_total" type="number" min="0"></td>
              <td><label class="admin-toggle"><input v-model="user.is_active" type="checkbox"><span>{{ user.is_active ? '启用' : '停用' }}</span></label></td>
              <td><button class="wk-btn wk-btn-xs" type="button" @click="saveUser(user)">保存</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </main>

    <main v-else-if="!loading && tab === 'costs' && costs" class="admin-content admin-costs">
      <section class="cost-flow" aria-label="近 30 天 token 构成">
        <div class="cost-period">
          <span>近 30 天平台调用</span>
          <strong>{{ number.format(costs.totals.requests) }}</strong>
          <small>请求</small>
        </div>
        <div class="token-ruler" aria-hidden="true">
          <span
            v-for="segment in tokenSegments"
            :key="segment.key"
            :class="`token-${segment.key}`"
            :style="{ width: `${segment.width}%` }"
          />
        </div>
        <dl v-if="tokenSegments.some(segment => segment.value > 0)" class="token-legend">
          <div v-for="segment in tokenSegments" :key="segment.key">
            <dt><i :class="`token-${segment.key}`" />{{ segment.label }}</dt>
            <dd>{{ number.format(segment.value) }}</dd>
          </div>
          <div title="网关未返回 usage 时，按本地 tokenizer 估算 token"><dt>估算记录</dt><dd>{{ number.format(costs.totals.estimated_events) }}</dd></div>
        </dl>
        <p v-else class="cost-empty">当前周期暂无 token 调用</p>
      </section>

      <section class="cost-section">
        <h2>按能力汇总</h2>
        <div class="admin-table-wrap">
          <table class="admin-table cost-table">
            <thead><tr><th>能力</th><th>调用事件</th><th>网关请求</th><th>输入 token</th><th>缓存 token</th><th>输出 token</th></tr></thead>
            <tbody>
              <tr v-for="item in costs.items" :key="item.feature">
                <td><strong>{{ item.label }}</strong><small>{{ item.feature }}</small></td>
                <td>{{ number.format(item.events) }}</td>
                <td>{{ number.format(item.requests) }}</td>
                <td>{{ number.format(item.prompt_tokens) }}</td>
                <td>{{ number.format(item.cached_tokens) }}</td>
                <td>{{ number.format(item.completion_tokens) }}</td>
              </tr>
              <tr v-if="!costs.items.length"><td colspan="6" class="admin-empty">当前周期暂无后台模型调用</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="cost-section">
        <h2>最近调用</h2>
        <div class="admin-table-wrap">
          <table class="admin-table cost-table cost-recent">
            <thead><tr><th>时间</th><th>能力</th><th>模型</th><th>作品</th><th>输入 / 缓存 / 输出</th><th>计量</th></tr></thead>
            <tbody>
              <tr v-for="item in costs.recent" :key="item.id">
                <td>{{ new Date(item.timestamp).toLocaleString('zh-CN', { hour12: false }) }}</td>
                <td>{{ item.label }}</td>
                <td><code>{{ item.model ?? 'unknown' }}</code></td>
                <td><code>{{ item.project_id ?? '—' }}</code></td>
                <td>{{ number.format(item.prompt_tokens) }} / {{ number.format(item.cached_tokens) }} / {{ number.format(item.completion_tokens) }}</td>
                <td>{{ item.estimated ? '估算' : '网关' }}</td>
              </tr>
              <tr v-if="!costs.recent.length"><td colspan="6" class="admin-empty">暂无调用明细</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>

    <main v-else-if="!loading && tab === 'settings' && settings" class="admin-content admin-settings">
      <section>
        <h2>账号默认值</h2>
        <label class="admin-field"><span>开放注册</span><input v-model="settings.registration_enabled" type="checkbox"></label>
        <label class="admin-field"><span>默认套餐</span><select v-model="settings.default_plan"><option value="free">免费</option><option value="author">作者</option><option value="studio">工作室</option></select></label>
        <label class="admin-field"><span>每月默认额度</span><input v-model.number="settings.default_monthly_quota" type="number" min="0"></label>
        <h2 class="admin-subhead">生成计价 · 每千 token</h2>
        <label class="admin-field"><span>基础档输入</span><input v-model.number="settings.credit_rates.basic_input" type="number" min="0"></label>
        <label class="admin-field"><span>基础档输出</span><input v-model.number="settings.credit_rates.basic_output" type="number" min="0"></label>
        <label class="admin-field"><span>高级档输入</span><input v-model.number="settings.credit_rates.advanced_input" type="number" min="0"></label>
        <label class="admin-field"><span>高级档输出</span><input v-model.number="settings.credit_rates.advanced_output" type="number" min="0"></label>
        <label class="admin-field"><span>缓存输入折算</span><span><input v-model.number="settings.credit_rates.cached_percent" type="number" min="0" max="100"> %</span></label>
        <button class="wk-btn" data-primary="true" type="button" @click="saveSettings">保存运行配置</button>
      </section>
      <section>
        <h2>模型运行状态</h2>
        <dl class="admin-definition">
          <div><dt>生成模型</dt><dd>{{ settings.generation_model }}</dd></div>
          <div><dt>一致性模型</dt><dd>{{ settings.consistency_model }}</dd></div>
          <div><dt>Embedding</dt><dd>{{ settings.embedding_model }}</dd></div>
          <div><dt>生成网关</dt><dd>{{ settings.generation_gateway_configured ? '已配置' : '未配置' }}</dd></div>
          <div><dt>向量网关</dt><dd>{{ settings.embedding_gateway_configured ? '已配置' : '未配置' }}</dd></div>
        </dl>
        <p>密钥只从服务端环境变量读取，管理页面不会返回密钥原文。</p>
      </section>
    </main>
    <div v-else class="admin-loading">正在读取管理数据…</div>
  </div>
</template>

<style scoped>
.admin-page { height: 100%; overflow: auto; background: var(--panel); color: var(--ink); }
.admin-summary { display: grid; grid-template-columns: repeat(5, minmax(110px, 1fr)); border-bottom: var(--hair) solid var(--line); }
.admin-metric { min-height: 92px; padding: 20px 24px; display: flex; flex-direction: column; justify-content: center; border-right: var(--hair) solid var(--line); }
.admin-metric strong { font: 700 28px/1 var(--font-mono); }
.admin-metric span { margin-top: 8px; color: var(--ink-3); font-size: var(--fs-sm); }
.admin-tabs { min-height: 48px; padding: 0 28px; display: flex; align-items: stretch; gap: 4px; border-bottom: var(--hair) solid var(--line); }
.admin-tabs button { padding: 0 14px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--ink-3); cursor: pointer; }
.admin-tabs button[aria-selected="true"] { border-bottom-color: var(--primary); color: var(--ink); font-weight: 700; }
.admin-message { margin-left: auto; align-self: center; color: var(--ink-2); font-size: var(--fs-sm); }
.admin-content { padding: 26px 28px 48px; }
.admin-search { width: min(460px, 100%); display: grid; grid-template-columns: 1fr auto; gap: 8px; margin-bottom: 18px; }
.admin-search input, .admin-table input, select { min-width: 0; height: 34px; border: var(--hair) solid var(--line-strong); padding: 0 9px; background: var(--paper); color: var(--ink); }
.admin-table-wrap { overflow: auto; border-top: var(--hair) solid var(--line-strong); }
.admin-table { width: 100%; min-width: 900px; border-collapse: collapse; font-size: var(--fs-sm); }
.admin-table th { padding: 10px 8px; text-align: left; color: var(--ink-3); font-weight: 600; }
.admin-table td { padding: 11px 8px; border-top: var(--hair) solid var(--line); }
.admin-table td:first-child { min-width: 190px; }
.admin-table small { display: block; margin-top: 4px; color: var(--ink-3); }
.admin-table input[type="number"] { width: 100px; }
.admin-toggle { display: inline-flex; align-items: center; gap: 6px; }
.admin-settings { display: grid; grid-template-columns: minmax(280px, 440px) minmax(320px, 1fr); gap: 56px; }
.admin-settings h2 { margin: 0 0 20px; font-size: 17px; }
.admin-settings .admin-subhead { margin: 30px 0 8px; font-size: 14px; }
.admin-field { min-height: 48px; display: grid; grid-template-columns: 150px 1fr; align-items: center; border-top: var(--hair) solid var(--line); }
.admin-definition { margin: 0; border-top: var(--hair) solid var(--line); }
.admin-definition div { min-height: 44px; display: grid; grid-template-columns: 130px 1fr; align-items: center; border-bottom: var(--hair) solid var(--line); }
.admin-definition dt { color: var(--ink-3); }.admin-definition dd { margin: 0; font-family: var(--font-mono); }
.admin-settings p { color: var(--ink-3); line-height: 1.7; }.admin-loading { padding: 40px 28px; color: var(--ink-3); }
.admin-costs { display: grid; gap: 34px; }
.cost-flow { display: grid; grid-template-columns: 180px minmax(260px, 1fr); column-gap: 30px; align-items: center; padding-bottom: 28px; border-bottom: var(--hair) solid var(--line-strong); }
.cost-period { grid-row: span 2; display: grid; grid-template-columns: auto 1fr; align-items: baseline; }
.cost-period span { grid-column: 1 / -1; color: var(--ink-3); font-size: var(--fs-sm); }
.cost-period strong { margin-top: 8px; font: 700 34px/1 var(--font-mono); }
.cost-period small { margin-left: 7px; color: var(--ink-3); }
.token-ruler { height: 12px; display: flex; overflow: hidden; background: var(--line); }
.token-ruler span { min-width: 0; transition: width 180ms ease; }
.token-input { background: var(--ink-2); }.token-cached { background: var(--primary); }.token-output { background: var(--alert); }
.token-legend { display: flex; flex-wrap: wrap; gap: 14px 28px; margin: 12px 0 0; }
.token-legend div { min-width: 110px; }
.token-legend dt { color: var(--ink-3); font-size: 12px; }
.token-legend dt i { width: 7px; height: 7px; display: inline-block; margin-right: 6px; }
.token-legend dd { margin: 4px 0 0; font: 600 14px/1.2 var(--font-mono); }
.cost-section h2 { margin: 0 0 12px; font-size: 15px; }
.cost-table { min-width: 780px; font-variant-numeric: tabular-nums; }
.cost-table td:not(:first-child) { font-family: var(--font-mono); }
.cost-recent { min-width: 980px; }.cost-recent code { color: var(--ink-2); background: transparent; }
.admin-empty { height: 80px; text-align: center; color: var(--ink-3); font-family: inherit !important; }
.cost-empty { grid-column: 2; margin: 12px 0 0; color: var(--ink-3); font-size: var(--fs-sm); }
@media (max-width: 900px) { .admin-summary { grid-template-columns: repeat(2, 1fr); }.admin-settings { grid-template-columns: 1fr; gap: 36px; } }
@media (max-width: 680px) { .cost-flow { grid-template-columns: 1fr; row-gap: 18px; }.cost-period { grid-row: auto; }.admin-tabs { overflow-x: auto; }.admin-tabs button { flex: 0 0 auto; } }
@media (prefers-reduced-motion: reduce) { .token-ruler span { transition: none; } }
</style>
