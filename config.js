import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/geocode': 'http://127.0.0.1:3000',
      '/build-matrix': 'http://127.0.0.1:3000',
      '/optimize': 'http://127.0.0.1:3000',
    },
  },
})
