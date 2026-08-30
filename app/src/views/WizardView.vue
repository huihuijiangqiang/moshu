<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { projectPath } from '@/router/project-route'

const router = useRouter()
const step = ref(3)

const steps = [
  { n: '01', title: '一句灵感', note: '已完成' },
  { n: '02', title: '题材与模板', note: '男频 · 边关权谋' },
  { n: '03', title: '设定与总纲', note: '进行中 · 请确认' },
  { n: '04', title: '生成前三章', note: '待开始' }
]

const blocks = [
  {
    kicker: '主角',
    title: '沈砚 · 三十二岁 · 北境守将',
    body: '寡言，动手快于开口。左肩旧伤遇寒即痛。缺陷：认死理，不肯托人情，所以四年没升。渴望：查清师父二十年前为何弃剑。'
  },
  {
    kicker: '金手指',
    title: '淬铁 · 以器听人',
    body: '能听见兵器上残留的持有者情绪。限制：每次听完必伤，境界越低伤越重——这条限制比能力本身重要，它决定后续所有战斗写法。'
  }
]

const volumes = [
  ['第一卷 少年出山', '从守关小校到被迫接印，立住「认死理」的人设，埋玄铁令。'],
  ['第二卷 北境风雪', '外敌试探与内部倾轧同时压来，残锋断，师父线浮出。'],
  ['第三卷 旧盟', '二十年前真相揭开一半，主角第一次主动违令。'],
  ['第四卷 同断', '破境与代价，收玄铁令，与师父旧敌对决。']
]

const why = [
  ['主角要有一个「挡路的缺陷」', '新人最常见的问题是主角万事顺遂，读者没有代入的缝隙。「认死理所以四年没升」既是缺陷也是后续所有冲突的引信。'],
  ['金手指的限制比能力重要', '没有代价的能力会让战斗失去张力。「听完必伤」让每次使用都是一次选择，也自然形成升级线。'],
  ['伏笔在第一卷就埋', '玄铁令会进入伏笔追踪，超过 30 章未提会提醒你。你不需要自己记。'],
  ['黄金三章的规矩', '下一步生成的前三章会把冲突放在第一章前 500 字，世界观分散交代——不会开篇讲三千字设定。']
]
</script>

<template>
  <div class="app">
    <header class="row-between rule-b" :style="{ height: 'var(--app-header-h)', padding: '0 18px', flex: 'none' }">
      <span :style="{ fontWeight: 700, fontSize: '15px', letterSpacing: '0.06em' }">墨枢 · 开新书</span>
      <span class="muted" :style="{ fontSize: '13px' }">随时退出，进度自动保留</span>
    </header>

    <div class="grid-rule rule-b" :style="{ gridTemplateColumns: 'repeat(4, 1fr)', flex: 'none' }">
      <div
        v-for="(s, i) in steps"
        :key="s.n"
        class="row"
        :style="{
          padding: '18px 22px', gap: '14px', alignItems: 'baseline',
          background: i + 1 === step ? 'var(--color-neutral-100)' : 'var(--color-bg)',
          boxShadow: i + 1 === step ? 'inset 0 0 0 2px var(--color-accent)' : 'none'
        }"
      >
        <span class="num" :style="{ fontSize: '22px', color: i + 1 === step ? 'var(--color-accent)' : 'var(--color-neutral-500)' }">
          {{ s.n }}
        </span>
        <span :style="{ fontSize: '13px' }">
          <span :style="{ fontWeight: 700, color: i + 1 === step ? 'var(--color-text)' : 'var(--color-neutral-700)' }">{{ s.title }}</span><br>
          <span :style="{ marginTop: '3px', display: 'inline-block', color: i + 1 === step ? 'var(--color-accent-700)' : 'var(--color-neutral-600)', fontWeight: i + 1 === step ? 700 : 400 }">
            {{ s.note }}
          </span>
        </span>
      </div>
    </div>

    <div class="app-body" :style="{ gridTemplateColumns: '1fr 360px' }">
      <main class="pane" :style="{ padding: '34px 40px', background: 'var(--color-neutral-100)' }">
        <h1 :style="{ fontSize: '28px', fontWeight: 700, margin: '0 0 10px' }">这是 AI 给你搭的骨架</h1>
        <p :style="{ fontSize: '15px', lineHeight: 1.65, margin: '0 0 30px', maxWidth: '60ch', color: 'var(--color-neutral-800)' }">
          全部可以直接改。改完点「确认并生成前三章」，这些内容会成为整本书的稳定设定，之后每一章的生成都以它为准。
        </p>

        <div class="grid-rule">
          <section v-for="b in blocks" :key="b.kicker" :style="{ padding: '20px 0' }">
            <div class="row-between" :style="{ marginBottom: '12px', alignItems: 'baseline' }">
              <span class="kicker">{{ b.kicker }}</span>
              <button class="chip chip-ghost" type="button" :style="{ color: 'var(--color-accent-700)', fontWeight: 700 }">换一个</button>
            </div>
            <h2 :style="{ fontSize: '19px', fontWeight: 700, margin: '0 0 8px' }">{{ b.title }}</h2>
            <p :style="{ margin: 0, lineHeight: 1.8, fontSize: '13px' }">{{ b.body }}</p>
          </section>

          <section :style="{ padding: '20px 0' }">
            <div class="row-between" :style="{ marginBottom: '12px', alignItems: 'baseline' }">
              <span class="kicker">总纲 · 四卷</span>
              <button class="chip chip-ghost" type="button" :style="{ color: 'var(--color-accent-700)', fontWeight: 700 }">展开细纲</button>
            </div>
            <div :style="{ display: 'grid', gap: '10px', fontSize: '13px', lineHeight: 1.7 }">
              <div v-for="[t, d] in volumes" :key="t"><strong>{{ t }}</strong> · {{ d }}</div>
            </div>
          </section>
        </div>

        <div class="row" :style="{ gap: '10px', marginTop: '30px' }">
          <button class="btn btn-primary" type="button" :style="{ height: '40px', fontSize: '14px' }" @click="router.push(projectPath('p1', 'write'))">
            确认并生成前三章
          </button>
          <button class="btn btn-secondary" type="button" :style="{ height: '40px', fontSize: '14px' }">全部重新生成</button>
        </div>
      </main>

      <aside class="pane pane-right" :style="{ padding: '26px 24px' }">
        <div class="kicker" :style="{ color: 'var(--color-accent)', marginBottom: '20px' }">为什么这样写</div>
        <div :style="{ display: 'grid', gap: '22px', fontSize: '13px' }">
          <div v-for="([t, d], i) in why" :key="t" :class="i ? 'rule-t' : ''" :style="i ? { paddingTop: '22px' } : {}">
            <div :style="{ fontWeight: 700, fontSize: '15px', marginBottom: '8px' }">{{ t }}</div>
            <p :style="{ margin: 0, lineHeight: 1.75, color: 'var(--color-neutral-800)' }">{{ d }}</p>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
