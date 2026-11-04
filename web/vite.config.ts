import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端只通过 /api/v1 访问后端：开发时走 proxy，生产由 nginx 同源反代。
// 不写后端绝对地址，否则同一份构建产物换个环境就要重打。
export default defineConfig({
  plugins: [react()],
  server: {
    // 显式绑 127.0.0.1：默认的 localhost 在 macOS 上解析到 ::1，
    // 于是 `curl 127.0.0.1:5173` 连不上——排查代理时会被这个坑掉半小时。
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
