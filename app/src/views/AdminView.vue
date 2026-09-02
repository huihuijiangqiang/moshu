<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { adminApi, type AdminOverview, type AdminSettings, type AdminUser } from '@/api/admin'
import { getSessionUser } from '@/api/session'
import { useShellStore } from '@/stores/shell'

const shell = useShellStore()
const overview = ref<AdminOverview | null>(null)
const users = ref<AdminUser[]>([])
const settings = ref<AdminSettings | null>(null)
const tab = ref<'users' | 'settings'>('users')
const search = ref('')
const loading = ref(true)
const message = ref('')
const currentUser = getSessionUser()
const canPromote = computed(() => currentUser?.system_role === 'super_admin')

async function load() {
  loading.value = true
  message.value = ''
  try {
    ;[overview.value, users.value, settings.value] = await Promise.all([
      adminApi.overview(), adminApi.users(search.value), adminApi.settings()
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
      default_monthly_quota: settings.value.default_monthly_quota
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

    <main v-else-if="!loading && settings" class="admin-content admin-settings">
      <section>
        <h2>账号默认值</h2>
        <label class="admin-field"><span>开放注册</span><input v-model="settings.registration_enabled" type="checkbox"></label>
        <label class="admin-field"><span>默认套餐</span><select v-model="settings.default_plan"><option value="free">免费</option><option value="author">作者</option><option value="studio">工作室</option></select></label>
        <label class="admin-field"><span>每月默认额度</span><input v-model.number="settings.default_monthly_quota" type="number" min="0"></label>
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
.admin-tabs button[aria-selected="true"] { border-bottom-color: var(--accent); color: var(--ink); font-weight: 700; }
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
.admin-field { min-height: 48px; display: grid; grid-template-columns: 150px 1fr; align-items: center; border-top: var(--hair) solid var(--line); }
.admin-definition { margin: 0; border-top: var(--hair) solid var(--line); }
.admin-definition div { min-height: 44px; display: grid; grid-template-columns: 130px 1fr; align-items: center; border-bottom: var(--hair) solid var(--line); }
.admin-definition dt { color: var(--ink-3); }.admin-definition dd { margin: 0; font-family: var(--font-mono); }
.admin-settings p { color: var(--ink-3); line-height: 1.7; }.admin-loading { padding: 40px 28px; color: var(--ink-3); }
@media (max-width: 900px) { .admin-summary { grid-template-columns: repeat(2, 1fr); }.admin-settings { grid-template-columns: 1fr; gap: 36px; } }
</style>
