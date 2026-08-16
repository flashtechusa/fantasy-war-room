import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The build lands directly in the FastAPI static directory, so `uvicorn` alone
// serves the whole app in production. In dev, /api is proxied to the backend.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/app/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
