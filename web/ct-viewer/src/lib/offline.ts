// Service-worker registration and "save this result for offline use".
export async function registerSW(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator) || import.meta.env.DEV) return null
  try {
    return await navigator.serviceWorker.register('./sw.js', { scope: './' })
  } catch {
    return null
  }
}

/** All built files (from asset-list.json written at build time). */
export async function builtAssets(): Promise<string[]> {
  try {
    const r = await fetch(new URL('asset-list.json', location.href), { cache: 'no-store' })
    if (!r.ok) return []
    return ((await r.json()) as string[]).map((f) => new URL(f, location.href).href)
  } catch {
    return []
  }
}

/** Every same-origin file this page has loaded, plus the result's files. */
export function offlineUrls(resultFiles: string[]): string[] {
  const here = location.origin
  const loaded = performance
    .getEntriesByType('resource')
    .map((e) => e.name)
    .filter((u) => u.startsWith(here))
  const page = new URL(location.href)
  const shell = [page.pathname, page.pathname + page.search]
  const all = [...shell, ...loaded, ...resultFiles.map((f) => new URL(f, location.href).href)]
  return [...new Set(all.map((u) => new URL(u, location.href).href))]
}

async function controller(): Promise<ServiceWorker | null> {
  if (!('serviceWorker' in navigator)) return null
  if (navigator.serviceWorker.controller) return navigator.serviceWorker.controller
  await navigator.serviceWorker.ready
  if (navigator.serviceWorker.controller) return navigator.serviceWorker.controller
  await new Promise((r) => {
    navigator.serviceWorker.addEventListener('controllerchange', r, { once: true })
    setTimeout(r, 3000)
  })
  return navigator.serviceWorker.controller
}

export async function saveOffline(urls: string[], onProgress: (done: number, total: number) => void): Promise<{ failed: string[]; time: string }> {
  const sw = await controller()
  return new Promise((resolve, reject) => {
    if (!sw) {
      reject(new Error('offline storage is not ready; reload the page once and try again'))
      return
    }
    const onMsg = (e: MessageEvent) => {
      if (e.data?.type === 'save-progress') onProgress(e.data.done, e.data.total)
      if (e.data?.type === 'save-done') {
        navigator.serviceWorker.removeEventListener('message', onMsg)
        resolve({ failed: e.data.failed, time: e.data.time })
      }
    }
    navigator.serviceWorker.addEventListener('message', onMsg)
    sw.postMessage({ type: 'save', urls })
  })
}
