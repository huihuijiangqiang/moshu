<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { shelfApi } from '@/api/mock/shelf'
import { useShellStore } from '@/stores/shell'

const shell = useShellStore()
const data = ref<Awaited<ReturnType<typeof shelfApi.usage>> | null>(null)
onMounted(async () => {
  shell.setCrumb('用量与计费')
  data.value = await shelfApi.usage()
})

const pct = computed(() => (data.value ? Math.round((data.value.remaining / data.value.quota) * 100) : 0))

const tiers = [
  { name: '基础档', price: '不计积分', current: true, note: '国产主力模型。中文网文语感好，日更够用。' },
  { name: '高级档', price: '35 积分 / 章', current: false, note: '情感戏与关键章推荐。也可自带 API 密钥，不计积分。' }
]
</script>

<template>
  <div class="wk-pane" :style="{ height: '100%', overflow: 'auto' }">
    <main v-if="data" class="pane" :style="{ flex: 1, padding: '40px', background: 'var(--panel)' }">
      <div :style="{ maxWidth: '560px', fontSize: '13px' }">
        <div class="row-between" :style="{ alignItems: 'end', marginBottom: '8px' }">
          <div>
            <div class="num" :style="{ fontSize: '40px' }">{{ data.remaining.toLocaleString() }}</div>
            <div class="muted" :style="{ marginTop: '6px' }">剩余积分 · 每月 1 日重置</div>
          </div>
          <div class="muted" :style="{ textAlign: 'right' }">
            <div><strong :style="{ color: 'var(--color-text)' }">{{ data.plan }}</strong> · {{ data.price }}</div>
            <div :style="{ marginTop: '4px' }">每月 {{ data.quota.toLocaleString() }} 积分</div>
          </div>
        </div>
        <div :style="{ height: '8px', background: 'var(--color-neutral-300)', margin: '18px 0 26px' }">
          <div :style="{ width: pct + '%', height: '100%', background: 'var(--color-accent)' }" />
        </div>

        <div class="kicker" :style="{ marginBottom: '14px' }">积分怎么花的 · 本月</div>
        <div class="grid-rule" :style="{ marginBottom: '26px' }">
          <div v-for="it in data.items" :key="it.label" class="row-between" :style="{ padding: '12px 0' }">
            <span :style="{ color: it.credits === 'free' ? 'var(--color-neutral-700)' : 'inherit' }">
              {{ it.label }} · {{ it.count }}
            </span>
            <strong>{{ it.credits === 'free' ? '免费' : it.credits.toLocaleString() }}</strong>
          </div>
        </div>

        <div class="kicker" :style="{ marginBottom: '14px' }">模型档位</div>
        <div class="grid-rule" :style="{ marginBottom: '22px' }">
          <div
            v-for="t in tiers"
            :key="t.name"
            :style="{ padding: '16px 14px', boxShadow: t.current ? 'inset 0 0 0 2px var(--color-accent)' : 'none' }"
          >
            <div class="row-between" :style="{ marginBottom: '6px' }">
              <strong>{{ t.name }}</strong>
              <span :style="{ color: t.current ? 'var(--color-accent-700)' : 'var(--color-neutral-700)', fontWeight: t.current ? 700 : 400 }">
                {{ t.current ? '当前 · ' : '' }}{{ t.price }}
              </span>
            </div>
            <div :style="{ color: 'var(--color-neutral-800)', lineHeight: 1.6 }">{{ t.note }}</div>
          </div>
        </div>

        <p :style="{ margin: 0, color: 'var(--color-neutral-800)', lineHeight: 1.75 }">
          积分不足时会提示降档而不是静默失败。<strong>自带密钥的用户只付订阅费。</strong>
        </p>
      </div>
    </main>
  </div>
</template>
