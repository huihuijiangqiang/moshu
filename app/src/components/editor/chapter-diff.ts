export type ChapterDiffKind = 'same' | 'added' | 'removed' | 'omitted'

export interface ChapterDiffRow {
  kind: ChapterDiffKind
  text: string
  count?: number
}

export function htmlBlocks(html: string): string[] {
  const document = new DOMParser().parseFromString(html, 'text/html')
  return Array.from(document.body.children)
    .map((element) => element.textContent?.trim() ?? '')
    .filter(Boolean)
}

export function htmlPlainText(html: string): string {
  return htmlBlocks(html).join('\n\n')
}

function compactSame(rows: ChapterDiffRow[], keep = 3): ChapterDiffRow[] {
  if (rows.length <= keep * 2 + 1) return rows
  return [
    ...rows.slice(0, keep),
    { kind: 'omitted', text: '', count: rows.length - keep * 2 },
    ...rows.slice(-keep)
  ]
}

/**
 * A bounded paragraph diff: unchanged edges stay readable while the changed middle is
 * rendered directly. It avoids quadratic diff work on novel-length chapters.
 */
export function chapterDiff(previousHtml: string, currentHtml: string): ChapterDiffRow[] {
  const previous = htmlBlocks(previousHtml)
  const current = htmlBlocks(currentHtml)
  let prefix = 0
  while (prefix < previous.length && prefix < current.length && previous[prefix] === current[prefix]) prefix += 1

  let suffix = 0
  while (
    suffix < previous.length - prefix &&
    suffix < current.length - prefix &&
    previous[previous.length - 1 - suffix] === current[current.length - 1 - suffix]
  ) suffix += 1

  const before = compactSame(previous.slice(0, prefix).map((text) => ({ kind: 'same' as const, text })))
  const removed = previous
    .slice(prefix, previous.length - suffix)
    .map((text) => ({ kind: 'removed' as const, text }))
  const added = current
    .slice(prefix, current.length - suffix)
    .map((text) => ({ kind: 'added' as const, text }))
  const after = compactSame(
    (suffix ? previous.slice(previous.length - suffix) : []).map((text) => ({ kind: 'same' as const, text }))
  )
  return [...before, ...removed, ...added, ...after]
}
