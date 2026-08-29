<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const phone = ref('')
const code = ref('')
const sent = ref(false)

const canSend = computed(() => /^1\d{10}$/.test(phone.value))
const canSubmit = computed(() => canSend.value && code.value.length === 6)

function send() { if (canSend.value) sent.value = true }
function submit() { if (canSubmit.value) router.push('/') }
</script>

<template>
  <div :style="{ minHeight: '100%', display: 'grid', placeItems: 'center', padding: '40px', background: 'var(--color-bg)' }">
    <div :style="{ width: '100%', maxWidth: '980px', border: '2px solid var(--color-divider)', background: 'var(--color-neutral-100)', display: 'grid', gridTemplateColumns: '1.2fr 1fr', minHeight: '480px', fontSize: '13px' }">
      <div :style="{ padding: '56px 48px', borderRight: '2px solid var(--color-divider)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }">
        <div>
          <div :style="{ fontWeight: 700, fontSize: '17px', letterSpacing: '0.06em', marginBottom: '48px' }">墨枢</div>
          <h1 :style="{ fontSize: '40px', fontWeight: 700, lineHeight: 1.24, letterSpacing: '-0.01em', margin: '0 0 24px' }">
            能一键出稿，<br>更能在第八十万字<br>不出错
          </h1>
          <p :style="{ margin: 0, fontSize: '16px', lineHeight: 1.7, maxWidth: '40ch', color: 'var(--color-neutral-800)' }">
            AI 铺量，你定调。设定库记住你写过的每一条规矩，一致性守卫替你盯着前后矛盾和没收的伏笔。
          </p>
        </div>
        <div class="row muted" :style="{ gap: '32px', paddingTop: '40px' }">
          <span>导出永久免费</span><span>不用你的稿子训练模型</span><span>随时可删</span>
        </div>
      </div>

      <form :style="{ padding: '56px 40px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }" @submit.prevent="submit">
        <div :style="{ fontSize: '22px', fontWeight: 700, marginBottom: '28px' }">登录 / 注册</div>

        <label class="kicker" :style="{ marginBottom: '10px' }">手机号</label>
        <input v-model="phone" class="input" inputmode="numeric" placeholder="请输入手机号" :style="{ marginBottom: '18px', height: '44px' }">

        <label class="kicker" :style="{ marginBottom: '10px' }">验证码</label>
        <div class="row" :style="{ gap: '10px', marginBottom: '26px' }">
          <input v-model="code" class="input" inputmode="numeric" maxlength="6" placeholder="6 位数字" :style="{ flex: 1, height: '44px' }">
          <button class="btn btn-secondary" type="button" :disabled="!canSend" :style="{ height: '44px', fontSize: '13px' }" @click="send">
            {{ sent ? '已发送' : '获取' }}
          </button>
        </div>

        <button class="btn btn-primary" type="submit" :disabled="!canSubmit" :style="{ width: '100%', height: '44px', fontSize: '14px', marginBottom: '20px' }">
          进入
        </button>
        <p class="muted" :style="{ margin: 0, lineHeight: 1.7 }">
          未注册的手机号将直接创建账号。继续即表示同意服务条款与隐私政策。
        </p>
      </form>
    </div>
  </div>
</template>
