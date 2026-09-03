<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { shelfApi, type ShelfBook } from '@/api/shelf'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useShellStore } from '@/stores/shell'
import { projectPath } from '@/router/project-route'

type StatusFilter = 'all' | ShelfBook['status']
type SortKey = 'updated' | 'words' | 'issues'
type ViewMode = 'list' | 'grid'

const router = useRouter()
const shell = useShellStore()
const books = ref<ShelfBook[]>([])
const query = ref('')
const status = ref<StatusFilter>('all')
const sort = ref<SortKey>('updated')
const view = ref<ViewMode>('list')
const filtersOpen = ref(false)
const settingsBook = ref<ShelfBook | null>(null)
const settingsForm = ref({ title: '', genre: '', status: 'ongoing' as 'ongoing' | 'finished' | 'archived', dailyGoal: 3000 })
const settingsBusy = ref(false)
const settingsError = ref('')

onMounted(async () => {
  shell.setCrumb('作品库')
  books.value = await shelfApi.listBooks()
})

const statusItems = computed(() => [
  { key: 'all' as const, label: '全部作品', count: books.value.length },
  { key: 'ongoing' as const, label: '正在写', count: books.value.filter((b) => b.status === 'ongoing').length },
  { key: 'planning' as const, label: '构思中', count: books.value.filter((b) => b.status === 'planning').length },
  { key: 'finished' as const, label: '已完稿', count: books.value.filter((b) => b.status === 'finished').length },
  { key: 'archived' as const, label: '已归档', count: books.value.filter((b) => b.status === 'archived').length }
])

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  const rows = books.value.filter((book) => {
    const matchStatus = status.value === 'all' || book.status === status.value
    const matchQuery = !q || `${book.title} ${book.genre} ${book.lastTouched}`.toLowerCase().includes(q)
    return matchStatus && matchQuery
  })

  return [...rows].sort((a, b) => {
    if (sort.value === 'words') return b.words - a.words
    if (sort.value === 'issues') return b.guardOpen - a.guardOpen
    return books.value.indexOf(a) - books.value.indexOf(b)
  })
})

const currentBook = computed(() => filtered.value.find((book) => book.status === 'ongoing') ?? filtered.value[0] ?? null)
const otherBooks = computed(() => filtered.value.filter((book) => book.id !== currentBook.value?.id))
const totalToday = computed(() => books.value.reduce((sum, book) => sum + book.todayWords, 0))
const dailyGoal = computed(() => books.value.reduce((sum, book) => sum + (book.dailyGoal ?? 0), 0) || 6000)
const dailyPct = computed(() => Math.min(100, Math.round((totalToday.value / dailyGoal.value) * 100)))
const todayLabel = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(new Date())
const primaryBookId = computed(() => currentBook.value?.id ?? books.value[0]?.id ?? null)
const guardTotal = computed(() => books.value.reduce((sum, book) => sum + book.guardOpen, 0))
const codexTotal = computed(() => books.value.reduce((sum, book) => sum + book.codexCount, 0))

const statusLabel: Record<ShelfBook['status'], string> = {
  ongoing: '正在写',
  planning: '构思中',
  finished: '已完稿',
  archived: '已归档'
}

function open(book: ShelfBook) {
  router.push(projectPath(book.id, book.status === 'ongoing' ? 'write' : 'outline'))
}

function openSettings(book: ShelfBook) {
  settingsBook.value = book
  settingsForm.value = {
    title: book.title,
    genre: book.genre,
    status: book.status === 'planning' ? 'ongoing' : book.status,
    dailyGoal: book.dailyGoal ?? 3000
  }
  settingsError.value = ''
}

async function saveSettings() {
  if (!settingsBook.value || !settingsForm.value.title.trim() || settingsBusy.value) return
  settingsBusy.value = true
  settingsError.value = ''
  try {
    const updated = await shelfApi.updateBook(settingsBook.value.id, {
      ...settingsForm.value,
      title: settingsForm.value.title.trim(),
      genre: settingsForm.value.genre.trim()
    })
    const index = books.value.findIndex((book) => book.id === updated.id)
    if (index >= 0) books.value[index] = updated
    settingsBook.value = null
  } catch {
    settingsError.value = '作品设置没有保存，请检查内容后重试。'
  } finally {
    settingsBusy.value = false
  }
}

function setStatus(next: StatusFilter) {
  status.value = next
  filtersOpen.value = false
}

function clearFilters() {
  query.value = ''
  status.value = 'all'
}

function formatWords(words: number) {
  return words ? `${(words / 10000).toFixed(1)} 万` : '尚未开写'
}
</script>

<template>
  <div class="studio-library">
    <button class="library-filter-trigger" type="button" aria-label="打开作品筛选" @click="filtersOpen = !filtersOpen">
      <AppIcon name="filter" />
    </button>

    <aside class="studio-sidebar" :data-open="filtersOpen" aria-label="作品筛选">
      <header class="studio-sidebar-head">
        <div><span class="studio-kicker">墨枢写作室</span><h1>我的作品</h1></div>
        <button class="icon-button" type="button" title="新建作品" @click="router.push('/projects/new')"><AppIcon name="plus" /></button>
      </header>

      <nav class="studio-status-nav" aria-label="作品状态">
        <button v-for="item in statusItems" :key="item.key" type="button" :aria-current="status === item.key" @click="setStatus(item.key)">
          <span>{{ item.label }}</span><span>{{ item.count }}</span>
        </button>
      </nav>

      <section class="studio-sidebar-section">
        <span class="studio-section-label">写作计划</span>
        <div class="studio-goal-copy"><strong>{{ totalToday.toLocaleString() }}</strong><span>/ {{ dailyGoal.toLocaleString() }} 字</span></div>
        <span class="studio-progress"><span :style="{ width: dailyPct + '%' }" /></span>
        <p>今天已完成 {{ dailyPct }}%，还差 {{ Math.max(0, dailyGoal - totalToday).toLocaleString() }} 字。</p>
      </section>

      <section class="studio-sidebar-section studio-reminder-block">
        <span class="studio-section-label">需要留意</span>
        <button type="button" :disabled="!primaryBookId" @click="primaryBookId && router.push(projectPath(primaryBookId, 'guard'))">
          <span class="reminder-mark">{{ guardTotal }}</span><span><strong>一致性问题</strong><small>{{ guardTotal ? `${guardTotal} 条待处理` : '当前没有待处理问题' }}</small></span>
        </button>
        <button type="button" :disabled="!primaryBookId" @click="primaryBookId && router.push(projectPath(primaryBookId, 'codex'))">
          <span class="reminder-mark reminder-mark-muted">{{ codexTotal }}</span><span><strong>设定库</strong><small>{{ codexTotal }} 条人物与世界设定</small></span>
        </button>
      </section>

      <button class="studio-team" type="button">
        <span class="workspace-avatar">墨</span><span><strong>个人工作区</strong><small>{{ books.length }} 部作品</small></span><AppIcon name="chevron" :size="13" />
      </button>
    </aside>

    <main class="studio-main">
      <header class="studio-main-head">
        <div><span class="studio-kicker">{{ todayLabel }}</span><h2>继续你的故事</h2></div>
        <button class="wk-btn" data-primary="true" type="button" @click="router.push('/projects/new')"><AppIcon name="plus" :size="15" />新建作品</button>
      </header>

      <section v-if="currentBook" class="featured-manuscript">
        <button class="featured-cover" :data-tone="currentBook.coverTone" type="button" @click="open(currentBook)">
          <span class="cover-series">长篇小说</span><strong>{{ currentBook.title }}</strong><span class="cover-author">{{ statusLabel[currentBook.status] }}</span>
        </button>

        <div class="featured-copy">
          <span class="book-status" :data-status="currentBook.status">{{ statusLabel[currentBook.status] }}</span>
          <h3>《{{ currentBook.title }}》</h3>
          <p class="featured-genre">{{ currentBook.genre }}</p>
          <p class="featured-last">{{ currentBook.lastTouched }}</p>
          <div class="featured-actions">
            <button class="wk-btn" data-primary="true" type="button" @click="open(currentBook)">{{ currentBook.status === 'ongoing' ? '继续写作' : '打开作品' }}</button>
            <button class="wk-btn" type="button" @click="router.push(projectPath(currentBook.id, 'outline'))">查看大纲</button>
            <button class="icon-button" type="button" title="作品设置" aria-label="打开作品设置" @click="openSettings(currentBook)"><AppIcon name="edit" /></button>
          </div>
        </div>

        <div class="featured-stats">
          <div><span>当前字数</span><strong>{{ formatWords(currentBook.words) }}</strong></div>
          <div><span>正文进度</span><strong>{{ currentBook.progress }}%</strong></div>
          <div><span>今日写作</span><strong>+{{ currentBook.todayWords.toLocaleString() }}</strong></div>
          <span class="featured-progress"><span :style="{ height: currentBook.progress + '%' }" /></span>
        </div>
      </section>

      <section v-if="filtered.length" class="manuscripts-section">
        <header class="manuscripts-head">
          <div><h3>其他稿件</h3><span>{{ otherBooks.length }} 部</span></div>
          <div class="manuscripts-tools">
            <label class="library-search"><AppIcon name="search" :size="15" /><input v-model="query" type="search" placeholder="搜索作品" aria-label="搜索作品"></label>
            <select v-model="sort" class="library-sort" aria-label="作品排序">
              <option value="updated">最近编辑</option><option value="words">字数最多</option><option value="issues">待处理最多</option>
            </select>
            <div class="view-switch" aria-label="显示方式">
              <button type="button" title="列表视图" :aria-pressed="view === 'list'" @click="view = 'list'"><AppIcon name="list" /></button>
              <button type="button" title="网格视图" :aria-pressed="view === 'grid'" @click="view = 'grid'"><AppIcon name="grid" /></button>
            </div>
          </div>
        </header>

        <div class="manuscript-list" :data-view="view">
          <article v-for="book in otherBooks" :key="book.id" class="manuscript-item" :data-tone="book.coverTone">
            <button class="manuscript-spine" type="button" :aria-label="`打开《${book.title}》`" @click="open(book)"><span :style="{ height: book.progress + '%' }" /></button>
            <div class="manuscript-title"><span class="book-status" :data-status="book.status">{{ statusLabel[book.status] }}</span><button class="manuscript-settings" type="button" title="作品设置" :aria-label="`设置《${book.title}》`" @click="openSettings(book)"><AppIcon name="edit" :size="15" /></button><h4>《{{ book.title }}》</h4><p>{{ book.genre }}</p></div>
            <div class="manuscript-meta"><span>字数</span><strong>{{ formatWords(book.words) }}</strong></div>
            <div class="manuscript-meta"><span>章节</span><strong>{{ book.chapters || '—' }}</strong></div>
            <div class="manuscript-touched"><span>最后编辑</span><strong>{{ book.lastTouched }}</strong></div>
            <div v-if="book.guardOpen" class="manuscript-alert">{{ book.guardOpen }} 条待处理</div>
            <button class="wk-btn wk-btn-xs" type="button" @click="open(book)">{{ book.status === 'ongoing' ? '继续写' : '打开' }}</button>
          </article>
        </div>
      </section>

      <div v-else class="library-empty"><h3>没有找到作品</h3><p>换个关键词或查看全部作品。</p><button class="wk-btn" type="button" @click="clearFilters">清除筛选</button></div>
    </main>

    <div v-if="settingsBook" class="shelf-settings-backdrop" @click.self="settingsBook = null">
      <section class="shelf-settings" role="dialog" aria-modal="true" aria-labelledby="shelf-settings-title">
        <header><div><span>作品管理</span><h2 id="shelf-settings-title">作品设置</h2></div><button type="button" aria-label="关闭" @click="settingsBook = null">×</button></header>
        <div class="shelf-settings-body">
          <label><span>书名</span><input v-model="settingsForm.title" maxlength="200"></label>
          <label><span>题材</span><input v-model="settingsForm.genre" maxlength="100" placeholder="例如：女频 · 穿越种田"></label>
          <label><span>每日目标</span><input v-model.number="settingsForm.dailyGoal" type="number" min="100" max="100000" step="100"><small>字</small></label>
          <fieldset><legend>创作状态</legend><button v-for="item in ([['ongoing', '正在写'], ['finished', '已完稿'], ['archived', '已归档']] as const)" :key="item[0]" type="button" :aria-pressed="settingsForm.status === item[0]" @click="settingsForm.status = item[0]">{{ item[1] }}</button></fieldset>
          <p v-if="settingsError" role="alert">{{ settingsError }}</p>
        </div>
        <footer><span>归档作品仍会保留全部正文与设定。</span><div><button class="wk-btn" type="button" @click="settingsBook = null">取消</button><button class="wk-btn" data-primary="true" type="button" :disabled="settingsBusy || !settingsForm.title.trim()" @click="saveSettings">{{ settingsBusy ? '保存中…' : '保存设置' }}</button></div></footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.manuscript-title { position: relative; padding-right: 28px; }
.manuscript-settings { position: absolute; top: 0; right: 0; width: 26px; height: 26px; display: grid; place-items: center; padding: 0; border: 0; background: transparent; color: var(--ink-4); cursor: pointer; }
.manuscript-settings:hover { color: var(--primary); background: var(--primary-soft); }
.shelf-settings-backdrop { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; padding: 18px; background: rgb(20 24 25 / 55%); }
.shelf-settings { width: min(560px, 100%); overflow: hidden; border: var(--hair) solid var(--line-strong); border-radius: 4px; background: var(--paper); box-shadow: 0 18px 56px rgb(0 0 0 / 24%); }
.shelf-settings > header { min-height: 72px; display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 2px solid var(--ink); }
.shelf-settings header span { color: var(--ink-4); font: 9px/1 var(--font-mono); }
.shelf-settings h2 { margin: 5px 0 0; font-size: 20px; }
.shelf-settings header button { width: 30px; height: 30px; border: 0; background: transparent; color: var(--ink-2); font-size: 22px; cursor: pointer; }
.shelf-settings-body { display: grid; gap: 16px; padding: 20px; }
.shelf-settings-body > label { position: relative; display: grid; gap: 7px; color: var(--ink-3); font-size: var(--fs-xs); font-weight: 700; }
.shelf-settings-body input { width: 100%; height: 40px; padding: 0 11px; border: var(--hair) solid var(--line-strong); border-radius: 3px; background: var(--paper); color: var(--ink); font: inherit; }
.shelf-settings-body label small { position: absolute; right: 11px; bottom: 12px; color: var(--ink-4); }
.shelf-settings-body fieldset { display: grid; grid-template-columns: repeat(3, 1fr); padding: 0; border: var(--hair) solid var(--line-strong); }
.shelf-settings-body legend { margin-left: 8px; padding: 0 5px; color: var(--ink-4); font-size: var(--fs-xs); }
.shelf-settings-body fieldset button { min-height: 38px; border: 0; border-right: var(--hair) solid var(--line); background: var(--paper); color: var(--ink-3); font: inherit; cursor: pointer; }
.shelf-settings-body fieldset button:last-child { border-right: 0; }
.shelf-settings-body fieldset button[aria-pressed="true"] { background: var(--primary-soft); color: var(--primary); font-weight: 700; }
.shelf-settings-body > p { margin: 0; color: var(--alert-ink); font-size: var(--fs-sm); }
.shelf-settings footer { min-height: 60px; display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 10px 20px; border-top: var(--hair) solid var(--line); color: var(--ink-4); font-size: var(--fs-xs); }
.shelf-settings footer > div { display: flex; gap: 7px; }
@media (max-width: 620px) {
  .shelf-settings-backdrop { padding: 0; place-items: end stretch; }
  .shelf-settings { width: 100%; border-radius: 0; }
  .shelf-settings footer { align-items: flex-start; flex-direction: column; }
  .shelf-settings footer > div { align-self: stretch; }
  .shelf-settings footer .wk-btn { flex: 1; }
}
</style>
