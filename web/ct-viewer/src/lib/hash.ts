// SHA-256 helpers (crypto.subtle runs off the main thread).
export async function sha256(data: ArrayBuffer | Uint8Array | string): Promise<string> {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data
  const d = await crypto.subtle.digest('SHA-256', bytes as BufferSource)
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

async function fetchHash(url: string): Promise<string> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`)
  return sha256(await r.arrayBuffer())
}

/** Hash of the result as delivered: manifest + web model bytes. */
export async function modelHash(manifestUrl: string, glbUrl: string): Promise<string> {
  const [a, b] = await Promise.all([fetchHash(manifestUrl), fetchHash(glbUrl)])
  return sha256(`manifest:${a}\nmodel:${b}`)
}

export const short = (h: string | null | undefined, n = 12) => (h ? h.slice(0, n) : '—')
