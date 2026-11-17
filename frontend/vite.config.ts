import { Agent } from 'node:http'
import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'

// In development the proxy sends /api to the service at VITE_API_URL, presents its
// local token (AGDASH_TOKEN in the developer's shell), so the page itself never holds
// it, and drops the browser's Origin, since the service only accepts agdash's own
// origins. When nothing answers there it says so as a 502, which the page reads as an
// unreachable service rather than as the service's own failure.
const target = process.env.VITE_API_URL || 'http://127.0.0.1:8765'
const token = process.env.AGDASH_TOKEN
const toService: ProxyOptions = {
  target,
  changeOrigin: true,
  // A fresh connection per request: reusing one after the browser aborted a request on
  // it could lose the request sent next, which then never settled.
  agent: new Agent({ keepAlive: false }),
  headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  configure: (proxy) => {
    proxy.on('proxyReq', (request) => request.removeHeader('origin'))
    proxy.on('error', (_error, _request, response) => {
      if (!('writeHead' in response)) return response.destroy()
      if (response.headersSent) return response.destroy()
      const body = JSON.stringify({ detail: `nothing answers at ${target}` })
      response.writeHead(502, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) })
      response.end(body)
    })
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
