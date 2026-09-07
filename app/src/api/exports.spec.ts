import { describe, expect, it } from 'vitest'
import { MAX_BACKUP_BYTES, readBackupPayload } from './exports'

describe('readBackupPayload', () => {
  it('accepts a JSON object backup', async () => {
    const file = new File(['{"schema_version":1}'], 'backup.json', { type: 'application/json' })
    await expect(readBackupPayload(file)).resolves.toEqual({ schema_version: 1 })
  })

  it('rejects malformed JSON and non-object roots', async () => {
    const malformed = new File(['{'], 'broken.json')
    const array = new File(['[]'], 'array.json')
    await expect(readBackupPayload(malformed)).rejects.toMatchObject({ kind: 'invalid_json' })
    await expect(readBackupPayload(array)).rejects.toMatchObject({ kind: 'invalid_json' })
  })

  it('checks file size before reading its contents', async () => {
    let read = false
    const file = {
      size: MAX_BACKUP_BYTES + 1,
      text: async () => {
        read = true
        return '{}'
      }
    } as File

    await expect(readBackupPayload(file)).rejects.toMatchObject({ kind: 'too_large' })
    expect(read).toBe(false)
  })
})
