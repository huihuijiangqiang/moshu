<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { shelfApi, type ShelfBook } from '@/api/mock/shelf'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useShellStore } from '@/stores/shell'

type StatusFilter = 'all' | ShelfBook['status']
type SortKey = 'updated' | 'words' | 'issues'
type ViewMode = 'list' | 'grid'

const router = useRouter()
const shell = useShellStore()

const books = ref<ShelfBook[]>([])
const query = ref('')
const status = ref<StatusFilter>('all')
const genre = ref('全部')
const sort = ref<SortKey>('updated')
const view = ref<ViewMode>('list')
const filtersOpen = ref(false)

onMounted(async () => {
  shell.setCrumb('作品库')
  books.value = await shelfApi.listBooks()
})

const statusItems = computed(() => [
  { key: 'all' as const, label: '全部作品', count: books.value.length },
  { key: 'ongoing' as const, label: '连载中', count: books.value.filter((b) => b.status === 'ongoing').length },
  { key: 'planning' as const, label: '筹备中', count: books.value.filter((b) => b.status === 'planning').length },
  { key: 'finished' as const, label: '已完结', count: books.value.filter((b) => b.status === 'finished').length }
])

const genres = ['全部', '玄幻', '武侠', '都市', '历史', '悬疑', '科幻', '古言']

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  const rows = books.value.filter((book) => {
    const matchStatus = status.value === 'all' || book.status === status.value
    const matchGenre = genre.value === '全部' || book.genre.includes(genre.value)
    const matchQuery = !q || `${book.title} ${book.genre} ${book.lastTouched}`.toLowerCase().includes(q)
    return matchStatus && matchGenre && matchQuery
  })

  return [...rows].sort((a, b) => {
    if (sort.value === 'words') return b.words - a.words
    if (sort.value === 'issues') return b.guardOpen - a.guardOpen
    return books.value.indexOf(a) - books.value.indexOf(b)
  })
})

const totalToday = computed(() => books.value.reduce((sum, book) => sum + book.todayWords, 0))
const dailyGoal = 6000
const dailyPct = computed(() => Math.min(100, Math.round((totalToday.value / dailyGoal) * 100)))

const statusLabel: Record<ShelfBook['status'], string> = {
  ongoing: '连载中',
  planning: '筹备中',
  finished: '已完结'
}

const coverCharacter = (title: string) => title.slice(0, 1)
const formatWords = (words: number) => words ? `${(words / 10000).toFixed(1)}万字` : '尚未开写'

function open(book: ShelfBook) {
  router.push(book.status === 'planning' || book.status === 'finished' ? '/outline' : '/write')
}

function openAt(index: number) {
  const book = books.value[index]
  if (book) open(book)
}

function setStatus(next: StatusFilter) {
  status.value = next
  filtersOpen.value = false
}

function clearFilters() {
  query.value = ''
  status.value = 'all'
  genre.value = '全部'
}
</script>

<template>
  <div class="library-page">
    <button
      class="library-filter-trigger"
      type="button"
      aria-label="打开作品筛选"
      @click="filtersOpen = !filtersOpen"
    >
      <AppIcon name="filter" />
    </button>

    <aside class="library-sidebar" :data-open="filtersOpen" aria-label="作品筛选">
      <div class="library-sidebar-head">
        <h1>作品库</h1>
        <button class="icon-button" type="button" title="新建作品" @click="router.push('/wizard')">
          <AppIcon name="plus" />
        </button>
      </div>

      <nav class="library-status-nav" aria-label="作品状态">
        <button
          v-for="item in statusItems"
          :key="item.key"
          type="button"
          :aria-current="status === item.key"
          @click="setStatus(item.key)"
        >
          <span>{{ item.label }}</span>
          <span>{{ item.count }}</span>
        </button>
        <button type="button"><span>回收站</span><span>0</span></button>
      </nav>

      <section class="library-filter-section">
        <div class="section-label">题材筛选</div>
        <div class="genre-grid">
          <button
            v-for="item in genres"
            :key="item"
            type="button"
            :aria-pressed="genre === item"
            @click="genre = item"
          >{{ item }}</button>
        </div>
      </section>

      <section class="library-workspace-section">
        <div class="section-label">团队 / 工作区</div>
        <button class="workspace-switcher" type="button">
          <span class="workspace-avatar">墨</span>
          <span>墨枢创作团队</span>
          <AppIcon name="chevron" :size="13" />
        </button>
      </section>
    </aside>

    <main class="library-main">
      <header class="library-main-head">
        <div>
          <h2>全部作品</h2>
          <p>{{ books.length }} 部作品 · {{ statusItems[1]?.count ?? 0 }} 部连载中</p>
        </div>
        <button class="wk-btn" data-primary="true" type="button" @click="router.push('/wizard')">
          <AppIcon name="plus" :size="14" />
          新建作品
        </button>
      </header>

      <div class="library-toolbar">
        <label class="library-search">
          <AppIcon name="search" :size="15" />
          <input v-model="query" type="search" placeholder="搜索作品名、标签或简介" aria-label="搜索作品">
        </label>

        <select v-model="sort" class="library-sort" aria-label="作品排序">
          <option value="updated">最近更新</option>
          <option value="words">字数最多</option>
          <option value="issues">问题最多</option>
        </select>

        <div class="view-switch" aria-label="显示方式">
          <button type="button" title="列表视图" :aria-pressed="view === 'list'" @click="view = 'list'">
            <AppIcon name="list" />
          </button>
          <button type="button" title="网格视图" :aria-pressed="view === 'grid'" @click="view = 'grid'">
            <AppIcon name="grid" />
          </button>
        </div>
      </div>

      <div v-if="filtered.length && view === 'list'" class="book-table" role="table" aria-label="作品列表">
        <div class="book-table-head" role="row">
          <span>作品信息</span>
          <span>字数</span>
          <span>章节</span>
          <span>最后编辑</span>
          <span>写作进度</span>
          <span>一致性</span>
          <span aria-hidden="true" />
        </div>

        <article
          v-for="book in filtered"
          :key="book.id"
          class="book-row"
          :data-featured="book.id === 'p1'"
          role="row"
          @dblclick="open(book)"
        >
          <div class="book-identity" role="cell">
            <div class="book-cover" :data-tone="book.coverTone" aria-hidden="true"><span>{{ coverCharacter(book.title) }}</span></div>
            <div>
              <h3>《{{ book.title }}》</h3>
              <p>{{ book.genre }}</p>
              <span class="book-status" :data-status="book.status">{{ statusLabel[book.status] }}</span>
            </div>
          </div>
          <div class="book-metric" role="cell">{{ formatWords(book.words) }}</div>
          <div class="book-metric" role="cell">{{ book.chapters || '—' }}<span v-if="book.chapters"> 章</span></div>
          <div class="book-updated" role="cell">{{ book.lastTouched }}</div>
          <div class="book-progress" role="cell">
            <div><span>{{ book.progress }}%</span><span>目标 {{ (book.targetWords / 10000).toFixed(0) }} 万字</span></div>
            <span class="progress-track"><span :style="{ width: book.progress + '%' }" /></span>
          </div>
          <div class="book-issues" role="cell" :data-has-issues="book.guardOpen > 0">{{ book.guardOpen }}</div>
          <div class="book-actions" role="cell">
            <button class="wk-btn wk-btn-xs" type="button" :data-primary="book.status === 'ongoing'" @click="open(book)">
              {{ book.status === 'planning' ? '进入管理' : book.status === 'finished' ? '查看详情' : '继续写作' }}
            </button>
            <button class="icon-button" type="button" title="更多操作"><AppIcon name="more" /></button>
          </div>
        </article>
      </div>

      <div v-else-if="filtered.length" class="book-grid">
        <article v-for="book in filtered" :key="book.id" class="book-grid-item">
          <div class="book-cover book-cover-large" :data-tone="book.coverTone" aria-hidden="true"><span>{{ coverCharacter(book.title) }}</span></div>
          <div class="book-grid-copy">
            <div class="row-between">
              <span class="book-status" :data-status="book.status">{{ statusLabel[book.status] }}</span>
              <span v-if="book.guardOpen" class="book-grid-alert">{{ book.guardOpen }} 条问题</span>
            </div>
            <h3>《{{ book.title }}》</h3>
            <p>{{ book.genre }} · {{ formatWords(book.words) }} · {{ book.chapters }} 章</p>
            <span class="progress-track"><span :style="{ width: book.progress + '%' }" /></span>
            <button class="wk-btn wk-btn-block" type="button" :data-primary="book.status === 'ongoing'" @click="open(book)">
              {{ book.status === 'ongoing' ? '继续写作' : '打开作品' }}
            </button>
          </div>
        </article>
      </div>

      <div v-else class="library-empty">
        <h3>没有匹配的作品</h3>
        <p>调整状态、题材或搜索词后再试。</p>
        <button class="wk-btn" type="button" @click="clearFilters">清除筛选</button>
      </div>

      <footer class="library-count">共 {{ filtered.length }} 部作品</footer>
    </main>

    <aside class="library-activity" aria-label="今日写作与提醒">
      <section class="today-panel">
        <div class="activity-title"><h2>今日写作</h2><button class="icon-button" type="button" title="调整今日目标">···</button></div>
        <p>跨作品总字数（含标点）</p>
        <div class="today-number"><strong>{{ totalToday.toLocaleString() }}</strong><span>/ {{ dailyGoal.toLocaleString() }} 字</span></div>
        <div class="today-progress"><span :style="{ width: dailyPct + '%' }" /></div>
        <div class="row-between today-meta"><span>每日目标：{{ dailyGoal.toLocaleString() }} 字</span><strong>{{ dailyPct }}%</strong></div>
      </section>

      <section class="activity-section">
        <div class="activity-title"><h3>最近活动</h3><button type="button">查看全部</button></div>
        <button v-for="book in books.slice(0, 5)" :key="book.id" class="activity-row" type="button" @click="open(book)">
          <span class="activity-dot" :data-status="book.status" />
          <span><strong>《{{ book.title }}》</strong><small>{{ book.lastTouched }}</small></span>
          <b v-if="book.todayWords">+{{ book.todayWords.toLocaleString() }}</b>
        </button>
      </section>

      <section class="activity-section reminders-section">
        <div class="activity-title"><h3>跨作品提醒</h3><button type="button" @click="router.push('/guard')">查看全部</button></div>
        <button class="reminder-row" type="button" @click="router.push('/guard')">
          <span class="reminder-icon">!</span><span><strong>剑起山河 · 3 条一致性问题</strong><small>涉及人物设定、时间线等</small></span>
        </button>
        <button class="reminder-row" type="button" @click="openAt(2)">
          <span class="reminder-icon">!</span><span><strong>长夜渡舟 · 章纲待确认</strong><small>第 17–20 章需要确认</small></span>
        </button>
        <button class="reminder-row reminder-row-info" type="button" @click="openAt(4)">
          <span class="reminder-icon">i</span><span><strong>失重花园 · 世界观待完善</strong><small>空间站设定存在空白</small></span>
        </button>
      </section>
    </aside>
  </div>
</template>
