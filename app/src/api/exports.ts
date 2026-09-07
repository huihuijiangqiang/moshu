import { request, requestResponse } from './http'

export const MAX_BACKUP_BYTES = 100 * 1024 * 1024

export class BackupFileError extends Error {
  constructor(public kind: 'too_large' | 'invalid_json') {
    super(kind)
    this.name = 'BackupFileError'
  }
}

export type ExportFormat = 'txt' | 'markdown' | 'docx' | 'epub'
export type ExportSplit = 'single' | 'zip'

export interface ExportOptions {
  format: ExportFormat
  split: ExportSplit
  includeOutline: boolean
  includeCodex: boolean
}

export interface RestoredProject {
  id: string
  title: string
}

export async function readBackupPayload(file: File): Promise<Record<string, unknown>> {
  if (file.size > MAX_BACKUP_BYTES) throw new BackupFileError('too_large')
  try {
    const payload: unknown = JSON.parse(await file.text())
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      throw new BackupFileError('invalid_json')
    }
    return payload as Record<string, unknown>
  } catch (error) {
    if (error instanceof BackupFileError) throw error
    throw new BackupFileError('invalid_json')
  }
}

function responseFilename(response: Response, fallback: string) {
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (!encoded) return fallback
  try {
    return decodeURIComponent(encoded)
  } catch {
    return fallback
  }
}

async function download(path: string, fallback: string) {
  const response = await requestResponse(path)
  return { blob: await response.blob(), filename: responseFilename(response, fallback) }
}

export const exportApi = {
  manuscript(projectId: string, options: ExportOptions) {
    const query = new URLSearchParams({
      format: options.format,
      split: options.split,
      include_outline: String(options.includeOutline),
      include_codex: String(options.includeCodex)
    })
    return download(`/projects/${projectId}/export?${query}`, `novel.${options.format}`)
  },

  backup(projectId: string) {
    return download(`/projects/${projectId}/backup`, 'moshu-backup.json')
  },

  restore(payload: Record<string, unknown>) {
    return request<RestoredProject>('/projects/restore-backup', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  }
}
