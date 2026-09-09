<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { accountSecurityApi } from '@/api/account-security'
import { ApiError } from '@/api/http'

const route = useRoute()
const router = useRouter()
const token = computed(() => typeof route.query.token === 'string' ? route.query.token : '')
const password = ref('')
const confirmation = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')

async function submit() {
  message.value = ''
  error.value = ''
  if (!token.value) {
    error.value = '重置链接缺少凭据，请重新申请。'
    return
  }
  if (password.value.length < 8 || password.value !== confirmation.value) {
    error.value = password.value.length < 8 ? '新密码至少需要 8 位。' : '两次输入的新密码不一致。'
    return
  }
  busy.value = true
  try {
    await accountSecurityApi.confirmPasswordReset(token.value, password.value)
    message.value = '密码已重置，请使用新密码登录。'
    password.value = ''
    confirmation.value = ''
  } catch (cause) {
    error.value = cause instanceof ApiError && cause.status === 400 ? '重置链接无效或已过期。' : '暂时无法重置密码，请稍后重试。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="reset-page">
    <form class="reset-form" @submit.prevent="submit">
      <span class="eyebrow">ACCOUNT / PASSWORD</span>
      <h1>设置新密码</h1>
      <p>重置链接仅可使用一次，并会让所有现有设备退出登录。</p>
      <label><span>新密码</span><input v-model="password" type="password" autocomplete="new-password" minlength="8" required></label>
      <label><span>确认新密码</span><input v-model="confirmation" type="password" autocomplete="new-password" minlength="8" required></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="message" class="success" role="status">{{ message }}</p>
      <div class="actions">
        <button class="wk-btn" type="button" @click="router.push({ name: 'login' })">返回登录</button>
        <button class="wk-btn" data-primary="true" type="submit" :disabled="busy">{{ busy ? '提交中…' : '确认重置' }}</button>
      </div>
    </form>
  </main>
</template>

<style scoped>
.reset-page { min-height: 100%; display: grid; place-items: center; padding: 24px; background: var(--color-bg); color: var(--color-text); }
.reset-form { width: min(440px, 100%); display: grid; gap: 16px; padding: 38px; border: 1px solid var(--color-divider); background: var(--color-neutral-100); }
.eyebrow { color: var(--color-neutral-800); font: 10px/1 var(--font-mono); letter-spacing: .08em; }
h1 { margin: 0; font-size: 26px; }
.reset-form > p { margin: -5px 0 8px; color: var(--color-neutral-800); font-size: 12px; line-height: 1.6; }
label { display: grid; gap: 7px; font-size: 12px; font-weight: 700; }
input { height: 42px; border: 1px solid var(--color-divider); padding: 0 11px; background: var(--color-bg); color: var(--color-text); font: inherit; }
.error, .success { margin: 0 !important; }
.error { color: var(--alert); }
.success { color: var(--color-accent); }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
</style>
