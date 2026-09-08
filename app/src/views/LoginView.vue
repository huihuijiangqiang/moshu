<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '@/api/auth'
import { ApiError } from '@/api/http'
import { accountSecurityApi } from '@/api/account-security'

type Mode = 'login' | 'register' | 'forgot'

const route = useRoute()
const router = useRouter()
const mode = ref<Mode>('login')
const name = ref('')
const email = ref('')
const password = ref('')
const submitting = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

const canSubmit = computed(() => {
  const emailValid = email.value.trim().includes('@')
  if (mode.value === 'forgot') return emailValid && !submitting.value
  const credentialsValid = emailValid && password.value.length >= 8
  return credentialsValid && (mode.value === 'login' || !!name.value.trim()) && !submitting.value
})

function switchMode(next: Mode) {
  mode.value = next
  errorMessage.value = ''
  successMessage.value = ''
}

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    if (mode.value === 'forgot') {
      await accountSecurityApi.requestPasswordReset(email.value)
      successMessage.value = '如果该邮箱已注册，邮件服务会发送一次性重置链接。请检查收件箱。'
      return
    }
    if (mode.value === 'login') await authApi.login(email.value, password.value)
    else await authApi.register(name.value, email.value, password.value)
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
      ? route.query.redirect
      : '/'
    await router.replace(redirect)
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) errorMessage.value = '邮箱或密码不正确。'
    else if (error instanceof ApiError && error.status === 409) errorMessage.value = '这个邮箱已经注册，请直接登录。'
    else errorMessage.value = '暂时无法完成登录，请检查服务连接后重试。'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-frame">
      <div class="auth-intro">
        <div>
          <div class="auth-brand">墨枢</div>
          <p class="auth-folio">MANUSCRIPT / 001</p>
          <h1>把故事写长，<br>也把前因后果留住。</h1>
        </div>
        <p class="auth-note">进入你的书架，继续当前作品。</p>
      </div>

      <form class="auth-form" @submit.prevent="submit">
        <div class="auth-mode" role="tablist" aria-label="账号操作">
          <button type="button" :aria-selected="mode === 'login'" @click="switchMode('login')">登录</button>
          <button type="button" :aria-selected="mode === 'register'" @click="switchMode('register')">创建账号</button>
        </div>

        <div class="auth-heading">
          <span>{{ mode === 'forgot' ? '找回密码' : mode === 'login' ? '继续写作' : '建立你的书架' }}</span>
          <small>{{ mode === 'forgot' ? '输入账号邮箱，我们会发送一次性重置链接' : mode === 'login' ? '使用邮箱和密码进入' : '账号创建后即可开新书' }}</small>
        </div>

        <label v-if="mode === 'register'">
          <span>显示名称</span>
          <input v-model="name" autocomplete="name" placeholder="你的笔名或称呼">
        </label>
        <label>
          <span>邮箱</span>
          <input v-model="email" autocomplete="email" inputmode="email" placeholder="name@example.com">
        </label>
        <label v-if="mode !== 'forgot'">
          <span>密码</span>
          <input v-model="password" autocomplete="current-password" type="password" placeholder="至少 8 位">
        </label>

        <p v-if="errorMessage" class="auth-error" role="alert">{{ errorMessage }}</p>
        <p v-if="successMessage" class="auth-success" role="status">{{ successMessage }}</p>
        <button class="auth-submit" type="submit" :disabled="!canSubmit">
          {{ submitting ? '处理中...' : mode === 'forgot' ? '发送重置链接' : mode === 'login' ? '进入书架' : '创建并进入' }}
        </button>
        <button v-if="mode === 'login'" class="auth-link" type="button" @click="switchMode('forgot')">忘记密码？</button>
        <button v-if="mode === 'forgot'" class="auth-link" type="button" @click="switchMode('login')">返回登录</button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.auth-page {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 40px;
  background: var(--color-bg);
}

.auth-frame {
  width: min(980px, 100%);
  min-height: 520px;
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(340px, .85fr);
  border: 1px solid var(--color-divider);
  background: var(--color-neutral-100);
  box-shadow: 16px 18px 0 color-mix(in srgb, var(--color-divider) 38%, transparent);
}

.auth-intro {
  padding: 54px 50px 44px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border-right: 1px solid var(--color-divider);
  background-image: linear-gradient(var(--color-divider) 1px, transparent 1px);
  background-size: 100% 34px;
}

.auth-brand { font-size: 19px; font-weight: 800; letter-spacing: 0; }
.auth-folio { margin: 70px 0 16px; font-size: 11px; font-family: monospace; color: var(--color-neutral-800); }
.auth-intro h1 { margin: 0; max-width: 12ch; font-size: 38px; line-height: 1.34; letter-spacing: 0; }
.auth-note { margin: 0; font-size: 14px; color: var(--color-neutral-800); }

.auth-form { padding: 54px 42px; display: flex; flex-direction: column; justify-content: center; }
.auth-mode { display: grid; grid-template-columns: 1fr 1fr; margin-bottom: 34px; border-bottom: 1px solid var(--color-divider); }
.auth-mode button { border: 0; border-bottom: 3px solid transparent; padding: 11px 4px; background: transparent; color: var(--color-neutral-800); cursor: pointer; }
.auth-mode button[aria-selected="true"] { border-bottom-color: var(--color-accent); color: var(--color-text); font-weight: 800; }
.auth-heading { display: flex; flex-direction: column; gap: 7px; margin-bottom: 26px; }
.auth-heading span { font-size: 22px; font-weight: 800; }
.auth-heading small { color: var(--color-neutral-800); }
.auth-form label { display: grid; gap: 8px; margin-bottom: 18px; font-size: 12px; font-weight: 700; }
.auth-form input { height: 44px; min-width: 0; border: 1px solid var(--color-divider); padding: 0 13px; background: var(--color-bg); color: var(--color-text); font: inherit; }
.auth-form input:focus-visible, .auth-mode button:focus-visible, .auth-submit:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }
.auth-error { margin: 0 0 14px; padding: 10px 12px; border-left: 3px solid var(--alert); background: color-mix(in srgb, var(--alert) 8%, transparent); color: var(--color-text); }
.auth-success { margin: 0 0 14px; padding: 10px 12px; border-left: 3px solid var(--color-accent); background: color-mix(in srgb, var(--color-accent) 8%, transparent); color: var(--color-text); line-height: 1.5; }
.auth-submit { height: 44px; border: 0; background: var(--color-accent); color: #fff; font-weight: 800; cursor: pointer; }
.auth-submit:disabled { cursor: not-allowed; opacity: .45; }
.auth-link { margin: -4px 0 0; border: 0; background: transparent; color: var(--color-neutral-800); font-size: 12px; cursor: pointer; text-align: center; }
.auth-link:hover { color: var(--color-accent); }

@media (max-width: 760px) {
  .auth-page { padding: 18px; place-items: start center; }
  .auth-frame { grid-template-columns: 1fr; min-height: 0; box-shadow: 8px 10px 0 color-mix(in srgb, var(--color-divider) 38%, transparent); }
  .auth-intro { min-height: 250px; padding: 30px 28px; border-right: 0; border-bottom: 1px solid var(--color-divider); }
  .auth-folio { margin-top: 34px; }
  .auth-intro h1 { font-size: 28px; }
  .auth-form { padding: 32px 28px 38px; }
}
</style>
