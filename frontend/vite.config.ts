import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 单机 Localhost 自娱：开发服务器把 /api 与 /static 反代到本机后端。
// 后端默认监听 8000；若该端口被占用，可用 VITE_BACKEND_TARGET 覆盖，例如：
//   $env:VITE_BACKEND_TARGET="http://127.0.0.1:8010"; npm run dev
const backendTarget = process.env.VITE_BACKEND_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: backendTarget, changeOrigin: true },
      '/static': { target: backendTarget, changeOrigin: true },
    },
  },
})
