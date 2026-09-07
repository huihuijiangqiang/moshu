import { Extension } from '@tiptap/core'

export const Provenance = Extension.create({
  name: 'provenance',
  addGlobalAttributes() {
    return [{
      types: ['paragraph'],
      attributes: {
        aiRunId: {
          default: null,
          parseHTML: (element) => element.getAttribute('data-ai-run-id'),
          renderHTML: (attributes) => attributes.aiRunId ? { 'data-ai-run-id': attributes.aiRunId } : {}
        },
        aiSourceHash: {
          default: null,
          parseHTML: (element) => element.getAttribute('data-ai-source-hash'),
          renderHTML: (attributes) => attributes.aiSourceHash ? { 'data-ai-source-hash': attributes.aiSourceHash } : {}
        }
      }
    }]
  }
})
