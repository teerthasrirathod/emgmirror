import type { VolumeResult } from './niftiWorker'
export type { VolumeResult }

/** Load a NIfTI volume in a worker; never blocks the main thread. */
export function loadVolume(url: string, maxDim = 256): Promise<VolumeResult> {
  return new Promise((resolve, reject) => {
    const w = new Worker(new URL('./niftiWorker.ts', import.meta.url), { type: 'module' })
    w.onmessage = (e) => {
      w.terminate()
      if (e.data.ok) resolve(e.data.result as VolumeResult)
      else reject(new Error(e.data.error))
    }
    w.onerror = (e) => {
      w.terminate()
      reject(new Error(e.message || 'volume worker failed'))
    }
    w.postMessage({ url: new URL(url, location.href).href, maxDim })
  })
}
