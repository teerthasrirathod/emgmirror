import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'meshoptimizer/decoder'
import type { Structure } from '../lib/manifest'
import type { VolumeResult } from '../lib/volume'
import { ModelEngine, type ClipRegion } from './engine'
import { clipRegion, type ClipState } from './clip'
import { writeEdgeLabels } from '../lib/orientation'

export type ViewPreset = 'anterior' | 'posterior' | 'left' | 'right' | 'superior' | 'inferior'

export interface StructureState {
  visible: boolean
  opacity: number
}

export interface ModelViewProps {
  glbUrl: string
  structures: Structure[]
  states: StructureState[]
  volume: VolumeResult | null
  lpsToGltf: number[][]
  clip: ClipState
  window: { centre: number; width: number }
  ctOpacity: number
  isolated: number
  selected: number
  preset: { name: ViewPreset; seq: number }
  dark: boolean
  reducedMotion: boolean
  lowPower: boolean
  onLoaded: (info: { ms: number; triangles: number }) => void
  onError: (msg: string) => void
  onHover: (index: number, x: number, y: number) => void
  onPick: (index: number) => void
  onBounds: (b: THREE.Box3) => void
  /** 'erase': a click removes surface within eraseRadiusMm of the clicked point */
  tool: 'view' | 'erase'
  onErase: (index: number, point: THREE.Vector3) => void
  erasures: { index: number; center: THREE.Vector3; radius: number }[]
  hoverEnabled: boolean
  orientEl: React.RefObject<HTMLDivElement | null>
}

MeshoptDecoder.useWorkers?.(Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 2) - 1)))

function loadGlb(url: string): Promise<THREE.Group> {
  const loader = new GLTFLoader()
  loader.setMeshoptDecoder(MeshoptDecoder)
  return loader.loadAsync(url).then((g) => g.scene)
}

const PRESET_DIR: Record<ViewPreset, { dir: THREE.Vector3; up: THREE.Vector3 }> = {
  // glTF axes from LPS: +X = patient left, +Y = superior, +Z = anterior
  anterior: { dir: new THREE.Vector3(0, 0, 1), up: new THREE.Vector3(0, 1, 0) },
  posterior: { dir: new THREE.Vector3(0, 0, -1), up: new THREE.Vector3(0, 1, 0) },
  left: { dir: new THREE.Vector3(1, 0, 0), up: new THREE.Vector3(0, 1, 0) },
  right: { dir: new THREE.Vector3(-1, 0, 0), up: new THREE.Vector3(0, 1, 0) },
  superior: { dir: new THREE.Vector3(0, 1, 0), up: new THREE.Vector3(0, 0, 1) },
  inferior: { dir: new THREE.Vector3(0, -1, 0), up: new THREE.Vector3(0, 0, 1) },
}

function Scene(props: ModelViewProps) {
  const { gl, camera, size, invalidate } = useThree()
  const controls = useRef<OrbitControlsImpl>(null)
  const [engine, setEngine] = useState<ModelEngine | null>(null)
  const anim = useRef<{ from: THREE.Vector3; to: THREE.Vector3; upFrom: THREE.Vector3; upTo: THREE.Vector3; t0: number; dur: number } | null>(null)
  const propsRef = useRef(props)
  const pendingReady = useRef<{ t0: number; tris: number } | null>(null)
  propsRef.current = props

  // create engine + load model
  useEffect(() => {
    const cam = camera as THREE.PerspectiveCamera
    const e = new ModelEngine(gl, cam, {
      samples: props.lowPower ? 2 : 4,
      peelLayers: props.lowPower ? 2 : 4,
      aoScale: props.lowPower ? 0.5 : 1,
    })
    let cancelled = false
    const t0 = performance.now()
    loadGlb(props.glbUrl)
      .then(async (root) => {
        if (cancelled) return
        e.setStructures(root, props.structures)
        propsRef.current.states.forEach((st, i) => {
          const it = e.items.find((x) => x.index === i)
          if (it) {
            it.visible = st.visible
            it.opacity = st.opacity
          }
        })
        e.rebuild()
        fit(e, cam, 'anterior', false)
        await e.warmUp()
        if (cancelled) return
        let tris = 0
        for (const it of e.items) tris += (it.mesh.geometry.index?.count ?? it.mesh.geometry.attributes.position.count) / 3
        pendingReady.current = { t0, tris: Math.round(tris) }
        setEngine(e)
        propsRef.current.onBounds(e.bounds.clone())
        invalidate()
      })
      .catch((err) => propsRef.current.onError(`Could not load the 3D model: ${err?.message ?? err}`))
    return () => {
      cancelled = true
      e.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.glbUrl])

  useEffect(() => {
    if (!engine) return
    const dpr = gl.getPixelRatio()
    engine.setSize(Math.max(1, Math.floor(size.width * dpr)), Math.max(1, Math.floor(size.height * dpr)))
    invalidate()
  }, [engine, size.width, size.height, gl, invalidate])

  useEffect(() => {
    if (!engine) return
    engine.setTheme(props.dark)
    invalidate()
  }, [engine, props.dark, invalidate])

  useEffect(() => {
    if (!engine) return
    props.states.forEach((s, i) => {
      const it = engine.items.find((x) => x.index === i)
      if (it) {
        it.visible = s.visible
        it.opacity = s.opacity
      }
    })
    engine.isolated = props.isolated
    engine.selected = props.selected
    engine.rebuild()
    invalidate()
  }, [engine, props.states, props.isolated, props.selected, invalidate])

  useEffect(() => {
    if (!engine) return
    engine.setErasures(props.erasures)
    invalidate()
  }, [engine, props.erasures, invalidate])

  useEffect(() => {
    if (!engine || !props.volume) return
    engine.setVolume(props.volume, props.lpsToGltf)
    invalidate()
  }, [engine, props.volume, props.lpsToGltf, invalidate])

  useEffect(() => {
    if (!engine) return
    engine.setWindow(props.window.centre, props.window.width)
    engine.setCTOpacity(props.ctOpacity)
    invalidate()
  }, [engine, props.window, props.ctOpacity, invalidate])

  useEffect(() => {
    if (!engine) return
    const region: ClipRegion | null = clipRegion(props.clip, engine.bounds)
    engine.setClip(region)
    invalidate()
  }, [engine, props.clip, invalidate])

  // camera presets
  useEffect(() => {
    if (!engine || props.preset.seq === 0) return
    fit(engine, camera as THREE.PerspectiveCamera, props.preset.name, !props.reducedMotion)
    invalidate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.preset.seq])

  function fit(e: ModelEngine, cam: THREE.PerspectiveCamera, name: ViewPreset, animate: boolean) {
    const sphere = e.bounds.getBoundingSphere(new THREE.Sphere())
    const p = PRESET_DIR[name]
    // fit the box as seen from this direction (tighter than the bounding sphere)
    const sz = e.bounds.getSize(new THREE.Vector3())
    const right = new THREE.Vector3().crossVectors(p.up, p.dir).normalize()
    const extent = (axis: THREE.Vector3) => Math.abs(axis.x) * sz.x + Math.abs(axis.y) * sz.y + Math.abs(axis.z) * sz.z
    const w = extent(right), h = extent(p.up), depth = extent(p.dir)
    const aspect = Math.max(0.3, size.width / Math.max(1, size.height))
    const t = Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2)
    const dist = Math.max(h / 2 / t, w / 2 / (t * aspect)) * 1.12 + depth / 2
    const to = sphere.center.clone().addScaledVector(p.dir, dist)
    cam.near = dist / 50
    cam.far = dist * 6
    cam.updateProjectionMatrix()
    controls.current?.target.copy(sphere.center)
    if (animate) {
      anim.current = { from: cam.position.clone(), to, upFrom: cam.up.clone(), upTo: p.up.clone(), t0: performance.now(), dur: 450 }
    } else {
      cam.position.copy(to)
      cam.up.copy(p.up)
      cam.lookAt(sphere.center)
      controls.current?.update()
    }
  }

  useFrame(() => {
    const a = anim.current
    if (a) {
      const t = Math.min(1, (performance.now() - a.t0) / a.dur)
      const k = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
      const target = controls.current?.target ?? new THREE.Vector3()
      // slerp around the target so the camera keeps its distance
      const r0 = a.from.clone().sub(target)
      const r1 = a.to.clone().sub(target)
      const q = new THREE.Quaternion().setFromUnitVectors(r0.clone().normalize(), r1.clone().normalize())
      const qk = new THREE.Quaternion().slerp(q, k)
      const len = THREE.MathUtils.lerp(r0.length(), r1.length(), k)
      camera.position.copy(target).add(r0.normalize().applyQuaternion(qk).multiplyScalar(len))
      camera.up.copy(a.upFrom).lerp(a.upTo, k).normalize()
      camera.lookAt(target)
      controls.current?.update()
      if (t >= 1) anim.current = null
      invalidate()
    }
    if (!engine) return
    engine.render()
    writeEdgeLabels(propsRef.current.orientEl.current, camera.quaternion)
    const pr = pendingReady.current
    if (pr) {
      // report "ready" only once the first full frame has been drawn
      pendingReady.current = null
      propsRef.current.onLoaded({ ms: performance.now() - pr.t0, triangles: pr.tris })
    }
  }, 1)

  // keyboard: arrows orbit, + / - zoom (the view container is focusable)
  useEffect(() => {
    const wrap = gl.domElement.closest('[data-testid="model-canvas"]') as HTMLElement | null
    if (!wrap) return
    const onKey = (ev: KeyboardEvent) => {
      const c = controls.current
      if (!c) return
      const target = c.target
      const off = camera.position.clone().sub(target)
      const step = THREE.MathUtils.degToRad(ev.shiftKey ? 45 : 15)
      const right = new THREE.Vector3().crossVectors(camera.up, off).normalize()
      let handled = true
      if (ev.key === 'ArrowLeft') off.applyAxisAngle(camera.up, -step)
      else if (ev.key === 'ArrowRight') off.applyAxisAngle(camera.up, step)
      else if (ev.key === 'ArrowUp') { off.applyAxisAngle(right, -step); camera.up.applyAxisAngle(right, -step) }
      else if (ev.key === 'ArrowDown') { off.applyAxisAngle(right, step); camera.up.applyAxisAngle(right, step) }
      else if (ev.key === '+' || ev.key === '=') off.multiplyScalar(0.85)
      else if (ev.key === '-' || ev.key === '_') off.multiplyScalar(1 / 0.85)
      else handled = false
      if (!handled) return
      ev.preventDefault()
      camera.position.copy(target).add(off)
      camera.lookAt(target)
      c.update()
      invalidate()
    }
    wrap.addEventListener('keydown', onKey)
    return () => wrap.removeEventListener('keydown', onKey)
  }, [gl, camera, invalidate])

  // pointer: hover tooltip + click to isolate
  useEffect(() => {
    if (!engine) return
    const el = gl.domElement
    let raf = 0
    let last: PointerEvent | null = null
    let down: { x: number; y: number; t: number } | null = null
    const pickAt = (ev: PointerEvent) => {
      const rect = el.getBoundingClientRect()
      const dpr = gl.getPixelRatio()
      return engine.pick((ev.clientX - rect.left) * dpr, (ev.clientY - rect.top) * dpr)
    }
    const onMove = (ev: PointerEvent) => {
      if (ev.pointerType !== 'mouse' || ev.buttons || !propsRef.current.hoverEnabled) return
      last = ev
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        if (!last) return
        const idx = pickAt(last)
        const rect = el.getBoundingClientRect()
        if (idx !== engine.hovered) {
          engine.hovered = idx
          engine.rebuild()
          invalidate()
        }
        el.style.cursor = idx >= 0 ? (propsRef.current.tool === 'erase' ? 'crosshair' : 'pointer') : 'grab'
        propsRef.current.onHover(idx, last.clientX - rect.left, last.clientY - rect.top)
      })
    }
    const onLeave = () => {
      engine.hovered = -1
      engine.rebuild()
      invalidate()
      propsRef.current.onHover(-1, 0, 0)
    }
    const onDown = (ev: PointerEvent) => {
      down = { x: ev.clientX, y: ev.clientY, t: performance.now() }
    }
    const onUp = (ev: PointerEvent) => {
      if (!down) return
      const moved = Math.hypot(ev.clientX - down.x, ev.clientY - down.y)
      if (moved < 6 && performance.now() - down.t < 600) {
        const idx = pickAt(ev)
        if (propsRef.current.tool === 'erase') {
          if (idx >= 0) {
            const rect = el.getBoundingClientRect()
            const dpr = gl.getPixelRatio()
            const p = engine.surfacePoint(idx, (ev.clientX - rect.left) * dpr, (ev.clientY - rect.top) * dpr)
            if (p) propsRef.current.onErase(idx, p)
          }
        } else propsRef.current.onPick(idx)
      }
      down = null
    }
    el.addEventListener('pointermove', onMove)
    el.addEventListener('pointerleave', onLeave)
    el.addEventListener('pointerdown', onDown)
    el.addEventListener('pointerup', onUp)
    return () => {
      cancelAnimationFrame(raf)
      el.removeEventListener('pointermove', onMove)
      el.removeEventListener('pointerleave', onLeave)
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('pointerup', onUp)
    }
  }, [engine, gl, invalidate])

  return (
    <OrbitControls
      ref={controls as never}
      makeDefault
      enableDamping={!props.reducedMotion}
      dampingFactor={0.12}
      rotateSpeed={0.8}
      zoomSpeed={0.9}
      minDistance={0.05}
      maxDistance={20}
    />
  )
}

export default function ModelView(props: ModelViewProps) {
  const dpr = useMemo<[number, number]>(() => (props.lowPower ? [1, 1.5] : [1, 2]), [props.lowPower])
  return (
    <Canvas
      className="model-canvas"
      frameloop="demand"
      dpr={dpr}
      camera={{ fov: 30, position: [0, 0, 3], near: 0.01, far: 50 }}
      gl={{ antialias: false, alpha: false, stencil: true, powerPreference: 'high-performance', preserveDrawingBuffer: true }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.NoToneMapping
        gl.outputColorSpace = THREE.SRGBColorSpace
      }}
      aria-hidden="true"
    >
      <Scene {...props} />
    </Canvas>
  )
}
