import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },
  server: {
    port: 5173,
    // 后端就绪后打开：所有 /api 请求转发到 FastAPI / NestJS
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalized = id.replaceAll('\\\\', '/')
          if (
            normalized.includes('/node_modules/@tiptap/')
            || normalized.includes('/node_modules/prosemirror-')
          ) return 'editor-core'
          return undefined
        }
      }
    }
  }
})
