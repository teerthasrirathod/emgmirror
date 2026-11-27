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
  /** Minimum distance from any mask voxel center to a CT volume boundary, in mm. */
  minimum_scan_edge_distance_mm?: number | null
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
  /** Source-reference coverage, distinct from model processing status and mesh QC. */
  source_status?: 'complete' | 'partial' | 'unknown'
  source_status_reason?: string
}

/** De-identified study identifiers (proposed S2 key `study`). */
export interface StudyInfo {
  study_date?: string | null
  series_description?: string | null
  /** hash of the source CT (hex), shown shortened */
  study_hash?: string | null
}

/** Sanitized execution summary. It reports work performed, not complete anatomy coverage. */
export interface ProcessingSummary {
  profile?: string
  detected_regions?: Record<string, number>
  planned_regions?: Record<string, number>
  planned_models?: string[]
  model_runs?: Array<{ source: string; status: string; seconds?: number }>
  anatomy_coverage?: Array<{
    anatomy_id: string
    label_id: number
    name: string
    laterality: 'left' | 'right' | 'not_applicable'
    status: 'segmented' | 'partial' | 'not_scheduled' | 'unavailable' | 'failed' | 'not_detected'
    capable_models: string[]
    scheduled_models: string[]
    completed_models: string[]
    failed_models: string[]
    unavailable_models?: string[]
    field_of_view_status?: 'not_assessed'
  }>
  geometry?: Record<string, unknown>
  warnings?: string[]
}

export type AnalysisStatus = 'not_scheduled' | 'unavailable' | 'failed' | 'partial' | 'complete'

export interface AnalysisReference {
  file: string
}

export interface AnalysisFinding {
  finding_id: string
  finding_type: string
  anatomy_id?: string
  region_id: string
  assertion: 'present' | 'absent' | 'uncertain' | 'not_assessed'
  summary: string
  source: { kind: 'model' | 'human'; id: string; version?: string; task_id?: string }
  review_status: 'unreviewed' | 'reviewed' | 'disagreement' | 'adjudicated'
  review_coverage: 'complete' | 'partial' | 'unknown'
  evidence: {
    volume_id: 'ct'
    coordinate_frame: 'LPS-mm'
    point_xyz_mm: [number, number, number]
    extent_xyz_mm?: [number, number, number]
  }
}

export interface AnalysisResult {
  version: 1
  status: AnalysisStatus
  runs: Array<{
    task_id: string
    model_id?: string
    model_version?: string
    status: AnalysisStatus
    regions?: string[]
    warnings?: string[]
  }>
  findings: AnalysisFinding[]
  warnings?: string[]
}

export interface Manifest {
  version: number
  units: string
  axes: string
  lps_to_gltf: number[][]
  structures: StructureEntry[]
  combined: { web: string; print?: string }
  volumes?: { ct?: string; labels?: string; ct_preview?: string; labels_preview?: string; model_input?: string; labels_model_grid?: string }
  source?: Record<string, string>
  study?: StudyInfo
  /** Intake adapter and source-to-canonical geometry record, when produced by upload processing. */
  intake?: Record<string, unknown>
  /** Planner and per-model results retained from the segmentation pipeline. */
  processing?: ProcessingSummary
  /** Optional region-aware finding analysis sidecar; independent of segmentation. */
  analysis?: AnalysisReference
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
  sourceStatus?: 'complete' | 'partial' | 'unknown'
  sourceStatusReason?: string
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
  if (/^(body_|skin|subcutaneous_fat|torso_fat)/.test(n)) return { system: 'other', material: 'organ' }
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
    // S2 mesher output: id = label value (number), name = code (also the GLB node name), display = readable name
    const raw = s as unknown as { id: string | number; name?: string; display?: unknown; label?: number }
    const numeric = typeof raw.id === 'number'
    const code = numeric ? String(raw.name ?? raw.id) : String(raw.id)
    const readable = numeric ? (typeof raw.display === 'string' ? raw.display : code) : s.name || code
    const { system, material } = kindFromDisplay(numeric ? null : s.display, code)
    return {
      id: code,
      name: readable,
      label: typeof raw.label === 'number' ? raw.label : numeric ? (raw.id as number) : i + 1,
      system,
      material,
      rgb: s.rgb,
      files: s.files ?? {},
      volumeMl: s.volume_ml ?? null,
      areaCm2: s.surface_area_cm2 ?? null,
      qc: s.qc ?? {},
      sourceStatus: s.source_status,
      sourceStatusReason: s.source_status_reason,
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

export async function loadAnalysis(base: string, file: string): Promise<AnalysisResult> {
  const segments = file.replace(/\\/g, '/').split('/')
  if (!file || file.startsWith('/') || file.includes(':') || segments.some((part) => !part || part === '.' || part === '..')) {
    throw new Error('The analysis result path is invalid.')
  }
  const r = await fetch(join(base, file))
  if (!r.ok) throw new Error(`Could not read the analysis result (${file}: HTTP ${r.status}).`)
  const value: unknown = await r.json()
  if (!value || typeof value !== 'object') throw new Error('The analysis result is not an object.')
  const doc = value as Partial<AnalysisResult>
  const statuses: AnalysisStatus[] = ['not_scheduled', 'unavailable', 'failed', 'partial', 'complete']
  if (doc.version !== 1 || !statuses.includes(doc.status as AnalysisStatus) || !Array.isArray(doc.runs) || !Array.isArray(doc.findings)) {
    throw new Error('The analysis result does not match the supported contract.')
  }
  const validFindings = doc.findings.every((f) =>
    !!f && typeof f.finding_id === 'string' && typeof f.finding_type === 'string' &&
    typeof f.region_id === 'string' && typeof f.summary === 'string' &&
    ['present', 'absent', 'uncertain', 'not_assessed'].includes(f.assertion) &&
    !!f.source && ['model', 'human'].includes(f.source.kind) && typeof f.source.id === 'string' &&
    ['unreviewed', 'reviewed', 'disagreement', 'adjudicated'].includes(f.review_status) &&
    ['complete', 'partial', 'unknown'].includes(f.review_coverage) &&
    (f.assertion !== 'absent' || (f.source.kind === 'human' && f.review_status !== 'unreviewed' && f.review_status !== 'disagreement' && f.review_coverage === 'complete')) &&
    !!f.evidence && f.evidence.volume_id === 'ct' && f.evidence.coordinate_frame === 'LPS-mm' &&
    Array.isArray(f.evidence.point_xyz_mm) && f.evidence.point_xyz_mm.length === 3 &&
    f.evidence.point_xyz_mm.every(Number.isFinite),
  )
  const validRuns = doc.runs.every((run) =>
    !!run && typeof run.task_id === 'string' && statuses.includes(run.status) &&
    (run.model_id === undefined || typeof run.model_id === 'string') &&
    (run.regions === undefined || (Array.isArray(run.regions) && run.regions.every((region) => typeof region === 'string'))),
  )
  const validWarnings = doc.warnings === undefined || (Array.isArray(doc.warnings) && doc.warnings.every((warning) => typeof warning === 'string'))
  if (!validFindings || !validRuns || !validWarnings) throw new Error('The analysis result contains invalid run, finding, or evidence data.')
  return doc as AnalysisResult
}

export function join(base: string, path: string): string {
  if (/^(https?:)?\/\//.test(path) || path.startsWith('/')) return path
  return base.replace(/\/?$/, '/') + path
}

/** Short QC notes shown as badges. Plain language; no clinical meaning. */
export function qcBadges(qc: QC): { text: string; tone: 'warn' | 'info' }[] {
  const out: { text: string; tone: 'warn' | 'info' }[] = []
  if (qc.cut_by_scan_edge) out.push({ text: 'Cut by scan edge', tone: 'warn' })
  else if (typeof qc.minimum_scan_edge_distance_mm === 'number' && qc.minimum_scan_edge_distance_mm <= 5) {
    out.push({ text: `Near scan edge (${qc.minimum_scan_edge_distance_mm.toFixed(1)} mm)`, tone: 'warn' })
  }
  if (qc.watertight === false) out.push({ text: 'Surface has gaps', tone: 'warn' })
  if (qc.self_intersections) out.push({ text: 'Self-intersecting surface', tone: 'warn' })
  if (typeof qc.hd95_mm === 'number' && qc.hd95_mm > 2) out.push({ text: `Shape deviates up to ${qc.hd95_mm.toFixed(1)} mm`, tone: 'info' })
  return out
}
