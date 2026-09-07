import type { Node as ProseMirrorNode } from '@tiptap/pm/model'

export interface ParagraphTextSelection {
  from: number
  to: number
}

function utf16Offset(text: string, codePointOffset: number) {
  return [...text].slice(0, codePointOffset).join('').length
}

/** Map backend code-point offsets onto ProseMirror's UTF-16 document positions. */
export function findParagraphTextSelection(
  doc: ProseMirrorNode,
  paragraphId: string,
  start: number,
  end: number
): ParagraphTextSelection | null {
  if (!paragraphId || !Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) {
    return null
  }

  let selection: ParagraphTextSelection | null = null
  doc.descendants((node, pos) => {
    if (selection || node.attrs.pid !== paragraphId) return false
    const codePointLength = [...node.textContent].length
    if (start >= codePointLength) return false
    const safeEnd = Math.min(end, codePointLength)
    selection = {
      from: pos + 1 + utf16Offset(node.textContent, start),
      to: pos + 1 + utf16Offset(node.textContent, safeEnd)
    }
    return false
  })
  return selection
}
