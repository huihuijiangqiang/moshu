<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { shelfApi, type ShelfBook } from '@/api/mock/shelf'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const store = useProjectStore()
const books = ref<ShelfBook[]>([])
const series = ref<number[]>([])

onMounted(async () => {
  books.value = await shelfApi.listBooks()
  series.value = await shelfApi.dailySeries()
})

const peak = computed(() => Math.max(1, ...series.value))
const wan = (n: number) => (n / 10000).toFixed(1)
</script>

<template>
  <div class="app">
    <header class="row-between rule-b" :style="{ height: 'var(--app-header-h)', padding: '0 18px', flex: 'none' }">
      <span :style="{ fontWeight: 700, fontSize: '15px', letterSpacing: '0.06em' }">墨枢</span>
      <div class="row" :style="{ fontSize: '13px', gap: '16px' }">
        <span class="muted">本月积分 <strong :style="{ color: 'var(--color-text)' }">2,840</strong> / 5,000</span>
        <span :style="{ width: '2px', height: '20px', background: 'var(--color-divider)' }" />
        <RouterLink to="/usage" :style="{ border: 0, color: 'var(--color-neutral-700)' }">用量</RouterLink>
        <span :style="{ width: '26px', height: '26px', background: 'var(--color-text)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '12px' }">沈</span>
      </div>
    </header>

    <main class="pane" :style="{ flex: 1, padding: '40px 44px', background: 'var(--color-neutral-100)' }">
      <div class="row-between" :style="{ alignItems: 'end', marginBottom: '36px' }">
        <div>
          <div :style="{ fontSize: '34px', fontWeight: 700, lineHeight: 1.2 }">
            今天已写 {{ (store.project?.dailyWords ?? 0).toLocaleString() }} 字
          </div>
          <div class="muted" :style="{ marginTop: '8px', fontSize: '13px' }">
            连续更新 43 天 · 距今日目标还差
            {{ Math.max(0, (store.project?.dailyGoal ?? 0) - (store.project?.dailyWords ?? 0)).toLocaleString() }} 字
          </div>
        </div>
        <button class="btn btn-primary" type="button" :style="{ height: '42px', fontSize: '14px' }" @click="router.push('/wizard')">
          开新书
        </button>
      </div>

      <div class="grid-rule">
        <article
          v-for="b in books"
          :key="b.id"
          :style="{ padding: '28px 0', display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr 130px', gap: '32px', alignItems: 'center', fontSize: '13px' }"
        >
          <div>
            <div class="row" :style="{ gap: '10px', marginBottom: '8px' }">
              <span class="tag" :class="b.status === 'ongoing' ? 'tag-accent' : 'tag-neutral'" :style="{ fontSize: '11px' }">
                {{ b.status === 'ongoing' ? '连载中' : '已完结' }}
              </span>
              <span class="muted">{{ b.genre }}</span>
            </div>
            <h2 :style="{ fontSize: '24px', fontWeight: 700, margin: 0, color: b.status === 'ongoing' ? 'var(--color-text)' : 'var(--color-neutral-800)' }">
              {{ b.title }}
            </h2>
            <div class="muted" :style="{ marginTop: '6px' }">上次写于 {{ b.lastTouched }}</div>
          </div>
          <div><div class="num" :style="{ fontSize: '26px' }">{{ wan(b.words) }}</div><div class="muted" :style="{ marginTop: '6px' }">万字 · {{ b.chapters }} 章</div></div>
          <div><div class="num" :style="{ fontSize: '26px' }">{{ b.codexCount }}</div><div class="muted" :style="{ marginTop: '6px' }">设定条目</div></div>
          <div>
            <div class="num" :style="{ fontSize: '26px', color: b.guardOpen ? 'var(--color-accent)' : 'inherit' }">{{ b.guardOpen }}</div>
            <div class="muted" :style="{ marginTop: '6px' }">守卫待处理</div>
          </div>
          <button
            class="btn"
            :class="b.status === 'ongoing' ? 'btn-primary' : 'btn-secondary'"
            type="button"
            :style="{ height: '38px', fontSize: '13px' }"
            @click="router.push('/write')"
          >{{ b.status === 'ongoing' ? '继续写' : '打开' }}</button>
        </article>
      </div>

      <div class="rule-t" :style="{ marginTop: '36px', paddingTop: '28px', display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '32px', fontSize: '13px' }">
        <div>
          <div class="kicker" :style="{ marginBottom: '12px' }">近 14 天</div>
          <div class="row" :style="{ alignItems: 'end', gap: '4px', height: '60px' }">
            <span
              v-for="(v, i) in series"
              :key="i"
              :style="{
                flex: 1,
                height: Math.round((v / peak) * 100) + '%',
                minHeight: v ? '2px' : '0',
                background: i === series.length - 1 ? 'var(--color-accent)' : 'var(--color-neutral-400)'
              }"
            />
          </div>
        </div>
        <div>
          <div class="kicker" :style="{ marginBottom: '12px' }">AI 采纳率</div>
          <div class="num" :style="{ fontSize: '34px' }">61%</div>
          <p :style="{ margin: '8px 0 0', lineHeight: 1.6, color: 'var(--color-neutral-800)' }">
            本月 AI 产出中被你留在正文里的比例。低于 40% 说明生成参数需要调。
          </p>
        </div>
        <div>
          <div class="kicker" :style="{ marginBottom: '12px' }">数据归你</div>
          <p :style="{ margin: 0, lineHeight: 1.7, color: 'var(--color-neutral-800)' }">
            正文、设定库、大纲随时一键全量导出，不受订阅状态影响。
            <RouterLink to="/export">去导出</RouterLink>
          </p>
        </div>
      </div>
    </main>
  </div>
</template>
