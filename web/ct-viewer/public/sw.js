// Offline cache for the viewer (reference mode).
// - Navigations and manifest.json: network first, cache fallback, so an online
//   viewer always sees the newest result and its version hash.
// - Everything else from this origin: cache first once saved.
// The page asks for a result to be saved with postMessage({type:'save', urls}).
const CACHE = 'ctv-offline-v1'

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()))

self.addEventListener('message', (e) => {
  const msg = e.data || {}
  if (msg.type !== 'save') return
  e.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE)
      let done = 0
      const failed = []
      for (const url of msg.urls) {
        try {
          const r = await fetch(url, { cache: 'reload' })
          if (!r.ok) throw new Error(String(r.status))
          await cache.put(url, r)
        } catch (err) {
          failed.push(url)
        }
        done++
        e.source && e.source.postMessage({ type: 'save-progress', done, total: msg.urls.length })
      }
      e.source && e.source.postMessage({ type: 'save-done', failed, time: new Date().toISOString() })
    })(),
  )
})

self.addEventListener('fetch', (e) => {
  const req = e.request
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return
  const url = new URL(req.url)
  const networkFirst = req.mode === 'navigate' || url.pathname.endsWith('/manifest.json')
  if (networkFirst) {
    e.respondWith(
      fetch(req)
        .then((r) => {
          if (r.ok) caches.open(CACHE).then((c) => c.put(req.mode === 'navigate' ? url.pathname : req, r.clone()))
          return r
        })
        .catch(async () => (await caches.match(req, { ignoreSearch: req.mode === 'navigate', ignoreVary: true })) || (await caches.match(url.pathname, { ignoreVary: true })) || Response.error()),
    )
    return
  }
  e.respondWith(caches.match(req, { ignoreVary: true }).then((hit) => hit || fetch(req)))
})
