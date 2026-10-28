import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = process.env.VITE_BACKEND ?? 'http://127.0.0.1:8110'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5273,
    proxy: { '/api': { target: backend, changeOrigin: true } },
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      // ECharts 体积大且很少变：单独成块，前端发版时浏览器缓存仍然有效
      output: { manualChunks: { echarts: ['echarts'] } },
    },
  },
})
