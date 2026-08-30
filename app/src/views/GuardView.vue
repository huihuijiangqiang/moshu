<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useGuardStore } from '@/stores/guard'
import { useProjectStore } from '@/stores/project'
import { useShellStore } from '@/stores/shell'
import type { GuardIssue, GuardKind } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'

/**
 * 一致性守卫：主从布局。左列是按严重度排序的告警行，右侧是证据与处置。
 * 关键是「一眼看清冲突的两处原文」——所以证据用左边线对照排版，
 * 本次命中的那一条用红边标出，另一条是被它违反的旧文或设定。
 */
const guard = useGuardStore()
const project = useProjectStore()
const shell = useShellStore()
const router = useRouter()
const { toProject } = useProjectNavigation()

const selectedId = ref<string | null>(null)

onMounted(() => {
  shell.setCrumb('一致性守卫')
})

const tabs: { key: GuardKind | 'resolved'; label: string; note: string }[] = [
  { key: 'conflict', label: '设定冲突', note: '需要你判断' },
  { key: 'foreshadow', label: '伏笔待回收', note: '超期会持续提醒' },
  { key: 'pending-entry', label: '新设定待确认', note: '自动抽取，确认后入库' },
  { key: 'resolved', label: '已处置', note: '本卷累计' }
]

/** 高严重度排前面，这是唯一的优先级信号——不引入黄绿蓝色阶 */
const rows = computed<GuardIssue[]>(() =>
  [...guard.visible].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === 'high' ? -1 : 1))
)

const selected = computed(() => rows.value.find((i) => i.id === selectedId.value) ?? rows.value[0] ?? null)

watch(rows, (list) => {
  if (!list.some((i) => i.id === selectedId.value)) selectedId.value = list[0]?.id ?? null
}, { immediate: true })

function act(issue: GuardIssue, action: string) {
  if (action.includes('查看时间线')) {
    router.push(toProject('outline'))
    return
  }
  if (action.includes('改写') || action.includes('回到正文') || action.includes('补一段')) {
    openIssueChapter(issue)
    return
  }

  // 更新设定、确认忽略等动作由 mock API 记录为已处置；正文修改必须由作者完成后再消警。
  guard.resolve(issue.id)
}

function openIssueChapter(issue: GuardIssue) {
  const indexes = [...issue.chapterRef.matchAll(/\d+/g)].map((match) => Number(match[0]))
  const chapter = [...indexes]
    .reverse()
    .map((index) => project.chapters.find((item) => item.index === index))
    .find(Boolean)

  router.push({
    path: toProject('write'),
    query: chapter ? { chapter: chapter.id } : undefined
  })
}
</script>

<template>
  <div class="guard-view" :style="{ display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', height: '100%', background: 'var(--canvas)' }">
    <!-- 顶部四个计数。数字大、标签小，扫一眼就知道要不要进来处理 -->
    <div
      class="guard-summary"
      :style="{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr) auto',
        gap: 'var(--rule)', background: 'var(--line-strong)',
        borderBottom: 'var(--rule) solid var(--line-strong)'
      }"
    >
      <button
        v-for="t in tabs"
        :key="t.key"
        type="button"
        class="guard-summary-item"
        :aria-selected="guard.tab === t.key"
        :style="{
          textAlign: 'left', border: 0, cursor: 'pointer', padding: 'var(--u3) var(--u4)',
          background: guard.tab === t.key ? 'var(--panel)' : 'var(--panel-sunken)',
          boxShadow: guard.tab === t.key ? 'inset 0 -2px 0 var(--alert)' : 'none'
        }"
        @click="guard.tab = t.key"
      >
        <span
          :style="{
            display: 'block', fontFamily: 'var(--font-mono)', fontSize: '30px', fontWeight: 700, lineHeight: 1,
            color: t.key === 'conflict' && guard.counts[t.key] ? 'var(--alert)' : 'var(--ink)'
          }"
        >{{ guard.counts[t.key] }}</span>
        <span :style="{ display: 'block', marginTop: '6px', fontSize: 'var(--fs)', fontWeight: 700 }">{{ t.label }}</span>
        <span :style="{ display: 'block', marginTop: '2px', fontSize: 'var(--fs-sm)', color: 'var(--ink-3)' }">{{ t.note }}</span>
      </button>

      <div class="guard-scan" :style="{ background: 'var(--panel-sunken)', padding: 'var(--u3) var(--u4)', display: 'grid', alignContent: 'center', gap: '6px' }">
        <span :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', whiteSpace: 'nowrap' }">
          上次全量扫描 · 8 月 27 日 23:10<br>
          {{ project.totalChapters }} 章 / {{ (project.totalWords / 10000).toFixed(1) }} 万字
        </span>
        <button class="wk-btn" type="button" :disabled="guard.scanning" @click="guard.rescan()">
          {{ guard.scanning ? '扫描中…' : '重新全量扫描' }}
        </button>
      </div>
    </div>

    <div class="wk-cols guard-workspace" :style="{ gridTemplateColumns: '340px minmax(0, 1fr)' }">
      <!-- 告警列表 -->
      <div class="wk-pane" aria-label="告警列表">
        <div class="wk-head">
          <span>{{ tabs.find((t) => t.key === guard.tab)?.label }}</span>
          <span class="wk-head-push">{{ rows.length }} 条</span>
        </div>

        <button
          v-for="i in rows"
          :key="i.id"
          class="wk-row"
          type="button"
          role="option"
          :aria-selected="i.id === selected?.id"
          :style="{ minHeight: 'auto', padding: '7px var(--u3)', display: 'grid', gap: '3px', alignItems: 'start' }"
          @click="selectedId = i.id"
        >
          <span class="row" :style="{ gap: '6px', width: '100%' }">
            <span class="pill" :class="i.severity === 'high' ? 'pill-alert' : ''">
              {{ i.severity === 'high' ? '高' : '中' }}
            </span>
            <span :style="{ fontWeight: 700, color: 'var(--ink)' }">{{ i.category }}</span>
            <span class="wk-row-meta" :style="{ marginLeft: 'auto' }">{{ i.chapterRef }}</span>
          </span>
          <span :style="{ fontSize: 'var(--fs-sm)', color: 'var(--ink-2)', lineHeight: 1.55, whiteSpace: 'normal' }">
            {{ i.title }}
          </span>
        </button>

        <p v-if="!rows.length" :style="{ padding: 'var(--u5) var(--u3)', color: 'var(--ink-3)', lineHeight: 1.7 }">
          这一类没有待处理项。<br>
          {{ guard.tab === 'conflict' ? '全书前后一致，继续写。' : '' }}
        </p>
      </div>

      <!-- 证据与处置 -->
      <main class="wk-pane wk-pane-paper guard-detail" aria-label="告警详情">
        <template v-if="selected">
          <div class="paper-bar">
            <span class="pill" :class="selected.severity === 'high' ? 'pill-alert' : ''">
              {{ selected.severity === 'high' ? '高' : '中' }}
            </span>
            <span :style="{ fontWeight: 700, color: 'var(--ink)', fontSize: 'var(--fs)' }">{{ selected.category }}</span>
            <span :style="{ color: 'var(--ink-3)' }">{{ selected.chapterRef }}</span>
            <span :style="{ marginLeft: 'auto' }" />
            <button class="wk-btn wk-btn-xs" type="button" @click="openIssueChapter(selected)">打开对应章节</button>
          </div>

          <div :style="{ maxWidth: '820px', padding: 'var(--u6)' }">
            <h2 :style="{ fontSize: 'var(--fs-xl)', fontWeight: 700, margin: '0 0 var(--u3)', lineHeight: 1.45 }">
              {{ selected.title }}
            </h2>
            <p
              v-if="selected.detail"
              :style="{ margin: '0 0 var(--u5)', fontSize: 'var(--fs-md)', lineHeight: 1.9, color: 'var(--ink-2)' }"
            >{{ selected.detail }}</p>

            <!-- 两处原文对照。本次命中用红边，被违反的旧文/设定用灰边 -->
            <template v-if="selected.evidence.length">
              <div class="wk-label" :style="{ marginBottom: 'var(--u3)' }">证据对照</div>
              <div
                v-for="(ev, n) in selected.evidence"
                :key="n"
                :style="{
                  borderLeft: '3px solid ' + (ev.accent ? 'var(--alert)' : 'var(--line-strong)'),
                  background: ev.accent ? 'var(--alert-soft)' : 'var(--panel-sunken)',
                  padding: 'var(--u3) var(--u4)', marginBottom: 'var(--u3)'
                }"
              >
                <div
                  :style="{
                    fontSize: 'var(--fs-xs)', fontWeight: 700, letterSpacing: '0.06em', marginBottom: '6px',
                    color: ev.accent ? 'var(--alert-ink)' : 'var(--ink-3)'
                  }"
                >{{ ev.label }}</div>
                <p :style="{ margin: 0, fontFamily: 'var(--font-prose)', fontSize: 'var(--fs-lg)', lineHeight: 1.95, color: 'var(--ink)' }">
                  {{ ev.text }}
                </p>
              </div>
            </template>

            <!-- 处置。第一项是推荐动作，误报单独一条，用于持续调准 -->
            <div :style="{ marginTop: 'var(--u6)', paddingTop: 'var(--u4)', borderTop: 'var(--hair) solid var(--line)' }">
              <div class="wk-label" :style="{ marginBottom: 'var(--u3)' }">处置</div>
              <div class="row" :style="{ flexWrap: 'wrap', gap: 'var(--u2)' }">
                <button
                  v-for="(a, n) in selected.actions"
                  :key="a"
                  class="wk-btn"
                  type="button"
                  :data-primary="n === 0"
                  @click="act(selected, a)"
                >{{ a }}</button>
                <button
                  class="wk-btn"
                  type="button"
                  :style="{ marginLeft: 'auto', borderColor: 'transparent', color: 'var(--ink-3)' }"
                  @click="guard.resolve(selected.id)"
                >这是误报</button>
              </div>
              <p :style="{ margin: 'var(--u3) 0 0', fontSize: 'var(--fs-sm)', color: 'var(--ink-3)', lineHeight: 1.7 }">
                处置结果会回写设定库。标记误报不会改动正文，只用于持续调准扫描规则。
              </p>
            </div>
          </div>
        </template>

        <p v-else :style="{ padding: 'var(--u6)', color: 'var(--ink-3)', lineHeight: 1.8 }">
          没有需要处理的告警。<br>
          守卫会在你每写完一章后增量扫描，发现问题再回到这里。
        </p>
      </main>
    </div>
  </div>
</template>
