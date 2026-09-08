<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ApiError } from '@/api/http'
import { accountSecurityApi, type AuthSession } from '@/api/account-security'
import AppIcon from '@/components/ui/AppIcon.vue'
import { clearSession } from '@/api/session'

const sessions = ref<AuthSession[]>([])
const loading = ref(true)
const errorMessage = ref('')
const actionMessage = ref('')
const busySession = ref('')
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordBusy = ref(false)

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function sessionLabel(session: AuthSession) {
  if (session.current) return '当前设备'
  return session.active ? '已登录设备' : '已结束'
}

async function loadSessions() {
  loading.value = true
  errorMessage.value = ''
  try {
    sessions.value = await accountSecurityApi.listSessions()
  } catch {
    errorMessage.value = '无法加载会话，请稍后重试。'
  } finally {
    loading.value = false
  }
}

async function revoke(session: AuthSession) {
  if (busySession.value) return
  busySession.value = session.id
  errorMessage.value = ''
  try {
    await accountSecurityApi.revokeSession(session.id)
    if (session.current) {
      clearSession()
      window.location.assign('/login')
    }
    else await loadSessions()
  } catch {
    errorMessage.value = '会话撤销失败，请稍后重试。'
  } finally {
    busySession.value = ''
  }
}

async function changePassword() {
  actionMessage.value = ''
  errorMessage.value = ''
  if (newPassword.value.length < 8) {
    errorMessage.value = '新密码至少需要 8 位。'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    errorMessage.value = '两次输入的新密码不一致。'
    return
  }
  passwordBusy.value = true
  try {
    await accountSecurityApi.changePassword(currentPassword.value, newPassword.value)
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    actionMessage.value = '密码已更新，其他设备的登录已失效。'
    await loadSessions()
  } catch (error) {
    if (error instanceof ApiError && error.status === 400) {
      try {
        const body = JSON.parse(error.message) as { detail?: { code?: string } }
        errorMessage.value = body.detail?.code === 'CURRENT_PASSWORD_INVALID' ? '当前密码不正确。' : '密码更新条件不满足。'
      } catch {
        errorMessage.value = '密码更新失败，请检查当前密码。'
      }
    } else {
      errorMessage.value = '密码更新失败，请稍后重试。'
    }
  } finally {
    passwordBusy.value = false
  }
}

onMounted(() => { void loadSessions() })
</script>

<template>
  <main class="security-page">
    <header class="security-header">
      <div>
        <span class="eyebrow">ACCOUNT / SECURITY</span>
        <h1>账户安全</h1>
        <p>管理登录会话和密码。撤销会话会立即阻止该设备继续访问作品。</p>
      </div>
    </header>
    <div class="security-grid">
      <section class="security-section">
        <div class="section-title"><AppIcon name="key" :size="16" /><h2>修改密码</h2></div>
        <form class="security-form" @submit.prevent="changePassword">
          <label><span>当前密码</span><input v-model="currentPassword" type="password" autocomplete="current-password" required></label>
          <label><span>新密码</span><input v-model="newPassword" type="password" autocomplete="new-password" minlength="8" required></label>
          <label><span>确认新密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" required></label>
          <p class="field-note">更新后当前设备保持登录，其他设备需要重新登录。</p>
          <p v-if="actionMessage" class="success" role="status">{{ actionMessage }}</p>
          <p v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</p>
          <button class="wk-btn" data-primary="true" type="submit" :disabled="passwordBusy">{{ passwordBusy ? '保存中…' : '更新密码' }}</button>
        </form>
      </section>
      <section class="security-section sessions-section">
        <div class="section-title"><AppIcon name="history" :size="16" /><h2>登录会话</h2><span>{{ sessions.filter((s) => s.active).length }} 个有效</span></div>
        <p class="section-note">只显示本账号的会话元数据，不会显示访问令牌。</p>
        <p v-if="loading" class="empty-state">正在加载…</p>
        <p v-else-if="errorMessage && !sessions.length" class="empty-state error" role="alert">{{ errorMessage }}</p>
        <p v-else-if="!sessions.length" class="empty-state">暂无登录会话。</p>
        <ul v-else class="session-list">
          <li v-for="session in sessions" :key="session.id" :data-current="session.current">
            <div class="session-main">
              <strong>{{ sessionLabel(session) }}</strong>
              <small>最近使用 {{ formatDate(session.last_used_at) }}</small>
              <small>创建于 {{ formatDate(session.created_at) }}</small>
            </div>
            <button v-if="session.active" class="wk-btn wk-btn-xs" type="button" :disabled="Boolean(busySession)" @click="revoke(session)">{{ busySession === session.id ? '撤销中…' : session.current ? '退出此设备' : '撤销' }}</button>
            <span v-else class="session-ended">已撤销</span>
          </li>
        </ul>
      </section>
    </div>
  </main>
</template>

<style scoped>
.security-page { min-height: 100%; overflow: auto; background: var(--panel); color: var(--ink); }
.security-header { padding: 44px clamp(24px, 6vw, 84px) 34px; border-bottom: var(--hair) solid var(--line); background: var(--paper); }
.eyebrow { color: var(--ink-4); font: 10px/1 var(--font-mono); letter-spacing: .08em; }
.security-header h1 { margin: 10px 0 8px; font: 600 30px/1.2 var(--font-prose); }
.security-header p { max-width: 620px; margin: 0; color: var(--ink-3); line-height: 1.6; }
.security-grid { display: grid; grid-template-columns: minmax(300px, .8fr) minmax(360px, 1.2fr); max-width: 1120px; margin: 0 auto; }
.security-section { padding: 34px clamp(24px, 4vw, 52px) 52px; }
.security-section + .security-section { border-left: var(--hair) solid var(--line); }
.section-title { display: flex; align-items: center; gap: 8px; min-height: 34px; border-bottom: 2px solid var(--ink); }
.section-title h2 { margin: 0; font-size: 16px; }
.section-title span { margin-left: auto; color: var(--ink-3); font: 10px/1 var(--font-mono); }
.security-form { display: grid; gap: 15px; padding-top: 22px; }
.security-form label { display: grid; gap: 7px; color: var(--ink-3); font-size: 12px; font-weight: 700; }
.security-form input { height: 40px; width: 100%; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); padding: 0 10px; color: var(--ink); font: inherit; }
.field-note, .section-note { margin: 0; color: var(--ink-4); font-size: 11px; line-height: 1.6; }
.success { margin: 0; color: var(--ok-ink, #2f6b49); font-size: 11px; }
.error { margin: 0; color: var(--alert-ink); font-size: 11px; }
.security-form .wk-btn { justify-self: start; }
.session-list { display: grid; gap: 0; padding: 14px 0 0; margin: 0; list-style: none; }
.session-list li { min-height: 72px; display: flex; align-items: center; gap: 16px; border-bottom: var(--hair) solid var(--line); }
.session-list li[data-current="true"] { border-left: 3px solid var(--primary); padding-left: 10px; }
.session-main { display: grid; gap: 4px; min-width: 0; flex: 1; }
.session-main strong { font-size: 13px; }
.session-main small { color: var(--ink-3); font: 10px/1.3 var(--font-mono); }
.session-list .wk-btn { flex: 0 0 auto; }
.session-ended { color: var(--ink-4); font-size: 11px; }
.empty-state { padding: 30px 0; color: var(--ink-3); font-size: 12px; }
@media (max-width: 800px) {
  .security-grid { grid-template-columns: 1fr; }
  .security-section + .security-section { border-top: var(--hair) solid var(--line); border-left: 0; }
}
</style>
