<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useCodexStore } from '@/stores/codex'
import { useGuardStore } from '@/stores/guard'
import { useShellStore } from '@/stores/shell'
import { CODEX_KIND_LABEL } from '@/types'
import { useProjectNavigation } from '@/composables/use-project-navigation'

/**
 * ⌘K 命令面板 —— 密集工具台的主入口。
 * 三类结果混排：导航、章节、设定条目。作者写到第 88 章时不会去点侧栏，
 * 会敲「玄铁」直接跳条目，所以搜索必须跨类型。
 */
interface Cmd {
  id: string
  group: string
  label: string
  hint?: string
  run: () => void
}

const router = useRouter()
const route = useRoute()
const project = useProjectStore()
const codex = useCodexStore()
const guard = useGuardStore()
const shell = useShellStore()
const { inProject, toProject } = useProjectNavigation()

const query = ref('')
const cursor = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)
const listEl = ref<HTMLElement | null>(null)

const navCmds = computed<Cmd[]>(() => [
  ...(inProject.value ? [
    { id: 'nav-write', group: '当前作品', label: '写作台', hint: 'G W', run: () => router.push(toProject('write')) },
    { id: 'nav-outline', group: '当前作品', label: '大纲', hint: 'G O', run: () => router.push(toProject('outline')) },
    { id: 'nav-codex', group: '当前作品', label: `设定库 · ${codex.entries.length} 条`, hint: 'G C', run: () => router.push(toProject('codex')) },
    { id: 'nav-guard', group: '当前作品', label: `一致性守卫 · ${guard.open.length} 条待处理`, hint: 'G G', run: () => router.push(toProject('guard')) },
    { id: 'nav-style', group: '当前作品', label: '风格档', run: () => router.push(toProject('style')) },
    { id: 'nav-ratio', group: '当前作品', label: 'AI 来源账本', run: () => router.push(toProject('ai-ratio')) },
    { id: 'nav-export', group: '当前作品', label: '导出作品', run: () => router.push(toProject('export')) }
  ] : []),
  { id: 'nav-shelf', group: '全局', label: '作品库', run: () => router.push('/') },
  { id: 'nav-deconstruct', group: '全局', label: '拆书分析', run: () => router.push('/deconstruct') },
  { id: 'nav-usage', group: '全局', label: '用量与计费', run: () => router.push('/usage') },
  { id: 'nav-model-settings', group: '全局', label: '我的模型服务', run: () => router.push('/model-settings') }
])

const actionCmds = computed<Cmd[]>(() => [
  ...(route.name === 'workspace' ? [{
    id: 'act-zen',
    group: '操作',
    label: shell.zen ? '退出纯净模式' : '进入纯净模式（只留正文）',
    hint: '⌘\\',
    run: () => shell.toggleZen()
  },
  {
    id: 'act-left',
    group: '操作',
    label: shell.leftOpen ? '收起章节栏' : '展开章节栏',
    hint: '⌘B',
    run: () => (shell.leftOpen = !shell.leftOpen)
  },
  {
    id: 'act-right',
    group: '操作',
    label: shell.rightOpen ? '收起 AI 面板' : '展开 AI 面板',
    hint: '⌘J',
    run: () => (shell.rightOpen = !shell.rightOpen)
  }] : []),
  {
    id: 'act-theme',
    group: '操作',
    label: shell.theme === 'dark' ? '切到浅色主题' : '切到深色主题',
    run: () => shell.toggleTheme()
  },
  ...(inProject.value ? [{ id: 'act-rescan', group: '操作', label: '重新扫描当前作品一致性', run: () => guard.rescan() }] : [])
])

const chapterCmds = computed<Cmd[]>(() =>
  inProject.value ? project.chapters.map((c) => ({
    id: 'ch-' + c.id,
    group: '章节',
    label: `第${c.index}章　${c.title || '未命名'}`,
    hint: c.status === 'outlined' ? '有章纲' : (c.words / 1000).toFixed(1) + 'k',
    run: () => {
      project.openChapter(c.id)
      router.push(toProject('write'))
    }
  })) : []
)

const codexCmds = computed<Cmd[]>(() =>
  inProject.value ? codex.entries.map((e) => ({
    id: 'cx-' + e.id,
    group: '设定',
    label: e.name,
    hint: CODEX_KIND_LABEL[e.kind] + (e.resident ? ' · 常驻' : ''),
    run: () => {
      codex.kind = e.kind
      codex.query = e.name
      router.push(toProject('codex'))
    }
  })) : []
)

/** 匹配名称、别名、拼音首字母都不做——先做子串命中，把 hint 也算进去 */
function match(cmd: Cmd, q: string) {
  if (!q) return true
  const hay = (cmd.label + ' ' + (cmd.hint ?? '')).toLowerCase()
  return hay.includes(q)
}

const results = computed<Cmd[]>(() => {
  const q = query.value.trim().toLowerCase()
  // 无输入时不铺 88 章，只给导航和操作，否则第一屏全是章节列表
  const pool = q
    ? [...navCmds.value, ...actionCmds.value, ...chapterCmds.value, ...codexCmds.value]
    : [...navCmds.value, ...actionCmds.value]
  return pool.filter((c) => match(c, q)).slice(0, 40)
})

/** 结果按 group 归拢，同时保留扁平索引供键盘移动 */
const grouped = computed(() => {
  const out: { group: string; items: { cmd: Cmd; index: number }[] }[] = []
  results.value.forEach((cmd, index) => {
    const last = out[out.length - 1]
    if (last && last.group === cmd.group) last.items.push({ cmd, index })
    else out.push({ group: cmd.group, items: [{ cmd, index }] })
  })
  return out
})

watch(results, () => {
  cursor.value = 0
})

function close() {
  shell.paletteOpen = false
  query.value = ''
  cursor.value = 0
}

function accept() {
  const cmd = results.value[cursor.value]
  if (!cmd) return
  close()
  cmd.run()
}

function move(delta: number) {
  const n = results.value.length
  if (!n) return
  cursor.value = (cursor.value + delta + n) % n
  nextTick(() => {
    listEl.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  })
}

function onKeydown(e: KeyboardEvent) {
  const mod = e.metaKey || e.ctrlKey

  if (mod && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    shell.paletteOpen ? close() : (shell.paletteOpen = true)
    return
  }

  if (!shell.paletteOpen) {
    // 全局快捷键只在面板关闭时生效，且不抢输入框里的按键
    const t = e.target as HTMLElement | null
    const typing = !!t?.closest('input, textarea, [contenteditable="true"]')
    if (typing || !mod) return
    if (e.key === '\\') {
      e.preventDefault()
      shell.toggleZen()
    } else if (e.key.toLowerCase() === 'b') {
      e.preventDefault()
      shell.leftOpen = !shell.leftOpen
    } else if (e.key.toLowerCase() === 'j') {
      e.preventDefault()
      shell.rightOpen = !shell.rightOpen
    }
    return
  }

  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    move(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    move(-1)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    accept()
  }
}

watch(
  () => shell.paletteOpen,
  async (open) => {
    if (!open) return
    await nextTick()
    inputEl.value?.focus()
  }
)

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="shell.paletteOpen" class="palette-veil" @click.self="close">
    <div class="palette" role="dialog" aria-modal="true" aria-label="命令面板">
      <input
        ref="inputEl"
        v-model="query"
        class="palette-input"
        placeholder="搜索命令、章节、设定条目……"
        aria-label="搜索命令"
      >

      <div ref="listEl" class="palette-list">
        <template v-for="g in grouped" :key="g.group">
          <div class="palette-group">{{ g.group }}</div>
          <button
            v-for="it in g.items"
            :key="it.cmd.id"
            class="palette-item"
            type="button"
            :aria-selected="it.index === cursor"
            @mouseenter="cursor = it.index"
            @click="accept()"
          >
            <span>{{ it.cmd.label }}</span>
            <span v-if="it.cmd.hint" class="palette-item-hint">{{ it.cmd.hint }}</span>
          </button>
        </template>
        <p v-if="!results.length" class="palette-empty">没有匹配的命令、章节或设定。</p>
      </div>

      <div class="palette-foot">
        <span>↑↓ 选择</span>
        <span>↵ 执行</span>
        <span>esc 关闭</span>
        <span :style="{ marginLeft: 'auto' }">{{ results.length }} 项</span>
      </div>
    </div>
  </div>
</template>
