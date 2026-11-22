import * as THREE from 'three'

// Patient directions in glTF scene axes (from LPS): +X left, +Y superior, +Z anterior.
const DIRS: [string, THREE.Vector3][] = [
  ['L', new THREE.Vector3(1, 0, 0)],
  ['R', new THREE.Vector3(-1, 0, 0)],
  ['S', new THREE.Vector3(0, 1, 0)],
  ['I', new THREE.Vector3(0, -1, 0)],
  ['A', new THREE.Vector3(0, 0, 1)],
  ['P', new THREE.Vector3(0, 0, -1)],
]

/** Radiology-style edge label for a screen direction, e.g. "L" or "LA". */
export function labelFor(screenDirWorld: THREE.Vector3): string {
  const scored = DIRS.map(([k, d]) => [k, d.dot(screenDirWorld)] as const)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
  if (scored.length === 0) return ''
  const [first, second] = scored
  return second && second[1] > 0.38 ? first[0] + second[0] : first[0]
}

/** Labels for the right, left, top and bottom edges of a view. */
export function edgeLabels(cameraQuaternion: THREE.Quaternion) {
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(cameraQuaternion)
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(cameraQuaternion)
  return {
    right: labelFor(right),
    left: labelFor(right.clone().negate()),
    top: labelFor(up),
    bottom: labelFor(up.clone().negate()),
  }
}

/** Write labels into an overlay element that has [data-edge] children. */
export function writeEdgeLabels(el: HTMLElement | null, q: THREE.Quaternion) {
  if (!el) return
  const l = edgeLabels(q)
  for (const child of el.querySelectorAll<HTMLElement>('[data-edge]')) {
    const v = l[child.dataset.edge as keyof typeof l] ?? ''
    if (child.textContent !== v) child.textContent = v
  }
}
