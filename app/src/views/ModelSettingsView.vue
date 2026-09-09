<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { modelConfigApi, type UserModelConfig } from '@/api/model-config'
import { useShellStore } from '@/stores/shell'

const shell = useShellStore()
const config = ref<UserModelConfig | null>(null)
const loading = ref(true)
const busy = ref(false)
const message = ref('')
const error = ref('')
const deleteArmed = ref(false)
const form = reactive({
  providerName: '',
  baseUrl: '',
  model: '',
  contextWindowTokens: 32768,
  maxOutputTokens: 4096,
  contextSafetyMarginTokens: 2048,
  apiKey: '',
  enabled: true
})

const configured = computed(() => config.value?.configured === true)
const routeState = computed(() => {
  if (!configured.value) return '平台模型'
  if (!form.enabled) return '已停用，使用平台模型'
  return `${form.providerName || '自定义服务'} / ${form.model || '未选择模型'}`
})
const testLabel = computed(() => {
  if (!configured.value || config.value?.lastTestStatus === 'untested') return '尚未测试'
  return config.value?.lastTestStatus === 'ok' ? '连接正常' : '连接失败'
})

function apply(value: UserModelConfig) {
  config.value = value
  form.providerName = value.providerName
  form.baseUrl = value.baseUrl
  form.model = value.model
  form.contextWindowTokens = value.contextWindowTokens
  form.maxOutputTokens = value.maxOutputTokens
  form.contextSafetyMarginTokens = value.contextSafetyMarginTokens
  form.apiKey = ''
  form.enabled = value.configured ? value.enabled : true
  deleteArmed.value = false
}

function readableError(value: unknown, fallback: string) {
  if (!(value instanceof Error)) return fallback
  try {
    const code = JSON.parse(value.message)?.detail?.code as string | undefined
    const labels: Record<string, string> = {
      MODEL_ENDPOINT_NOT_ALLOWED: '服务地址必须是公网 HTTPS 地址，且不能包含账号、查询参数或片段。',
      MODEL_API_KEY_REQUIRED: '首次保存必须填写 API Key。',
      MODEL_API_KEY_INVALID: 'API Key 格式无效，不能包含空格。',
      MODEL_CONFIG_REVISION_CONFLICT: '配置已在其他页面更新，请刷新后再修改。'
    }
    if (code && labels[code]) return labels[code]
  } catch { /* use the bounded fallback below */ }
  return fallback
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    apply(await modelConfigApi.get())
  } catch (reason) {
    error.value = readableError(reason, '模型配置读取失败，请重新加载。')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!config.value || busy.value) return
  busy.value = true
  message.value = ''
  error.value = ''
  try {
    apply(await modelConfigApi.save({
      providerName: form.providerName.trim(),
      baseUrl: form.baseUrl.trim(),
      model: form.model.trim(),
      contextWindowTokens: form.contextWindowTokens,
      maxOutputTokens: form.maxOutputTokens,
      contextSafetyMarginTokens: form.contextSafetyMarginTokens,
      ...(form.apiKey.trim() ? { apiKey: form.apiKey.trim() } : {}),
      enabled: form.enabled,
      revision: config.value.revision
    }))
    message.value = '模型服务已保存'
  } catch (reason) {
    error.value = readableError(reason, '保存失败，请核对地址、模型名和密钥。')
  } finally {
    busy.value = false
  }
}

async function testConnection() {
  if (!config.value?.configured || busy.value) return
  busy.value = true
  message.value = ''
  error.value = ''
  try {
    apply(await modelConfigApi.test(config.value.revision))
    message.value = config.value.lastTestStatus === 'ok'
      ? '连接测试通过'
      : '服务已响应，但认证或模型列表接口不可用'
  } catch (reason) {
    error.value = readableError(reason, '连接测试失败，请检查服务是否可访问。')
  } finally {
    busy.value = false
  }
}

async function remove() {
  if (!config.value?.configured || busy.value) return
  if (!deleteArmed.value) {
    deleteArmed.value = true
    return
  }
  busy.value = true
  message.value = ''
  error.value = ''
  try {
    await modelConfigApi.remove(config.value.revision)
    apply(await modelConfigApi.get())
    message.value = '自定义模型服务已删除，生成已切回平台模型'
  } catch (reason) {
    error.value = readableError(reason, '删除失败，请刷新后重试。')
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  shell.setCrumb('我的模型服务')
  void load()
})
</script>

<template>
  <div class="model-page">
    <div v-if="loading" class="model-state">正在读取模型配置…</div>
    <div v-else-if="!config" class="model-state" data-error>
      <strong>模型配置无法读取</strong>
      <button class="wk-btn" type="button" @click="load">重新加载</button>
    </div>
    <template v-else>
      <header class="model-header">
        <div>
          <span class="model-kicker">GENERATION ROUTE</span>
          <h1>我的模型服务</h1>
          <p>用于一键成章和行内写作。后台抽取与一致性检查仍由平台承担。</p>
        </div>
        <label class="model-switch">
          <input v-model="form.enabled" type="checkbox" :disabled="!configured || busy">
          <span>{{ configured && form.enabled ? '使用自定义服务' : '使用平台服务' }}</span>
        </label>
      </header>

      <div class="route-strip" :data-custom="configured && form.enabled">
        <span>本次正文</span><i aria-hidden="true" /><strong>{{ routeState }}</strong>
      </div>

      <main class="model-layout">
        <form class="model-form" @submit.prevent="save">
          <div class="section-title"><h2>连接信息</h2><span>OpenAI 兼容接口</span></div>
          <label>
            <span>服务名称</span>
            <input v-model="form.providerName" required maxlength="100" placeholder="例如：我的中转站">
          </label>
          <label>
            <span>Base URL</span>
            <input v-model="form.baseUrl" required maxlength="1000" inputmode="url" placeholder="https://api.example.com/v1">
          </label>
          <label>
            <span>模型名</span>
            <input v-model="form.model" required maxlength="200" placeholder="服务端实际支持的模型 ID">
          </label>
          <div class="section-title model-capacity-title"><h2>模型容量</h2><span>按服务商公开参数填写</span></div>
          <label>
            <span>上下文窗口</span>
            <input v-model.number="form.contextWindowTokens" required type="number" min="4096" max="2000000" step="1024">
          </label>
          <label>
            <span>最大输出</span>
            <input v-model.number="form.maxOutputTokens" required type="number" min="256" max="131072" step="256">
          </label>
          <label>
            <span>安全余量</span>
            <input v-model.number="form.contextSafetyMarginTokens" required type="number" min="256" max="262144" step="256">
          </label>
          <label>
            <span>API Key</span>
            <input
              v-model="form.apiKey"
              :required="!configured"
              type="password"
              maxlength="512"
              autocomplete="new-password"
              :placeholder="configured ? `已保存 ${config.keyHint ?? '密钥'}，留空则不更换` : '输入 API Key'"
            >
          </label>

          <div class="model-actions">
            <button class="wk-btn" data-primary="true" type="submit" :disabled="busy">
              {{ busy ? '处理中…' : configured ? '保存更改' : '保存服务' }}
            </button>
            <button class="wk-btn" type="button" :disabled="!configured || busy" @click="testConnection">测试连接</button>
          </div>
          <p v-if="message" class="form-message" data-success>{{ message }}</p>
          <p v-if="error" class="form-message" data-error>{{ error }}</p>
        </form>

        <aside class="model-audit">
          <div class="section-title"><h2>当前状态</h2><span>只显示密钥掩码</span></div>
          <dl>
            <div><dt>生成来源</dt><dd>{{ configured && form.enabled ? '自定义服务' : '平台服务' }}</dd></div>
            <div><dt>连接检查</dt><dd :data-status="config.lastTestStatus">{{ testLabel }}</dd></div>
            <div><dt>密钥</dt><dd class="mono">{{ config.keyHint ?? '未保存' }}</dd></div>
            <div><dt>上下文窗口</dt><dd class="mono">{{ config.contextWindowTokens.toLocaleString() }}</dd></div>
            <div><dt>配置版本</dt><dd class="mono">{{ config.revision || '—' }}</dd></div>
          </dl>
          <p class="security-note">密钥加密保存，之后不会再次显示。更换密钥时直接输入新值并保存。</p>
          <div v-if="configured" class="danger-zone">
            <button type="button" :disabled="busy" @click="remove">
              {{ deleteArmed ? '再次点击，确认删除' : '删除自定义服务' }}
            </button>
            <button v-if="deleteArmed" type="button" @click="deleteArmed = false">取消</button>
          </div>
        </aside>
      </main>
    </template>
  </div>
</template>

<style scoped>
.model-page { height: 100%; min-width: 0; overflow: auto; overflow-x: hidden; color: var(--ink); background: var(--panel); }
.model-state { min-height: 100%; display: grid; place-content: center; justify-items: center; gap: var(--u3); color: var(--ink-3); }
.model-state[data-error] strong { color: var(--alert-ink); }
.model-header { min-height: 190px; display: flex; align-items: flex-end; justify-content: space-between; gap: 32px; padding: 36px clamp(24px, 5vw, 72px) 30px; background: var(--paper); border-bottom: var(--hair) solid var(--line); }
.model-kicker { color: var(--ink-4); font: 700 10px/1 var(--font-mono); }
.model-header h1 { margin: 10px 0 9px; font: 600 34px/1.2 var(--font-prose); letter-spacing: 0; }
.model-header p { max-width: 600px; margin: 0; color: var(--ink-2); line-height: 1.7; }
.model-switch { flex: none; display: flex; align-items: center; gap: 9px; min-height: 34px; padding: 0 11px; color: var(--ink-2); border: var(--hair) solid var(--line-strong); background: var(--panel); cursor: pointer; }
.model-switch input { accent-color: var(--primary); }
.model-switch:has(input:disabled) { cursor: default; opacity: .6; }
.route-strip { min-height: 48px; display: grid; grid-template-columns: auto minmax(80px, 1fr) auto; align-items: center; gap: 12px; padding: 0 clamp(24px, 5vw, 72px); color: var(--ink-3); background: var(--panel-sunken); border-bottom: var(--hair) solid var(--line); font-size: var(--fs-sm); }
.route-strip i { height: 1px; position: relative; background: var(--line-strong); }
.route-strip i::after { content: ''; position: absolute; right: 0; top: -3px; width: 6px; height: 6px; border-top: 1px solid var(--ink-4); border-right: 1px solid var(--ink-4); transform: rotate(45deg); }
.route-strip strong { color: var(--ink); font-weight: 650; }
.route-strip[data-custom='true'] strong { color: var(--primary); }
.model-layout { max-width: 1120px; margin: 0 auto; display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(280px, .65fr); }
.model-form, .model-audit { box-sizing: border-box; width: 100%; min-width: 0; padding: 34px clamp(24px, 4vw, 48px) 56px; }
.model-form { display: grid; gap: 18px; border-right: var(--hair) solid var(--line); background: var(--paper); }
.model-capacity-title { margin-top: 8px; }
.model-audit { background: var(--panel-sunken); }
.section-title { min-height: 34px; display: flex; align-items: baseline; justify-content: space-between; gap: 16px; border-bottom: 2px solid var(--ink); }
.section-title h2 { margin: 0; font-size: 15px; }
.section-title span { color: var(--ink-4); font-size: var(--fs-xs); }
.model-form label { display: grid; grid-template-columns: 118px minmax(0, 1fr); align-items: center; gap: 16px; }
.model-form label > span { color: var(--ink-2); font-size: var(--fs-sm); }
.model-form input { box-sizing: border-box; width: 100%; min-width: 0; height: 36px; padding: 0 10px; color: var(--ink); background: var(--panel); border: var(--hair) solid var(--line-strong); border-radius: 2px; }
.model-form input:focus { outline: 2px solid var(--primary-line); outline-offset: 1px; border-color: var(--primary); }
.model-actions { display: flex; flex-wrap: wrap; gap: var(--u2); padding-top: var(--u2); }
.form-message { margin: -6px 0 0; font-size: var(--fs-sm); line-height: 1.6; }
.form-message[data-success] { color: var(--primary); }
.form-message[data-error] { color: var(--alert-ink); }
.model-audit dl { margin: 0; }
.model-audit dl > div { min-height: 50px; display: flex; align-items: center; justify-content: space-between; gap: 18px; border-bottom: var(--hair) solid var(--line); }
.model-audit dt { color: var(--ink-3); }
.model-audit dd { margin: 0; text-align: right; color: var(--ink); }
.model-audit dd[data-status='ok'] { color: var(--primary); }
.model-audit dd[data-status='failed'] { color: var(--alert-ink); }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }
.security-note { margin: 18px 0 0; color: var(--ink-3); font-size: var(--fs-xs); line-height: 1.7; }
.danger-zone { display: flex; align-items: center; gap: 14px; margin-top: 34px; padding-top: 14px; border-top: var(--hair) solid var(--line-strong); }
.danger-zone button { padding: 0; color: var(--alert-ink); background: transparent; border: 0; cursor: pointer; font-size: var(--fs-xs); }
.danger-zone button + button { color: var(--ink-3); }
button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
@media (max-width: 760px) {
  .model-header { align-items: flex-start; flex-direction: column; min-height: 0; }
  .route-strip { grid-template-columns: auto minmax(32px, 1fr) minmax(0, auto); }
  .route-strip strong { min-width: 0; overflow-wrap: anywhere; text-align: right; }
  .model-layout { grid-template-columns: minmax(0, 1fr); }
  .model-form { border-right: 0; border-bottom: var(--hair) solid var(--line); }
}
@media (max-width: 520px) {
  .model-page { padding-bottom: 72px; }
  .route-strip { grid-template-columns: auto minmax(20px, 1fr); padding-top: 10px; padding-bottom: 10px; }
  .route-strip strong { grid-column: 1 / -1; text-align: left; }
  .section-title span { min-width: 0; text-align: right; overflow-wrap: anywhere; }
  .model-form label { grid-template-columns: 1fr; gap: 6px; }
  .model-header h1 { font-size: 28px; }
}
</style>
