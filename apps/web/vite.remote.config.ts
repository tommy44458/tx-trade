import { renameSync } from 'node:fs'
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// The remote page is deployed on its own origin, and Google sign-in returns to that
// origin's root. Serve and publish remote.html as the root document.
const OUT_DIR = resolve(import.meta.dirname, 'dist-remote')

const remoteIndex = (): Plugin => ({
  name: 'txintrade-remote-index',
  configureServer(server) {
    server.middlewares.use((request, _response, next) => {
      if (request.url === '/' || request.url?.startsWith('/?')) request.url = `/remote.html${request.url.slice(1)}`
      next()
    })
  },
  closeBundle() {
    renameSync(resolve(OUT_DIR, 'remote.html'), resolve(OUT_DIR, 'index.html'))
  },
})

export default defineConfig({
  plugins: [react(), remoteIndex()],
  // The cloud session cookie is set for "localhost"; open the page on the same host name.
  server: { host: 'localhost', port: 5174, strictPort: true },
  preview: { host: 'localhost', port: 5174, strictPort: true },
  build: { outDir: OUT_DIR, rollupOptions: { input: 'remote.html' } },
})
