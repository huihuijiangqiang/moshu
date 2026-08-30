import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

type Theme = 'light' | 'dark'

const THEME_KEY = 'moshu.theme'

/**
 * 界面外壳状态：面板折叠、命令面板、主题。
 * 独立于业务 store，因为写作台切章不应该影响折叠状态，
 * 反过来折叠也不该触发任何数据请求。
 */
export const useShellStore = defineStore('shell', () => {
  const leftOpen = ref(true)
  const rightOpen = ref(true)
  const paletteOpen = ref(false)
  const theme = ref<Theme>('light')
  /** 顶栏面包屑。外壳在 App.vue 上，各屏用 setCrumb 往上报当前位置。 */
  const crumb = ref('')

  function setCrumb(next: string) {
    crumb.value = next
  }

  // 纯净模式（Cmd+\）：两栏一起收，正文独占。再按一次恢复原状。
  const beforeZen = ref<[boolean, boolean] | null>(null)
  const zen = ref(false)

  function toggleZen() {
    if (zen.value) {
      const [l, r] = beforeZen.value ?? [true, true]
      leftOpen.value = l
      rightOpen.value = r
      beforeZen.value = null
      zen.value = false
    } else {
      beforeZen.value = [leftOpen.value, rightOpen.value]
      leftOpen.value = false
      rightOpen.value = false
      zen.value = true
    }
  }

  function setTheme(next: Theme) {
    theme.value = next
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem(THEME_KEY, next)
    } catch {
      // 隐私模式下 localStorage 会抛，主题不是关键路径，静默降级
    }
  }

  function initTheme() {
    let saved: string | null = null
    try {
      saved = localStorage.getItem(THEME_KEY)
    } catch {
      saved = null
    }
    setTheme(saved === 'dark' ? 'dark' : 'light')
  }

  function toggleTheme() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  // 手动展开任一栏就算退出纯净模式，否则状态会自相矛盾
  watch([leftOpen, rightOpen], ([l, r]) => {
    if (zen.value && (l || r)) {
      zen.value = false
      beforeZen.value = null
    }
  })

  return {
    leftOpen,
    rightOpen,
    paletteOpen,
    theme,
    zen,
    crumb,
    setCrumb,
    toggleZen,
    setTheme,
    initTheme,
    toggleTheme
  }
})
