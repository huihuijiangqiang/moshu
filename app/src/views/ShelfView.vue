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

onMounted(async () => {
  shell.setCrumb('作品库')
  books.value = await shelfApi.listBooks()
})

const statusItems = computed(() => [
  { key: 'all' as const, label: '全部作品', count: books.value.length },
  { key: 'ongoing' as const, label: '正在写', count: books.value.filter((b) => b.status === 'ongoing').length },
  { key: 'planning' as const, label: '构思中', count: books.value.filter((b) => b.status === 'planning').length },
  { key: 'finished' as const, label: '已完稿', count: books.value.filter((b) => b.status === 'finished').length }
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
  finished: '已完稿'
}

function open(book: ShelfBook) {
  router.push(projectPath(book.id, book.status === 'planning' || book.status === 'finished' ? 'outline' : 'write'))
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
          <span class="cover-series">长篇小说</span><strong>{{ currentBook.title }}</strong><span class="cover-author">创作中</span>
        </button>

        <div class="featured-copy">
          <span class="book-status" :data-status="currentBook.status">{{ statusLabel[currentBook.status] }}</span>
          <h3>《{{ currentBook.title }}》</h3>
          <p class="featured-genre">{{ currentBook.genre }}</p>
          <p class="featured-last">{{ currentBook.lastTouched }}</p>
          <div class="featured-actions">
            <button class="wk-btn" data-primary="true" type="button" @click="open(currentBook)">继续写作</button>
            <button class="wk-btn" type="button" @click="router.push(projectPath(currentBook.id, 'outline'))">查看大纲</button>
            <button class="icon-button" type="button" title="更多操作"><AppIcon name="more" /></button>
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
            <div class="manuscript-title"><span class="book-status" :data-status="book.status">{{ statusLabel[book.status] }}</span><h4>《{{ book.title }}》</h4><p>{{ book.genre }}</p></div>
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
  </div>
</template>
