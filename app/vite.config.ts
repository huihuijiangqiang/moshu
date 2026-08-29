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
      // '/api': { target: 'http://localhost:8000', changeOrigin: true }
    }
  }
})
