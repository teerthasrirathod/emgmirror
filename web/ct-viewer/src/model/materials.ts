import * as THREE from 'three'
import type { MaterialKind } from '../lib/manifest'
export { defaultOpacity } from '../lib/appearance'

/** PBR surface material for one structure. */
export function surfaceMaterial(kind: MaterialKind, rgb: [number, number, number]): THREE.MeshPhysicalMaterial {
  const color = new THREE.Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, THREE.SRGBColorSpace)
  const m = new THREE.MeshPhysicalMaterial({ color, metalness: 0, side: THREE.FrontSide })
  switch (kind) {
    case 'bone':
      m.roughness = 0.62
      m.sheen = 0.25
      m.sheenRoughness = 0.8
      m.sheenColor = new THREE.Color(0xfff6e6)
      break
    case 'artery':
    case 'vein':
      m.roughness = 0.38
      m.clearcoat = 0.55
      m.clearcoatRoughness = 0.28
      break
    case 'muscle':
      m.roughness = 0.68
      m.sheen = 0.4
      m.sheenRoughness = 0.6
      m.sheenColor = color.clone().multiplyScalar(1.2)
      break
    default:
      m.roughness = 0.48
      m.clearcoat = 0.25
      m.clearcoatRoughness = 0.4
  }
  return m
}

/**
 * Patch a surface material for depth peeling: discard fragments at or in front
 * of the previous layer and behind the opaque scene, output premultiplied colour.
 */
export interface PeelUniforms {
  uPrevDepth: { value: THREE.Texture | null }
  uOpaqueDepth: { value: THREE.Texture | null }
  uPeelLayer: { value: number }
  uResolution: { value: THREE.Vector2 }
}

export function makePeelable(m: THREE.MeshPhysicalMaterial, u: PeelUniforms): void {
  m.transparent = false
  m.depthWrite = true
  m.blending = THREE.NoBlending
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u)
    shader.fragmentShader = shader.fragmentShader
      .replace(
        'void main() {',
        `uniform sampler2D uPrevDepth;
uniform sampler2D uOpaqueDepth;
uniform int uPeelLayer;
uniform vec2 uResolution;
void main() {
  vec2 st = gl_FragCoord.xy / uResolution;
  float zf = gl_FragCoord.z;
  if (zf >= texture2D(uOpaqueDepth, st).r) discard;
  if (uPeelLayer > 0 && zf <= texture2D(uPrevDepth, st).r + 1e-7) discard;`,
      )
      .replace(
        '#include <dithering_fragment>',
        `#include <dithering_fragment>
  gl_FragColor = vec4(gl_FragColor.rgb * gl_FragColor.a, gl_FragColor.a);`,
      )
  }
  m.customProgramCacheKey = () => 'peel'
}

/** Writes only stencil (back faces +1, front faces -1) for capping. */
export function stencilMaterial(side: THREE.Side, planes: THREE.Plane[]): THREE.MeshBasicMaterial {
  const m = new THREE.MeshBasicMaterial({
    side,
    clippingPlanes: planes,
    colorWrite: false,
    depthWrite: false,
    depthTest: false,
    stencilWrite: true,
    stencilFunc: THREE.AlwaysStencilFunc,
  })
  const op = side === THREE.BackSide ? THREE.IncrementWrapStencilOp : THREE.DecrementWrapStencilOp
  m.stencilFail = op
  m.stencilZFail = op
  m.stencilZPass = op
  return m
}

const CT_GLSL = `
uniform highp sampler3D uVolume;
uniform mat4 uWorldToTex;
uniform vec2 uWindow; // centre, width (HU)
uniform float uHasVolume;
float ctSample(vec3 worldPos, out float inside) {
  vec3 t = (uWorldToTex * vec4(worldPos, 1.0)).xyz;
  inside = step(0.0, t.x) * step(0.0, t.y) * step(0.0, t.z) * step(t.x, 1.0) * step(t.y, 1.0) * step(t.z, 1.0);
  float hu = texture(uVolume, t).r;
  return clamp((hu - (uWindow.x - 0.5 * uWindow.y)) / uWindow.y, 0.0, 1.0);
}
`

export interface CTUniforms {
  uVolume: { value: THREE.Data3DTexture | null }
  uWorldToTex: { value: THREE.Matrix4 }
  uWindow: { value: THREE.Vector2 }
  uHasVolume: { value: number }
}

export function makeCTUniforms(): CTUniforms {
  return {
    uVolume: { value: null },
    uWorldToTex: { value: new THREE.Matrix4() },
    uWindow: { value: new THREE.Vector2(40, 400) },
    uHasVolume: { value: 0 },
  }
}

const capVertex = `
varying vec3 vWorld;
void main() {
  vec4 w = modelMatrix * vec4(position, 1.0);
  vWorld = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}`

/** Solid cut face for one structure, tinted by the CT under it. */
export function capMaterial(rgb: [number, number, number], ct: CTUniforms): THREE.ShaderMaterial {
  const c = new THREE.Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, THREE.SRGBColorSpace)
  return new THREE.ShaderMaterial({
    uniforms: { ...ct, uColor: { value: c } },
    vertexShader: capVertex,
    fragmentShader: `${CT_GLSL}
uniform vec3 uColor;
varying vec3 vWorld;
void main() {
  float inside;
  float g = ctSample(vWorld, inside);
  float tex = mix(1.0, 0.55 + 0.75 * g, 0.6 * uHasVolume * inside);
  gl_FragColor = vec4(uColor * 0.82 * tex, 1.0);
}`,
    side: THREE.FrontSide,
    stencilWrite: true,
    stencilRef: 0,
    stencilFunc: THREE.NotEqualStencilFunc,
    stencilFail: THREE.ReplaceStencilOp,
    stencilZFail: THREE.ReplaceStencilOp,
    stencilZPass: THREE.ReplaceStencilOp,
  })
}

/** The CT slice on the cut faces, outside the structures. */
export function ctPlaneMaterial(ct: CTUniforms): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: { ...ct, uOpacity: { value: 0.92 } },
    vertexShader: capVertex,
    fragmentShader: `${CT_GLSL}
uniform float uOpacity;
varying vec3 vWorld;
void main() {
  float inside;
  float g = ctSample(vWorld, inside);
  if (inside < 0.5 || uHasVolume < 0.5) discard;
  // air outside the body stays see-through
  vec3 t = (uWorldToTex * vec4(vWorld, 1.0)).xyz;
  float hu = texture(uVolume, t).r;
  float cut = max(uWindow.x - 0.5 * uWindow.y, -950.0); // below the window: see-through
  float tissue = smoothstep(cut - 60.0, cut + 60.0, hu);
  if (tissue < 0.5) discard;
  float a = uOpacity * tissue;
  vec3 col = vec3(pow(g, 1.0 / 1.1));
  gl_FragColor = vec4(col * a, a);
}`,
    side: THREE.FrontSide,
    transparent: true,
    // writes depth so translucent structures behind the slice stay hidden
    depthWrite: true,
    blending: THREE.CustomBlending,
    blendSrc: THREE.OneFactor,
    blendDst: THREE.OneMinusSrcAlphaFactor,
    polygonOffset: true,
    polygonOffsetFactor: 2,
    polygonOffsetUnits: 4,
  })
}

/** Flat id colour for GPU picking (both sides, so a cut face picks its structure). */
export function pickMaterial(index: number, planes: THREE.Plane[]): THREE.ShaderMaterial {
  const id = index + 1
  const c = new THREE.Vector3((id & 255) / 255, ((id >> 8) & 255) / 255, 0)
  return new THREE.ShaderMaterial({
    uniforms: { uId: { value: c } },
    vertexShader: `#include <clipping_planes_pars_vertex>
void main() {
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;
  #include <clipping_planes_vertex>
}`,
    fragmentShader: `#include <clipping_planes_pars_fragment>
uniform vec3 uId;
void main() {
  #include <clipping_planes_fragment>
  gl_FragColor = vec4(uId, 1.0);
}`,
    side: THREE.DoubleSide,
    clipping: true,
    clippingPlanes: planes,
  })
}
