// Result-folder contract (S2 manifest version 1) and helpers.

export type System = 'skeleton' | 'organs' | 'vessels' | 'muscles' | 'other'
export type MaterialKind = 'bone' | 'artery' | 'vein' | 'organ' | 'muscle'

export interface QC {
  watertight?: boolean | null
  self_intersections?: boolean | number | null
  hd95_mm?: number | null
  mean_dist_mm?: number | null
  volume_change_pct?: number | null
  cut_by_scan_edge?: boolean | null
}

export interface StructureEntry {
  id: string
  name: string
  label?: number
  display?: unknown
  rgb: [number, number, number]
  files: { glb?: string; stl?: string; '3mf'?: string }
  volume_ml?: number | null
  surface_area_cm2?: number | null
  qc?: QC
}

/** De-identified study identifiers (proposed S2 key `study`). */
export interface StudyInfo {
  study_date?: string | null
  series_description?: string | null
  /** hash of the source CT (hex), shown shortened */
  study_hash?: string | null
}

export interface Manifest {
  version: number
  units: string
  axes: string
  lps_to_gltf: number[][]
  structures: StructureEntry[]
  combined: { web: string; print?: string }
  volumes?: { ct?: string; labels?: string; ct_preview?: string }
  source?: Record<string, string>
  study?: StudyInfo
}

/** One structure as the viewer uses it. */
export interface Structure {
  id: string
  name: string
  label: number
  system: System
  material: MaterialKind
  rgb: [number, number, number]
  files: StructureEntry['files']
  volumeMl: number | null
  areaCm2: number | null
  qc: QC
}

export const SYSTEM_ORDER: System[] = ['skeleton', 'organs', 'vessels', 'muscles', 'other']
export const SYSTEM_LABEL: Record<System, string> = {
  skeleton: 'Skeleton',
  organs: 'Organs',
  vessels: 'Blood vessels',
  muscles: 'Muscles',
  other: 'Other',
}

/** Infer system and material from a structure id when `display` does not say. */
export function inferKind(id: string): { system: System; material: MaterialKind } {
  const n = id.toLowerCase()
  if (/(vertebra|rib|sacrum|hip|femur|humerus|scapula|clavic|skull|sternum|costal|bone|tibia|fibula|radius|ulna|patella|pelvis|mandible|carpal|tarsal|phalan)/.test(n))
    return { system: 'skeleton', material: 'bone' }
  if (/(aorta|artery|arteries|trunk|carotid|subclavian)/.test(n)) return { system: 'vessels', material: 'artery' }
  if (/(vena|vein|portal|sinus)/.test(n)) return { system: 'vessels', material: 'vein' }
  if (/(muscle|gluteus|autochthon|iliopsoas|psoas|erector)/.test(n)) return { system: 'muscles', material: 'muscle' }
  return { system: 'organs', material: 'organ' }
}

function kindFromDisplay(display: unknown, id: string): { system: System; material: MaterialKind } {
  const inferred = inferKind(id)
  if (display && typeof display === 'object') {
    const d = display as Record<string, unknown>
    const system = SYSTEM_ORDER.includes(d.system as System) ? (d.system as System) : inferred.system
    const mats: MaterialKind[] = ['bone', 'artery', 'vein', 'organ', 'muscle']
    const material = mats.includes(d.material as MaterialKind) ? (d.material as MaterialKind) : inferred.material
    return { system, material }
  }
  if (typeof display === 'string') {
    const s = display.toLowerCase()
    for (const sys of SYSTEM_ORDER) if (s.includes(sys.replace(/s$/, ''))) return { ...inferred, system: sys }
  }
  return inferred
}

export function normalise(m: Manifest): Structure[] {
  return m.structures.map((s, i) => {
    const { system, material } = kindFromDisplay(s.display, s.id)
    return {
      id: s.id,
      name: s.name || s.id,
      label: typeof s.label === 'number' ? s.label : i + 1,
      system,
      material,
      rgb: s.rgb,
      files: s.files ?? {},
      volumeMl: s.volume_ml ?? null,
      areaCm2: s.surface_area_cm2 ?? null,
      qc: s.qc ?? {},
    }
  })
}

export async function loadManifest(base: string): Promise<Manifest> {
  const r = await fetch(join(base, 'manifest.json'))
  if (!r.ok) throw new Error(`Could not read the result (manifest.json: HTTP ${r.status}).`)
  const m = (await r.json()) as Manifest
  if (m.version !== 1) throw new Error(`This result uses format version ${m.version}; this viewer reads version 1.`)
  return m
}

export function join(base: string, path: string): string {
  if (/^(https?:)?\/\//.test(path) || path.startsWith('/')) return path
  return base.replace(/\/?$/, '/') + path
}

/** Short QC notes shown as badges. Plain language; no clinical meaning. */
export function qcBadges(qc: QC): { text: string; tone: 'warn' | 'info' }[] {
  const out: { text: string; tone: 'warn' | 'info' }[] = []
  if (qc.cut_by_scan_edge) out.push({ text: 'Cut by scan edge', tone: 'warn' })
  if (qc.watertight === false) out.push({ text: 'Surface has gaps', tone: 'warn' })
  if (qc.self_intersections) out.push({ text: 'Self-intersecting surface', tone: 'warn' })
  if (typeof qc.hd95_mm === 'number' && qc.hd95_mm > 2) out.push({ text: `Shape deviates up to ${qc.hd95_mm.toFixed(1)} mm`, tone: 'info' })
  return out
}
