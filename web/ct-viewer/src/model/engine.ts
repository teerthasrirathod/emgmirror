// Rendering engine for the 3D model view.
//
// Frame:
//  1. opaque pass into a multisampled HDR target: opaque structures, then for
//     each cut structure a stencil pair (back +1 / front -1) and its cap
//  2. GTAO (ambient occlusion) from that depth buffer, multiplied in place
//  3. CT slice on the cut faces (blended, behind the caps)
//  4. depth peeling of translucent structures (N layers), composited back to front
//  5. tone mapping + background to the screen
import * as THREE from 'three'
import { GTAOPass } from 'three/examples/jsm/postprocessing/GTAOPass.js'
import { FullScreenQuad } from 'three/examples/jsm/postprocessing/Pass.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import type { Structure } from '../lib/manifest'
import type { VolumeResult } from '../lib/volume'
import {
  capMaterial,
  ctPlaneMaterial,
  defaultOpacity,
  makeCTUniforms,
  makePeelable,
  pickMaterial,
  stencilMaterial,
  surfaceMaterial,
  type PeelUniforms,
} from './materials'

export interface ClipRegion {
  /** orthonormal basis (columns u, v, w) of the region */
  basis: THREE.Matrix4
  /** min / max along u, v, w in metres */
  min: THREE.Vector3
  max: THREE.Vector3
}

interface Item {
  s: Structure
  index: number
  mesh: THREE.Mesh
  surface: THREE.MeshPhysicalMaterial
  peel: THREE.MeshPhysicalMaterial
  pick: THREE.ShaderMaterial
  pickMesh: THREE.Mesh
  back: THREE.Mesh
  front: THREE.Mesh
  cap: THREE.Mesh
  box: THREE.Box3
  visible: boolean
  opacity: number
}

export interface EngineQuality {
  samples: number
  peelLayers: number
  aoScale: number
}

const FAR = 1e3

export class ModelEngine {
  readonly renderer: THREE.WebGLRenderer
  readonly camera: THREE.PerspectiveCamera
  readonly planes: THREE.Plane[] = Array.from({ length: 6 }, () => new THREE.Plane(new THREE.Vector3(1, 0, 0), FAR))
  items: Item[] = []
  bounds = new THREE.Box3()
  quality: EngineQuality
  background: [THREE.Color, THREE.Color] = [new THREE.Color('#eef0f3'), new THREE.Color('#d9dde3')]

  private opaqueScene = new THREE.Scene()
  private transScene = new THREE.Scene()
  private pickScene = new THREE.Scene()
  private keyLights: THREE.DirectionalLight[] = []
  private ct = makeCTUniforms()
  private ctPlane: THREE.Mesh
  private capGeom = new THREE.BoxGeometry(1, 1, 1)
  private capBoxMatrix = new THREE.Matrix4()
  private clip: ClipRegion | null = null
  private ctOpacity = 0.92
  private rtMain!: THREE.WebGLRenderTarget
  private rtPeel: THREE.WebGLRenderTarget[] = []
  private rtPick = new THREE.WebGLRenderTarget(1, 1)
  private gtao!: GTAOPass
  private quad = new FullScreenQuad()
  private compositeMat: THREE.ShaderMaterial
  private outputMat: THREE.ShaderMaterial
  private peelU: PeelUniforms = {
    uPrevDepth: { value: null },
    uOpaqueDepth: { value: null },
    uPeelLayer: { value: 0 },
    uResolution: { value: new THREE.Vector2(1, 1) },
  }
  private size = new THREE.Vector2(1, 1)
  private pmrem: THREE.PMREMGenerator
  private envTex: THREE.Texture
  selected = -1
  hovered = -1
  isolated = -1
  aoEnabled = true

  constructor(renderer: THREE.WebGLRenderer, camera: THREE.PerspectiveCamera, quality: EngineQuality) {
    this.renderer = renderer
    this.camera = camera
    this.quality = quality
    renderer.localClippingEnabled = true
    renderer.autoClear = false

    this.pmrem = new THREE.PMREMGenerator(renderer)
    this.envTex = this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    for (const sc of [this.opaqueScene, this.transScene]) {
      sc.environment = this.envTex
      sc.environmentIntensity = 0.75
      const key = new THREE.DirectionalLight(0xffffff, 1.6)
      sc.add(key)
      sc.add(key.target)
      this.keyLights.push(key)
      sc.add(new THREE.HemisphereLight(0xf4f1ea, 0x5b544c, 0.35))
    }

    this.ctPlane = new THREE.Mesh(this.capGeom, ctPlaneMaterial(this.ct))
    this.ctPlane.matrixAutoUpdate = false
    this.ctPlane.frustumCulled = false

    this.compositeMat = new THREE.ShaderMaterial({
      uniforms: { tLayer: { value: null } },
      vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: `uniform sampler2D tLayer; varying vec2 vUv; void main(){ gl_FragColor = texture2D(tLayer, vUv); }`,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      blending: THREE.CustomBlending,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneMinusSrcAlphaFactor,
      blendSrcAlpha: THREE.OneFactor,
      blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
    })
    this.outputMat = new THREE.ShaderMaterial({
      uniforms: {
        tColor: { value: null },
        uTop: { value: this.background[0] },
        uBottom: { value: this.background[1] },
        uExposure: { value: 1.0 },
      },
      vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: `
uniform sampler2D tColor; uniform vec3 uTop; uniform vec3 uBottom; uniform float uExposure;
varying vec2 vUv;
vec3 aces(vec3 x){ x *= uExposure * 0.85; return clamp((x*(2.51*x+0.03))/(x*(2.43*x+0.59)+0.14), 0.0, 1.0); }
vec3 toSRGB(vec3 c){ return mix(c * 12.92, 1.055 * pow(c, vec3(1.0/2.4)) - 0.055, step(0.0031308, c)); }
void main(){
  vec4 c = texture2D(tColor, vUv);
  vec3 bg = mix(uBottom, uTop, smoothstep(0.0, 1.0, vUv.y));
  vec3 fg = toSRGB(aces(c.rgb / max(c.a, 1e-4)));
  gl_FragColor = vec4(mix(bg, fg, clamp(c.a, 0.0, 1.0)), 1.0);
}`,
      depthTest: false,
      depthWrite: false,
    })
    this.allocate(1, 1)
  }

  // ------------------------------------------------------------ resources
  private allocate(w: number, h: number) {
    this.rtMain?.dispose()
    this.rtPeel.forEach((r) => r.dispose())
    const depth = new THREE.DepthTexture(w, h)
    depth.format = THREE.DepthStencilFormat
    depth.type = THREE.UnsignedInt248Type
    this.rtMain = new THREE.WebGLRenderTarget(w, h, {
      type: THREE.HalfFloatType,
      samples: this.quality.samples,
      stencilBuffer: true,
      depthTexture: depth,
    })
    this.rtPeel = []
    for (let i = 0; i < this.quality.peelLayers; i++) {
      const d = new THREE.DepthTexture(w, h)
      d.type = THREE.UnsignedIntType
      this.rtPeel.push(new THREE.WebGLRenderTarget(w, h, { type: THREE.HalfFloatType, depthTexture: d }))
    }
    if (!this.gtao) {
      this.gtao = new GTAOPass(this.opaqueScene, this.camera, w, h, { depthTexture: depth })
      this.gtao.output = GTAOPass.OUTPUT.Off
      this.gtao.blendIntensity = 1
      this.gtao.updateGtaoMaterial({ radius: 0.012, distanceExponent: 1.4, thickness: 1.2, scale: 1.1, samples: 12 })
      this.gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 12 })
    } else {
      this.gtao.setGBuffer(depth)
    }
    const s = this.quality.aoScale
    this.gtao.setSize(Math.max(1, Math.round(w * s)), Math.max(1, Math.round(h * s)))
    this.peelU.uOpaqueDepth.value = depth
    this.peelU.uResolution.value.set(w, h)
  }

  setSize(w: number, h: number) {
    if (w === this.size.x && h === this.size.y) return
    this.size.set(w, h)
    this.allocate(w, h)
  }

  setTheme(dark: boolean) {
    if (dark) {
      this.background[0].set('#20242b')
      this.background[1].set('#0f1115')
    } else {
      this.background[0].set('#f3f4f6')
      this.background[1].set('#d6dae0')
    }
  }

  // ------------------------------------------------------------ content
  setStructures(root: THREE.Object3D, structures: Structure[]) {
    root.updateMatrixWorld(true)
    const byName = new Map<string, THREE.Mesh>()
    root.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) {
        const m = o as THREE.Mesh
        // glTF names may be on the mesh or its parent node
        const key = [m.name, m.parent?.name, (m.geometry as THREE.BufferGeometry).name].find(
          (k) => k && structures.some((s) => s.id === k),
        )
        if (key && !byName.has(key)) byName.set(key, m)
      }
    })
    this.items = []
    this.bounds.makeEmpty()
    structures.forEach((s, index) => {
      const src = byName.get(s.id)
      if (!src) return
      const geom = src.geometry as THREE.BufferGeometry
      if (!geom.attributes.normal) geom.computeVertexNormals()
      geom.computeBoundingBox()
      geom.computeBoundingSphere()
      const surface = surfaceMaterial(s.material, s.rgb)
      surface.clippingPlanes = this.planes
      const peel = surface.clone()
      peel.clippingPlanes = this.planes
      makePeelable(peel, this.peelU)
      const mesh = new THREE.Mesh(geom, surface)
      mesh.matrixAutoUpdate = false
      mesh.matrix.copy(src.matrixWorld)
      mesh.matrixWorld.copy(src.matrixWorld)
      mesh.userData.index = index
      const back = new THREE.Mesh(geom, stencilMaterial(THREE.BackSide, this.planes))
      const front = new THREE.Mesh(geom, stencilMaterial(THREE.FrontSide, this.planes))
      const pick = pickMaterial(index, this.planes)
      const pickMesh = new THREE.Mesh(geom, pick)
      for (const m of [back, front, pickMesh]) {
        m.matrixAutoUpdate = false
        m.matrix.copy(mesh.matrix)
        m.matrixWorld.copy(mesh.matrixWorld)
      }
      const cap = new THREE.Mesh(this.capGeom, capMaterial(s.rgb, this.ct))
      cap.matrixAutoUpdate = false
      cap.frustumCulled = false
      const box = geom.boundingBox!.clone().applyMatrix4(mesh.matrixWorld)
      this.bounds.union(box)
      this.items.push({
        s,
        index,
        mesh,
        surface,
        peel,
        pick,
        pickMesh,
        back,
        front,
        cap,
        box,
        visible: true,
        opacity: defaultOpacity(s.material, s.id),
      })
    })
    this.rebuild()
  }

  setVolume(v: VolumeResult, lpsToGltf: number[][]) {
    const tex = new THREE.Data3DTexture(v.half, v.dims[0], v.dims[1], v.dims[2])
    tex.format = THREE.RedFormat
    tex.type = THREE.HalfFloatType
    tex.minFilter = THREE.LinearFilter
    tex.magFilter = THREE.LinearFilter
    tex.unpackAlignment = 1
    tex.needsUpdate = true
    this.ct.uVolume.value?.dispose()
    this.ct.uVolume.value = tex
    // world (glTF m) -> LPS mm -> RAS mm -> voxel -> texture coordinate
    const L = new THREE.Matrix4().set(...(lpsToGltf.flat() as [
      number, number, number, number, number, number, number, number,
      number, number, number, number, number, number, number, number,
    ]))
    const rasToLps = new THREE.Matrix4().makeScale(-1, -1, 1)
    const A = new THREE.Matrix4().set(...(v.affine.flat() as [
      number, number, number, number, number, number, number, number,
      number, number, number, number, number, number, number, number,
    ]))
    const voxToWorld = L.clone().multiply(rasToLps).multiply(A)
    const worldToVox = voxToWorld.clone().invert()
    const voxToTex = new THREE.Matrix4()
      .makeScale(1 / v.dims[0], 1 / v.dims[1], 1 / v.dims[2])
      .multiply(new THREE.Matrix4().makeTranslation(0.5, 0.5, 0.5))
    this.ct.uWorldToTex.value.copy(voxToTex.multiply(worldToVox))
    this.ct.uHasVolume.value = 1
  }

  setWindow(centre: number, width: number) {
    this.ct.uWindow.value.set(centre, width)
  }

  setCTOpacity(a: number) {
    this.ctOpacity = a
    const m = this.ctPlane.material as THREE.ShaderMaterial
    m.uniforms.uOpacity.value = a
    // a mostly see-through slice should not hide what lies behind it
    m.depthWrite = a >= 0.6
  }

  /**
   * Compile every shader variant before the first frame, using
   * KHR_parallel_shader_compile where available, so the main thread does not
   * stall on program links.
   */
  async warmUp(): Promise<void> {
    const r = this.renderer
    const tiny = new THREE.BufferGeometry()
    tiny.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1e-6, 0, 0, 0, 1e-6, 0], 3))
    tiny.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1], 3))
    const seen = new Set<string>()
    const opaque: THREE.Mesh[] = []
    const trans: THREE.Mesh[] = []
    for (const it of this.items) {
      if (seen.has(it.s.material)) continue
      seen.add(it.s.material)
      opaque.push(new THREE.Mesh(tiny, it.surface))
      trans.push(new THREE.Mesh(tiny, it.peel))
    }
    const first = this.items[0]
    if (first) opaque.push(new THREE.Mesh(tiny, first.back.material), new THREE.Mesh(tiny, first.front.material), new THREE.Mesh(tiny, first.cap.material))
    opaque.push(new THREE.Mesh(tiny, this.ctPlane.material))
    for (const m of [...opaque, ...trans]) m.frustumCulled = false
    this.opaqueScene.add(...opaque)
    this.transScene.add(...trans)
    if (first) this.pickScene.add(new THREE.Mesh(tiny, first.pick))
    try {
      await Promise.all([
        r.compileAsync(this.opaqueScene, this.camera),
        r.compileAsync(this.transScene, this.camera),
        r.compileAsync(this.pickScene, this.camera),
      ])
    } finally {
      this.opaqueScene.remove(...opaque)
      this.transScene.remove(...trans)
      this.pickScene.clear()
      this.rebuild()
    }
  }

  // ------------------------------------------------------------ state
  setVisible(index: number, visible: boolean) {
    const it = this.items.find((i) => i.index === index)
    if (it) it.visible = visible
    this.rebuild()
  }

  setOpacity(index: number, opacity: number) {
    const it = this.items.find((i) => i.index === index)
    if (it) it.opacity = opacity
    this.rebuild()
  }

  setClip(region: ClipRegion | null) {
    this.clip = region
    if (!region) {
      for (const p of this.planes) p.set(new THREE.Vector3(1, 0, 0), FAR)
    } else {
      const u = new THREE.Vector3(), v = new THREE.Vector3(), w = new THREE.Vector3()
      region.basis.extractBasis(u, v, w)
      const axes = [u, v, w]
      const mn = region.min.toArray(), mx = region.max.toArray()
      axes.forEach((a, k) => {
        // keep p·a >= min  and  p·a <= max
        this.planes[2 * k].set(a.clone(), -mn[k])
        this.planes[2 * k + 1].set(a.clone().negate(), mx[k])
      })
      const c = region.min.clone().add(region.max).multiplyScalar(0.5)
      const sz = region.max.clone().sub(region.min)
      const t = new THREE.Vector3().addScaledVector(u, c.x).addScaledVector(v, c.y).addScaledVector(w, c.z)
      this.capBoxMatrix.copy(region.basis).setPosition(t).multiply(new THREE.Matrix4().makeScale(sz.x, sz.y, sz.z))
      this.ctPlane.matrix.copy(this.capBoxMatrix)
      this.ctPlane.matrixWorld.copy(this.capBoxMatrix)
    }
    this.rebuild()
  }

  /** A structure is cut when its box straddles the region boundary. */
  private isCut(it: Item): boolean {
    if (!this.clip) return false
    const corners: THREE.Vector3[] = []
    const b = it.box
    for (let i = 0; i < 8; i++)
      corners.push(new THREE.Vector3(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z))
    let anyOut = false
    for (const p of this.planes) {
      let neg = 0
      for (const c of corners) if (p.distanceToPoint(c) < 0) neg++
      if (neg === 8) return false // fully clipped
      if (neg > 0) anyOut = true
    }
    return anyOut
  }

  effectiveOpacity(it: Item): number {
    if (this.isolated >= 0 && it.index !== this.isolated) return Math.min(it.opacity, 0.07)
    return it.opacity
  }

  rebuild() {
    for (const sc of [this.opaqueScene, this.transScene]) {
      for (const c of [...sc.children]) if (!(c as THREE.Light).isLight && !(c as THREE.Object3D).userData.keep) sc.remove(c)
    }
    this.pickScene.clear()
    // note: every mesh keeps its own .matrix (scene updates recompute matrixWorld from it)
    let order = 1
    for (const it of this.items) {
      if (!it.visible) continue
      const op = this.effectiveOpacity(it)
      if (op <= 0.001) continue
      const highlight = it.index === this.selected || it.index === this.hovered
      for (const m of [it.surface, it.peel]) {
        m.emissive.setRGB(highlight ? 0.08 : 0, highlight ? 0.07 : 0, highlight ? 0.04 : 0)
      }
      if (op >= 0.999) {
        it.mesh.material = it.surface
        it.mesh.renderOrder = 0
        this.opaqueScene.add(it.mesh)
      } else {
        it.peel.opacity = op
        it.peel.transparent = false
        it.mesh.material = it.peel
        this.transScene.add(it.mesh)
      }
      if (op > 0.05) this.pickScene.add(it.pickMesh)
      if (this.clip && op > 0.05 && this.isCut(it)) {
        it.back.renderOrder = 1000 + order++
        it.front.renderOrder = 1000 + order++
        it.cap.renderOrder = 1000 + order++
        it.cap.matrix.copy(this.capBoxMatrix)
        it.cap.matrixWorld.copy(this.capBoxMatrix)
        this.opaqueScene.add(it.back, it.front, it.cap)
      }
    }
  }

  // ------------------------------------------------------------ frame
  render() {
    const r = this.renderer
    const cam = this.camera
    cam.updateMatrixWorld()
    // headlight from upper left of the viewer
    for (const k of this.keyLights) {
      k.position.copy(cam.position).add(new THREE.Vector3(-0.6, 0.8, 0.2).applyQuaternion(cam.quaternion).multiplyScalar(2))
      k.target.position.copy(this.bounds.getCenter(new THREE.Vector3()))
      k.target.updateMatrixWorld()
    }

    // 1. opaque + caps
    r.setRenderTarget(this.rtMain)
    r.setClearColor(0x000000, 0)
    r.clear(true, true, true)
    r.render(this.opaqueScene, cam)

    // 2. ambient occlusion
    if (this.aoEnabled) {
      this.gtao.render(r, null as unknown as THREE.WebGLRenderTarget, this.rtMain, 0, false)
      this.quad.material = this.gtao.blendMaterial
      this.gtao.blendMaterial.uniforms.intensity.value = 1
      this.gtao.blendMaterial.uniforms.tDiffuse.value = this.gtao.pdRenderTarget.texture
      r.setRenderTarget(this.rtMain)
      this.quad.render(r)
    }

    // 3. CT slice on the cut faces
    if (this.clip && this.ct.uHasVolume.value && this.ctOpacity > 0.01) {
      r.setRenderTarget(this.rtMain)
      r.render(this.ctPlane, cam)
    }

    // 4. depth-peeled translucency
    const nTrans = this.transScene.children.filter((c) => (c as THREE.Mesh).isMesh).length
    if (nTrans > 0) {
      const layers = this.rtPeel.length
      for (let i = 0; i < layers; i++) {
        this.peelU.uPeelLayer.value = i
        this.peelU.uPrevDepth.value = i > 0 ? this.rtPeel[i - 1].depthTexture : null
        r.setRenderTarget(this.rtPeel[i])
        r.setClearColor(0x000000, 0)
        r.clear(true, true, false)
        r.render(this.transScene, cam)
      }
      r.setRenderTarget(this.rtMain)
      this.quad.material = this.compositeMat
      for (let i = layers - 1; i >= 0; i--) {
        this.compositeMat.uniforms.tLayer.value = this.rtPeel[i].texture
        this.quad.render(r)
      }
    }

    // 5. to screen
    r.setRenderTarget(null)
    this.outputMat.uniforms.tColor.value = this.rtMain.texture
    this.quad.material = this.outputMat
    this.quad.render(r)
  }

  /** GPU pick: index of the structure under a canvas pixel, or -1. */
  pick(x: number, y: number): number {
    const r = this.renderer
    const cam = this.camera
    const w = this.size.x, h = this.size.y
    cam.setViewOffset(w, h, Math.round(x), Math.round(y), 1, 1)
    r.setRenderTarget(this.rtPick)
    r.setClearColor(0x000000, 0)
    r.clear(true, true, true)
    r.render(this.pickScene, cam)
    cam.clearViewOffset()
    const px = new Uint8Array(4)
    r.readRenderTargetPixels(this.rtPick, 0, 0, 1, 1, px)
    r.setRenderTarget(null)
    const id = px[0] + (px[1] << 8)
    return id > 0 ? id - 1 : -1
  }

  dispose() {
    this.rtMain.dispose()
    this.rtPeel.forEach((p) => p.dispose())
    this.rtPick.dispose()
    this.gtao.dispose()
    this.envTex.dispose()
    this.pmrem.dispose()
    this.ct.uVolume.value?.dispose()
    for (const it of this.items) {
      it.surface.dispose()
      it.peel.dispose()
      it.pick.dispose()
      ;(it.cap.material as THREE.Material).dispose()
      it.mesh.geometry.dispose()
    }
  }
}
