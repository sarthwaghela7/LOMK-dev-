import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: './' makes built assets load via relative paths, required for Electron's file:// protocol
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
  },
})
