<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { billingApi, type BillingProduct, type BillingStatus } from '@/api/billing'
import { useShellStore } from '@/stores/shell'
import { useUsageStore } from '@/stores/usage'

const shell = useShellStore()
const usage = useUsageStore()
const products = ref<BillingProduct[]>([])
const billingStatus = ref<BillingStatus | null>(null)
const billingLoading = ref(false)

onMounted(() => {
  shell.setCrumb('用量与计费')
  void usage.load(true).catch(() => undefined)
  billingLoading.value = true
  void Promise.all([billingApi.products(), billingApi.status()]).then(([availableProducts, status]) => {
    products.value = availableProducts
    billingStatus.value = status
  }).catch(() => undefined).finally(() => { billingLoading.value = false })
})

const data = computed(() => usage.summary)
const remainingPct = computed(() => {
  if (!data.value?.quota) return 0
  return Math.max(0, Math.min(100, Math.round(data.value.remaining / data.value.quota * 100)))
})
const maxDaily = computed(() => Math.max(1, ...(data.value?.daily.map((item) => item.credits) ?? [1])))

function shortDate(value: string) {
  const date = new Date(`${value}T00:00:00`)
  return `${date.getMonth() + 1}/${date.getDate()}`
}

function fullTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(new Date(value))
}

function resetDate(value: string | null) {
  if (!value) return '尚未安排'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric'
  }).format(new Date(value))
}
</script>

<template>
  <div class="usage-page">
    <div v-if="usage.loading && !data" class="usage-state">正在核对用量台账…</div>
    <div v-else-if="usage.error && !data" class="usage-state" data-error>
      <strong>用量台账读取失败</strong><span>{{ usage.error }}</span>
      <button type="button" @click="usage.load(true)">重新读取</button>
    </div>
    <template v-else-if="data">
      <header class="usage-statement">
        <div class="statement-title">
          <span>MONTHLY MODEL LEDGER</span>
          <h1>{{ data.plan_label }}用量单</h1>
          <p>每笔模型调用按实际 token 结算，失败与中断不会扣除积分。</p>
        </div>
        <div class="balance-block">
          <span>当前可用</span>
          <strong>{{ data.available.toLocaleString() }}</strong>
          <small>月度 {{ data.remaining.toLocaleString() }} · 充值 {{ data.purchased_remaining.toLocaleString() }}</small>
        </div>
      </header>

      <div class="quota-rule" role="progressbar" :aria-valuenow="remainingPct" aria-valuemin="0" aria-valuemax="100">
        <span :style="{ width: remainingPct + '%' }" />
      </div>

      <main class="usage-ledger">
        <section class="ledger-main">
          <div class="section-line"><h2>本期去向</h2><span>已结算 {{ data.spent.toLocaleString() }} 积分</span></div>
          <div v-if="data.items.length" class="breakdown-table">
            <div class="table-head"><span>功能</span><span>调用</span><span>输入 / 输出</span><span>积分</span></div>
            <div v-for="item in data.items" :key="item.feature" class="table-row">
              <strong>{{ item.label }}</strong>
              <span>{{ item.count }} 次<small v-if="item.user_key_count"> · 自带 {{ item.user_key_count }}</small></span>
              <span class="mono">{{ item.prompt_tokens.toLocaleString() }} / {{ item.completion_tokens.toLocaleString() }}</span>
              <b>{{ item.credits.toLocaleString() }}</b>
            </div>
          </div>
          <div v-else class="empty-ledger">本期还没有已结算的模型调用。</div>

          <div class="section-line recent-head"><h2>逐笔明细</h2><span>最近 20 笔</span></div>
          <div v-if="data.recent.length" class="recent-list">
            <div v-for="item in data.recent" :key="item.id" class="recent-row">
              <time>{{ fullTime(item.timestamp) }}</time>
              <div><strong>{{ item.label }}</strong><small>{{ item.model ?? '未记录模型' }}{{ item.billing_mode === 'user_key' ? ' · 自带模型' : '' }}</small></div>
              <span class="mono">{{ item.prompt_tokens.toLocaleString() }} + {{ item.completion_tokens.toLocaleString() }} tok</span>
              <b>{{ item.billing_mode === 'user_key' ? '自付' : `-${item.credits}` }}</b>
            </div>
          </div>
          <div v-else class="empty-ledger">生成正文后，实际 token 和积分会逐笔出现在这里。</div>
        </section>

        <aside class="ledger-side">
          <section>
            <div class="section-line"><h2>近 14 天</h2><span>每日积分</span></div>
            <div class="daily-chart" aria-label="近 14 天积分用量">
              <div v-for="item in data.daily" :key="item.date" class="day-column" :title="`${item.date} · ${item.credits} 积分`">
                <span class="day-value">{{ item.credits || '' }}</span>
                <i :style="{ height: Math.max(2, item.credits / maxDaily * 100) + '%' }" />
                <small>{{ shortDate(item.date) }}</small>
              </div>
            </div>
          </section>

          <section class="pricing-section">
            <div class="section-line"><h2>当前计价</h2><span>积分 / 千 token</span></div>
            <dl>
              <div><dt>基础档</dt><dd>输入 {{ data.rates.basic_input }} · 输出 {{ data.rates.basic_output }}</dd></div>
              <div><dt>高级档</dt><dd>输入 {{ data.rates.advanced_input }} · 输出 {{ data.rates.advanced_output }}</dd></div>
              <div><dt>缓存输入</dt><dd>按输入价 {{ data.rates.cached_percent }}% 折算</dd></div>
            </dl>
            <p>下次额度重置：{{ resetDate(data.resets_at) }}</p>
          </section>

          <section class="recharge-section">
            <div class="section-line"><h2>充值积分</h2><span>人民币 / 一次性</span></div>
            <div v-if="billingLoading" class="billing-note">正在读取商品…</div>
            <template v-else-if="products.length">
              <div v-for="product in products" :key="product.id" class="product-row">
                <div><strong>{{ product.name }}</strong><small>{{ product.credits.toLocaleString() }} 积分</small></div>
                <b>¥{{ (product.amount_minor / 100).toFixed(2) }}</b>
              </div>
              <p class="billing-note">支付渠道：{{ billingStatus?.providers.wechat.configured ? '微信支付' : '' }}{{ billingStatus?.providers.wechat.configured && billingStatus?.providers.alipay.configured ? '、' : '' }}{{ billingStatus?.providers.alipay.configured ? '支付宝' : '' }}{{ !billingStatus?.providers.wechat.configured && !billingStatus?.providers.alipay.configured ? '待管理员配置' : '' }}</p>
            </template>
            <p v-else class="billing-note">充值商品尚未发布。管理员配置微信支付或支付宝后，这里会显示可购买的积分包。</p>
          </section>
        </aside>
      </main>
    </template>
  </div>
</template>

<style scoped>
.usage-page { height: 100%; overflow: auto; background: var(--panel); color: var(--ink); }
.usage-state { min-height: 100%; display: grid; place-content: center; justify-items: center; gap: 10px; color: var(--ink-3); }
.usage-state[data-error] strong { color: var(--alert-ink); }
.usage-state button { height: 34px; padding: 0 14px; border: 1px solid var(--line-strong); background: var(--paper); color: var(--ink); cursor: pointer; }
.usage-statement { min-height: 188px; padding: 34px clamp(24px, 5vw, 72px) 28px; display: flex; align-items: flex-end; justify-content: space-between; gap: 36px; border-bottom: var(--hair) solid var(--line); background: var(--paper); }
.statement-title > span { color: var(--ink-3); font: 700 10px/1 var(--font-mono); }
.statement-title h1 { margin: 10px 0 9px; font: 600 clamp(25px, 3vw, 35px)/1.2 var(--font-prose); letter-spacing: 0; }
.statement-title p { margin: 0; color: var(--ink-2); }
.balance-block { min-width: 220px; text-align: right; }
.balance-block span, .balance-block small { display: block; color: var(--ink-3); }
.balance-block strong { display: block; margin: 4px 0; font: 700 44px/1 var(--font-mono); }
.balance-block small { font-family: var(--font-mono); }
.quota-rule { height: 5px; background: var(--panel-sunken); }
.quota-rule span { display: block; height: 100%; background: var(--primary); transition: width .2s ease; }
.usage-ledger { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(330px, .55fr); max-width: 1240px; margin: 0 auto; }
.ledger-main, .ledger-side { padding: 32px clamp(24px, 4vw, 48px) 56px; }
.ledger-main { border-right: var(--hair) solid var(--line); }
.ledger-side { background: var(--panel-sunken); }
.ledger-side section + section { margin-top: 40px; }
.section-line { min-height: 34px; display: flex; align-items: baseline; justify-content: space-between; gap: 16px; border-bottom: 2px solid var(--ink); }
.section-line h2 { margin: 0; font-size: 15px; }
.section-line span { color: var(--ink-3); font-size: 11px; }
.table-head, .table-row { display: grid; grid-template-columns: minmax(150px, 1.2fr) 70px minmax(130px, 1fr) 64px; align-items: center; gap: 12px; }
.table-head { min-height: 34px; color: var(--ink-3); font-size: 10px; border-bottom: var(--hair) solid var(--line); }
.table-head span:last-child, .table-row b { text-align: right; }
.table-row { min-height: 55px; border-bottom: var(--hair) solid var(--line); }
.table-row strong { font-size: 13px; }
.table-row b { font-family: var(--font-mono); }
.mono { font-family: var(--font-mono); font-size: 11px; color: var(--ink-2); }
.empty-ledger { min-height: 90px; display: grid; place-items: center; border-bottom: var(--hair) solid var(--line); color: var(--ink-3); }
.recent-head { margin-top: 38px; }
.recent-row { min-height: 58px; display: grid; grid-template-columns: 82px minmax(140px, 1fr) minmax(130px, auto) 46px; align-items: center; gap: 12px; border-bottom: var(--hair) solid var(--line); }
.recent-row time { color: var(--ink-3); font: 10px/1 var(--font-mono); }
.recent-row div { min-width: 0; }
.recent-row strong, .recent-row small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-row small { margin-top: 3px; color: var(--ink-3); }
.recent-row b { text-align: right; font-family: var(--font-mono); }
.daily-chart { height: 182px; padding-top: 24px; display: grid; grid-template-columns: repeat(14, 1fr); align-items: end; gap: 4px; border-bottom: var(--hair) solid var(--line-strong); }
.day-column { height: 100%; display: grid; grid-template-rows: 18px 1fr 22px; align-items: end; text-align: center; }
.day-column i { width: min(14px, 70%); min-height: 2px; justify-self: center; background: var(--primary); }
.day-column small, .day-value { color: var(--ink-3); font: 8px/1 var(--font-mono); }
.day-column small { writing-mode: vertical-rl; justify-self: center; padding-top: 4px; }
.pricing-section dl { margin: 0; }
.pricing-section dl div { min-height: 46px; display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: var(--hair) solid var(--line); }
.pricing-section dt { color: var(--ink-2); }
.pricing-section dd { margin: 0; font: 11px/1 var(--font-mono); }
.pricing-section p { margin: 14px 0 0; color: var(--ink-3); font-size: 11px; }
.recharge-section { padding-top: 2px; }
.recharge-section .section-line { margin-bottom: 2px; }
.product-row { min-height: 52px; display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: var(--hair) solid var(--line); }
.product-row strong, .product-row small { display: block; }
.product-row small { margin-top: 3px; color: var(--ink-3); font: 10px/1 var(--font-mono); }
.product-row b { font: 700 13px/1 var(--font-mono); }
.billing-note { margin: 12px 0 0; color: var(--ink-3); font-size: 11px; line-height: 1.6; }
button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
@media (max-width: 860px) {
  .usage-statement { align-items: flex-start; flex-direction: column; }
  .balance-block { text-align: left; }
  .usage-ledger { grid-template-columns: 1fr; }
  .ledger-main { border-right: 0; border-bottom: var(--hair) solid var(--line); }
}
@media (max-width: 560px) {
  .table-head { display: none; }
  .table-row { grid-template-columns: 1fr auto; padding: 9px 0; }
  .table-row span { display: none; }
  .recent-row { grid-template-columns: 64px 1fr 34px; }
  .recent-row > .mono { display: none; }
}
@media (prefers-reduced-motion: reduce) { .quota-rule span { transition: none; } }
</style>
