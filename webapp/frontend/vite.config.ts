/// <reference types="vitest/config" />
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
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    // split the heavy deps into their own cacheable vendor chunks instead of one ~1MB blob.
    // (Below-the-fold chart sections are additionally React.lazy'd in App.tsx, so recharts loads
    // on scroll.) chunkSizeWarningLimit lowered — no single chunk should approach the old blob.
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          recharts: ['recharts'],
          viem: ['viem'],
          motion: ['framer-motion'],
        },
      },
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
