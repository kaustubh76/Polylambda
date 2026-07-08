import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: Vite on :5173 proxies /api → the FastAPI backend on :8000.
// Build: emits ./dist which webapp/backend/main.py serves as static (single-process demo).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', assetsDir: 'assets', sourcemap: false },
})
