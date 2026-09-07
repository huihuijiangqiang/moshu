/** 图标轨与行内动作用的图标名。类型单独放，因为 <script setup> 不允许 export。 */
export type IconName =
  | 'shelf'
  | 'write'
  | 'outline'
  | 'timeline'
  | 'codex'
  | 'guard'
  | 'style'
  | 'ratio'
  | 'export'
  | 'usage'
  | 'search'
  | 'list'
  | 'grid'
  | 'plus'
  | 'filter'
  | 'more'
  | 'close'
  | 'chevron'
  | 'collapse'
  | 'edit'
  | 'trash'
  | 'restore'
  | 'check'
  | 'history'
  | 'key'
  | 'team'
  | 'agent'

/**
 * 自己画而不引图标库：只用 12 个图标，装一个几千图标的包不值得，
 * 而且 stroke 宽度要和界面的 1px 线对齐，现成图标库多是 2px 圆角。
 */
export const ICON_PATHS: Record<IconName, string> = {
  shelf: 'M3 3.5h3.5v13H3zM8 3.5h3.5v13H8zM13.5 4.5l3 .5-2 12-3-.5z',
  write: 'M3.5 16.5l1-3.5 9-9 2.5 2.5-9 9zM11.5 5.5l2.5 2.5',
  outline: 'M3.5 5.5h4M3.5 10h13M3.5 14.5h8M10 5.5h6.5',
  timeline: 'M3 5.5h14M3 10h14M3 14.5h14M6 3.5v4M13 8v4M8.5 12.5v4',
  codex: 'M3.5 6.5h9v10h-9zM6.5 3.5h10v10',
  guard: 'M10 2.5l6 2.5v4.5c0 4-2.6 6.8-6 8.2-3.4-1.4-6-4.2-6-8.2V5z',
  style: 'M2.5 12.5c2-7 3.5 3.5 5.5-3.5s2.5 8 4.5 2.5 2.5 2 5 2',
  ratio: 'M10 2.5a7.5 7.5 0 107.5 7.5H10z',
  export: 'M10 12.5V3M6.5 6.5L10 3l3.5 3.5M3.5 12.5v4h13v-4',
  usage: 'M4.5 16.5V9M9.5 16.5V4M14.5 16.5v-6',
  search: 'M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM13 13l4 4',
  list: 'M3.5 5h2M8 5h8.5M3.5 10h2M8 10h8.5M3.5 15h2M8 15h8.5',
  grid: 'M3.5 3.5h5v5h-5zM11.5 3.5h5v5h-5zM3.5 11.5h5v5h-5zM11.5 11.5h5v5h-5z',
  plus: 'M10 3.5v13M3.5 10h13',
  filter: 'M3 4h14l-5.5 6v5l-3 1.5V10z',
  more: 'M4.5 10h.1M9.9 10h.1M15.4 10h.1',
  close: 'M4.5 4.5l11 11M15.5 4.5l-11 11',
  chevron: 'M7.5 5l5 5-5 5',
  collapse: 'M12.5 5l-5 5 5 5',
  edit: 'M4 14.5l.7-3.2L13 3l4 4-8.3 8.3-3.2.7zM11.5 4.5l4 4',
  trash: 'M3.5 5.5h13M7 5.5V3.5h6v2M5.5 5.5l.7 11h7.6l.7-11M8.5 8.5v5M11.5 8.5v5',
  restore: 'M5.5 6H2.8V3.3M3.2 5.8A7 7 0 1110 17M3.2 5.8A7 7 0 0115.8 8',
  check: 'M3.5 10.5l4 4 9-9',
  history: 'M4 5.5h8.5M4 9.5h8.5M4 13.5h5.5M14.5 11.5v5M12 14h5',
  key: 'M12 7a4 4 0 11-1.2-2.8L17 4v3h-2v2h-2l-1.2 1.2A4 4 0 0112 7z',
  team: 'M6.5 9a2.5 2.5 0 110-5 2.5 2.5 0 010 5zM13.5 9a2.5 2.5 0 110-5 2.5 2.5 0 010 5zM2.8 16.5c.4-3 1.7-4.5 3.7-4.5s3.3 1.5 3.7 4.5M9.8 16.5c.4-3 1.7-4.5 3.7-4.5s3.3 1.5 3.7 4.5',
  agent: 'M5 4.5h10a2 2 0 012 2v7a2 2 0 01-2 2h-3l-2 2-2-2H5a2 2 0 01-2-2v-7a2 2 0 012-2zM6.5 9h.1M10 9h.1M13.5 9h.1'
}
