import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// Lists every built file so "Save for offline use" can cache the whole app,
// including views the user has not opened yet.
function assetList(): Plugin {
  return {
    name: 'ctv-asset-list',
    generateBundle(_, bundle) {
      const files = Object.keys(bundle).filter((f) => !f.endsWith('.map'))
      this.emitFile({ type: 'asset', fileName: 'asset-list.json', source: JSON.stringify(files) })
    },
  }
}

export default defineConfig({
  plugins: [react(), assetList()],
  base: './',
  worker: { format: 'es' },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 2500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('@niivue')) return 'niivue'
          if (id.includes('node_modules/three')) return 'three'
          return undefined
        },
      },
    },
  },
})
