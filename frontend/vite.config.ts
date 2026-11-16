import { Agent } from 'node:http'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the proxy presents the service's local token (AGDASH_TOKEN in the
// developer's shell), so the page itself never holds it, and drops the browser's
// Origin, since the service only accepts agdash's own origins.
const target = process.env.VITE_API_URL || 'http://127.0.0.1:8765'
const token = process.env.AGDASH_TOKEN
const toService = {
  target,
  changeOrigin: true,
  // A fresh connection per request: reusing one after the browser aborted a request on
  // it could lose the request sent next, which then never settled.
  agent: new Agent({ keepAlive: false }),
  headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  configure: (proxy: { on: (event: 'proxyReq', handler: (request: { removeHeader: (name: string) => void }) => void) => void }) => {
    proxy.on('proxyReq', (request) => request.removeHeader('origin'))
  },
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5174,
    proxy: { '/api': toService, '/health': toService },
  },
})
