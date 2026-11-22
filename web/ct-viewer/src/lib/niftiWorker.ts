/// <reference lib="webworker" />
// Fetches, decompresses and parses a NIfTI-1 volume off the main thread, and
// returns a half-float copy (optionally downsampled) ready for a WebGL 3D texture.
import { gunzipSync } from 'fflate'

export interface VolumeRequest {
  url: string
  maxDim: number
}

export interface VolumeResult {
  dims: [number, number, number]
  /** voxel index -> RAS millimetres, row-major 4x4 (already scaled for downsampling) */
  affine: number[][]
  /** half-float texels (Uint16 bit patterns), x fastest */
  half: Uint16Array
  min: number
  max: number
  ms: number
}

async function fetchBytes(url: string): Promise<Uint8Array> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`)
  const raw = new Uint8Array(await r.arrayBuffer())
  if (raw[0] === 0x1f && raw[1] === 0x8b) {
    if (typeof DecompressionStream !== 'undefined') {
      const ds = new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip')))
      return new Uint8Array(await ds.arrayBuffer())
    }
    return gunzipSync(raw)
  }
  return raw
}

const f32 = new Float32Array(1)
const u32 = new Uint32Array(f32.buffer)
function toHalf(v: number): number {
  f32[0] = v
  const x = u32[0]
  const sign = (x >>> 16) & 0x8000
  const exp = ((x >>> 23) & 0xff) - 127 + 15
  const mant = x & 0x7fffff
  if (exp <= 0) return sign
  if (exp >= 31) return sign | 0x7c00
  // round to nearest
  let h = sign | (exp << 10) | (mant >>> 13)
  if (mant & 0x1000) h++
  return h
}

function parse(buf: Uint8Array, maxDim: number): Omit<VolumeResult, 'ms'> {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength)
  let le = true
  if (dv.getInt32(0, true) !== 348) {
    if (dv.getInt32(0, false) === 348) le = false
    else throw new Error('Not a NIfTI-1 file')
  }
  const dim = [1, 2, 3].map((i) => dv.getInt16(40 + 2 * i, le)) as [number, number, number]
  const datatype = dv.getInt16(70, le)
  const pixdim = [0, 1, 2, 3].map((i) => dv.getFloat32(76 + 4 * i, le))
  const voxOffset = Math.max(352, Math.round(dv.getFloat32(108, le)))
  let slope = dv.getFloat32(112, le)
  const inter = dv.getFloat32(116, le)
  if (!slope) slope = 1
  const qformCode = dv.getInt16(252, le)
  const sformCode = dv.getInt16(254, le)
  let A: number[][]
  if (sformCode > 0) {
    A = [0, 1, 2].map((r) => [0, 1, 2, 3].map((c) => dv.getFloat32(280 + 16 * r + 4 * c, le)))
    A.push([0, 0, 0, 1])
  } else if (qformCode > 0) {
    const b = dv.getFloat32(256, le), c = dv.getFloat32(260, le), d = dv.getFloat32(264, le)
    const a = Math.sqrt(Math.max(0, 1 - b * b - c * c - d * d))
    const qfac = pixdim[0] < 0 ? -1 : 1
    const R = [
      [a * a + b * b - c * c - d * d, 2 * (b * c - a * d), 2 * (b * d + a * c)],
      [2 * (b * c + a * d), a * a + c * c - b * b - d * d, 2 * (c * d - a * b)],
      [2 * (b * d - a * c), 2 * (c * d + a * b), a * a + d * d - b * b - c * c],
    ]
    const s = [pixdim[1], pixdim[2], pixdim[3] * qfac]
    const off = [dv.getFloat32(268, le), dv.getFloat32(272, le), dv.getFloat32(276, le)]
    A = R.map((row, r) => [row[0] * s[0], row[1] * s[1], row[2] * s[2], off[r]])
    A.push([0, 0, 0, 1])
  } else {
    A = [[pixdim[1], 0, 0, 0], [0, pixdim[2], 0, 0], [0, 0, pixdim[3], 0], [0, 0, 0, 1]]
  }

  const n = dim[0] * dim[1] * dim[2]
  const body = buf.byteOffset + voxOffset
  let get: (i: number) => number
  const ab = buf.buffer
  const aligned = (bytes: number) => body % bytes === 0 && le
  switch (datatype) {
    case 2: { const a = new Uint8Array(ab, body, n); get = (i) => a[i]; break }
    case 4: {
      if (aligned(2)) { const a = new Int16Array(ab, body, n); get = (i) => a[i] }
      else get = (i) => dv.getInt16(voxOffset + 2 * i, le)
      break
    }
    case 512: {
      if (aligned(2)) { const a = new Uint16Array(ab, body, n); get = (i) => a[i] }
      else get = (i) => dv.getUint16(voxOffset + 2 * i, le)
      break
    }
    case 8: {
      if (aligned(4)) { const a = new Int32Array(ab, body, n); get = (i) => a[i] }
      else get = (i) => dv.getInt32(voxOffset + 4 * i, le)
      break
    }
    case 16: {
      if (aligned(4)) { const a = new Float32Array(ab, body, n); get = (i) => a[i] }
      else get = (i) => dv.getFloat32(voxOffset + 4 * i, le)
      break
    }
    default:
      throw new Error(`Unsupported NIfTI datatype ${datatype}`)
  }

  const step = Math.max(1, Math.ceil(Math.max(...dim) / maxDim))
  const od: [number, number, number] = [Math.ceil(dim[0] / step), Math.ceil(dim[1] / step), Math.ceil(dim[2] / step)]
  const half = new Uint16Array(od[0] * od[1] * od[2])
  let min = Infinity, max = -Infinity, o = 0
  for (let z = 0; z < od[2]; z++) {
    const zz = z * step * dim[0] * dim[1]
    for (let y = 0; y < od[1]; y++) {
      const yy = zz + y * step * dim[0]
      for (let x = 0; x < od[0]; x++) {
        const v = get(yy + x * step) * slope + inter
        if (v < min) min = v
        if (v > max) max = v
        half[o++] = toHalf(v)
      }
    }
  }
  const affine = A.map((row, r) => (r < 3 ? [row[0] * step, row[1] * step, row[2] * step, row[3]] : row))
  return { dims: od, affine, half, min, max }
}

self.onmessage = async (e: MessageEvent<VolumeRequest>) => {
  const t0 = performance.now()
  try {
    const bytes = await fetchBytes(e.data.url)
    const res = parse(bytes, e.data.maxDim)
    const out: VolumeResult = { ...res, ms: performance.now() - t0 }
    ;(self as unknown as Worker).postMessage({ ok: true, result: out }, [out.half.buffer])
  } catch (err) {
    ;(self as unknown as Worker).postMessage({ ok: false, error: String((err as Error)?.message ?? err) })
  }
}
