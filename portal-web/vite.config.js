import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// En desarrollo, /api se reenvía al backend local (python -m cxc_portal.server, puerto 8780).
export default defineConfig({
  plugins: [react()],
  server: { port: 5180, proxy: { '/api': 'http://127.0.0.1:8780' } },
})
