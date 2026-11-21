import * as THREE from 'three'
import type { ClipRegion } from './engine'

export type ClipAxis = 'axial' | 'coronal' | 'sagittal'

export interface ClipState {
  mode: 'off' | 'plane' | 'box'
  axis: ClipAxis
  /** 0..1 along the model extent */
  pos: number
  /** keep the other side of the plane */
  flip: boolean
  /** oblique tilt in degrees around the plane's two in-plane axes */
  tiltA: number
  tiltB: number
  box: { x: [number, number]; y: [number, number]; z: [number, number] }
}

export const DEFAULT_CLIP: ClipState = {
  mode: 'off',
  axis: 'axial',
  pos: 0.55,
  flip: false,
  tiltA: 0,
  tiltB: 0,
  box: { x: [0.15, 0.85], y: [0.2, 0.8], z: [0.0, 0.6] },
}

// Right-handed bases (u x v = w). glTF axes: +X patient left, +Y superior, +Z anterior.
// By default the kept side is the one away from the usual viewing side, so
// the cut face looks at the camera preset of the same name.
const AXIS_BASIS: Record<ClipAxis, [THREE.Vector3, THREE.Vector3, THREE.Vector3]> = {
  axial: [new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0)],
  coronal: [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)],
  sagittal: [new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0)],
}

function project(b: THREE.Box3, axis: THREE.Vector3): [number, number] {
  let lo = Infinity, hi = -Infinity
  for (let i = 0; i < 8; i++) {
    const p = new THREE.Vector3(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z)
    const d = p.dot(axis)
    lo = Math.min(lo, d)
    hi = Math.max(hi, d)
  }
  return [lo, hi]
}

export function planeBasis(c: ClipState): THREE.Matrix4 {
  const [u0, v0, w0] = AXIS_BASIS[c.axis].map((a) => a.clone())
  const q = new THREE.Quaternion()
    .setFromAxisAngle(u0, THREE.MathUtils.degToRad(c.tiltA))
    .multiply(new THREE.Quaternion().setFromAxisAngle(v0, THREE.MathUtils.degToRad(c.tiltB)))
  const u = u0.applyQuaternion(q), v = v0.applyQuaternion(q), w = w0.applyQuaternion(q)
  return new THREE.Matrix4().makeBasis(u, v, w)
}

export function clipRegion(c: ClipState, bounds: THREE.Box3): ClipRegion | null {
  if (c.mode === 'off' || bounds.isEmpty()) return null
  const size = bounds.getSize(new THREE.Vector3()).length()
  const R = size * 3
  if (c.mode === 'box') {
    const lerp = (lo: number, hi: number, t: number) => lo + (hi - lo) * t
    const min = new THREE.Vector3(
      lerp(bounds.min.x, bounds.max.x, c.box.x[0]),
      lerp(bounds.min.y, bounds.max.y, c.box.y[0]),
      lerp(bounds.min.z, bounds.max.z, c.box.z[0]),
    )
    const max = new THREE.Vector3(
      lerp(bounds.min.x, bounds.max.x, Math.max(c.box.x[1], c.box.x[0] + 0.01)),
      lerp(bounds.min.y, bounds.max.y, Math.max(c.box.y[1], c.box.y[0] + 0.01)),
      lerp(bounds.min.z, bounds.max.z, Math.max(c.box.z[1], c.box.z[0] + 0.01)),
    )
    return { basis: new THREE.Matrix4(), min, max }
  }
  const basis = planeBasis(c)
  const u = new THREE.Vector3(), v = new THREE.Vector3(), w = new THREE.Vector3()
  basis.extractBasis(u, v, w)
  const [ulo, uhi] = project(bounds, u)
  const [vlo, vhi] = project(bounds, v)
  const [wlo, whi] = project(bounds, w)
  const d = wlo + (whi - wlo) * c.pos
  const uc = (ulo + uhi) / 2, vc = (vlo + vhi) / 2
  const min = new THREE.Vector3(uc - R, vc - R, c.flip ? d : wlo - R)
  const max = new THREE.Vector3(uc + R, vc + R, c.flip ? whi + R : d)
  return { basis, min, max }
}
