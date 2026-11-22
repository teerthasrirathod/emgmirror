import { Canvas, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { VolumeResult } from '../lib/volume'
import type { WindowState } from '../lib/windows'
import { planeBasis, type ClipAxis } from '../model/clip'
import { writeEdgeLabels } from '../lib/orientation'

export interface ObliqueState {
  axis: ClipAxis
  pos: number
  tiltA: number
  tiltB: number
}

interface Props {
  volume: VolumeResult
  lpsToGltf: number[][]
  bounds: THREE.Box3
  state: ObliqueState
  window: WindowState
  onWindow: (w: WindowState) => void
  orientEl?: React.RefObject<HTMLDivElement | null>
}

function worldToTex(v: VolumeResult, lpsToGltf: number[][]): THREE.Matrix4 {
  const L = new THREE.Matrix4().fromArray(lpsToGltf.flat()).transpose()
  const A = new THREE.Matrix4().fromArray(v.affine.flat()).transpose()
  const voxToWorld = L.multiply(new THREE.Matrix4().makeScale(-1, -1, 1)).multiply(A)
  return new THREE.Matrix4()
    .makeScale(1 / v.dims[0], 1 / v.dims[1], 1 / v.dims[2])
    .multiply(new THREE.Matrix4().makeTranslation(0.5, 0.5, 0.5))
    .multiply(voxToWorld.invert())
}

function Plane({ volume, lpsToGltf, bounds, state, window: win, orientEl }: Props) {
  const { camera, size, invalidate } = useThree()
  const tex = useMemo(() => {
    const t = new THREE.Data3DTexture(volume.half, volume.dims[0], volume.dims[1], volume.dims[2])
    t.format = THREE.RedFormat
    t.type = THREE.HalfFloatType
    t.minFilter = THREE.LinearFilter
    t.magFilter = THREE.LinearFilter
    t.unpackAlignment = 1
    t.needsUpdate = true
    return t
  }, [volume])
  useEffect(() => () => tex.dispose(), [tex])
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          uVolume: { value: tex },
          uWorldToTex: { value: worldToTex(volume, lpsToGltf) },
          uWindow: { value: new THREE.Vector2(win.centre, win.width) },
        },
        vertexShader: `varying vec3 vWorld; void main(){ vec4 w = modelMatrix * vec4(position,1.0); vWorld = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`,
        fragmentShader: `
uniform highp sampler3D uVolume; uniform mat4 uWorldToTex; uniform vec2 uWindow; varying vec3 vWorld;
void main(){
  vec3 t = (uWorldToTex * vec4(vWorld,1.0)).xyz;
  if (any(lessThan(t, vec3(0.0))) || any(greaterThan(t, vec3(1.0)))) { gl_FragColor = vec4(0.0,0.0,0.0,1.0); return; }
  float hu = texture(uVolume, t).r;
  float g = clamp((hu - (uWindow.x - 0.5*uWindow.y)) / uWindow.y, 0.0, 1.0);
  gl_FragColor = vec4(vec3(g), 1.0);
}`,
        side: THREE.DoubleSide,
        toneMapped: false,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tex],
  )
  useEffect(() => {
    mat.uniforms.uWindow.value.set(win.centre, win.width)
    invalidate()
  }, [win, mat, invalidate])

  const mesh = useRef<THREE.Mesh>(null)
  useEffect(() => {
    const basis = planeBasis({ ...state, mode: 'plane', flip: false, box: { x: [0, 1], y: [0, 1], z: [0, 1] } })
    const u = new THREE.Vector3(), v = new THREE.Vector3(), w = new THREE.Vector3()
    basis.extractBasis(u, v, w)
    const c = bounds.getCenter(new THREE.Vector3())
    const ext = bounds.getSize(new THREE.Vector3()).length()
    let lo = Infinity, hi = -Infinity
    for (let i = 0; i < 8; i++) {
      const p = new THREE.Vector3(i & 1 ? bounds.max.x : bounds.min.x, i & 2 ? bounds.max.y : bounds.min.y, i & 4 ? bounds.max.z : bounds.min.z)
      lo = Math.min(lo, p.dot(w)); hi = Math.max(hi, p.dot(w))
    }
    const d = lo + (hi - lo) * state.pos
    const centre = c.clone().addScaledVector(w, d - c.dot(w))
    const m = mesh.current!
    m.matrixAutoUpdate = false
    m.matrix.copy(basis).setPosition(centre).multiply(new THREE.Matrix4().makeScale(ext, ext, 1))
    m.matrixWorldNeedsUpdate = true
    const cam = camera as THREE.OrthographicCamera
    // display conventions: axial seen from the feet with anterior up; coronal
    // seen from the front; sagittal seen from the patient's left, anterior on the left
    const fromSide = state.axis === 'axial' ? -1 : 1
    const up = state.axis === 'coronal' ? v : u
    cam.position.copy(centre).addScaledVector(w, fromSide * ext)
    cam.up.copy(up)
    cam.lookAt(centre)
    const aspect = size.width / Math.max(1, size.height)
    const half = ext * 0.36
    cam.left = -half * Math.max(1, aspect); cam.right = half * Math.max(1, aspect)
    cam.top = half / Math.min(1, aspect); cam.bottom = -half / Math.min(1, aspect)
    cam.near = 0.001; cam.far = ext * 3
    cam.updateProjectionMatrix()
    cam.updateMatrixWorld()
    if (orientEl) writeEdgeLabels(orientEl.current, cam.quaternion)
    invalidate()
  }, [state, bounds, camera, size, invalidate, orientEl])

  return (
    <mesh ref={mesh} material={mat}>
      <planeGeometry args={[1, 1]} />
    </mesh>
  )
}

export default function ObliqueView(props: Props) {
  const drag = useRef<{ x: number; y: number; w: WindowState } | null>(null)
  return (
    <div
      className="oblique-wrap"
      onPointerDown={(e) => {
        ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
        drag.current = { x: e.clientX, y: e.clientY, w: props.window }
      }}
      onPointerMove={(e) => {
        const d = drag.current
        if (!d) return
        const width = Math.max(1, d.w.width + (e.clientX - d.x) * 4)
        const centre = d.w.centre - (e.clientY - d.y) * 2
        props.onWindow({ centre, width, preset: 'custom' })
      }}
      onPointerUp={() => (drag.current = null)}
      onPointerCancel={() => (drag.current = null)}
    >
      <Canvas orthographic frameloop="demand" dpr={[1, 2]} gl={{ antialias: true, preserveDrawingBuffer: true }} aria-hidden="true">
        <color attach="background" args={['#000']} />
        <Plane {...props} />
      </Canvas>
    </div>
  )
}
