// Review, edit and approval records for one result.
//
// S3 groundwork: records live in this browser (localStorage) and can be
// exported as JSON. A device build needs authenticated users and a
// server-side, append-only audit trail (planned for the backend milestone).
import { sha256 } from '../lib/hash'

export type Edit =
  | { kind: 'exclude'; structure: string; reason: string }
  | { kind: 'erase'; structure: string; center: [number, number, number]; radiusMm: number }

export interface Reviewer {
  name: string
  role: string
}

export interface Approval {
  user: string
  role: string
  time: string
  modelHash: string
  editsHash: string
  versionHash: string
}

export interface AuditEvent {
  time: string
  user: string
  action: 'edit' | 'undo' | 'approve' | 'invalidate' | 'revoke'
  detail: string
  versionHash: string
}

export interface ReviewRecord {
  modelHash: string
  edits: Edit[]
  approval: Approval | null
  audit: AuditEvent[]
}

const key = (modelHash: string) => `ctv-review:${modelHash}`

export function loadRecord(modelHash: string): ReviewRecord {
  try {
    const raw = localStorage.getItem(key(modelHash))
    if (raw) {
      const r = JSON.parse(raw) as ReviewRecord
      if (r.modelHash === modelHash) return r
    }
  } catch {
    /* storage unavailable or corrupt: start empty */
  }
  return { modelHash, edits: [], approval: null, audit: [] }
}

export function saveRecord(r: ReviewRecord): boolean {
  try {
    localStorage.setItem(key(r.modelHash), JSON.stringify(r))
    return true
  } catch {
    return false
  }
}

/** Canonical form of the edits: content only, no times or names. */
export function canonicalEdits(edits: Edit[]): string {
  return JSON.stringify(
    edits.map((e) =>
      e.kind === 'exclude'
        ? ['exclude', e.structure]
        : ['erase', e.structure, e.center.map((v) => Math.round(v * 1e5) / 1e5), Math.round(e.radiusMm * 10) / 10],
    ),
  )
}

export async function versionOf(modelHash: string, edits: Edit[]) {
  const editsHash = await sha256(canonicalEdits(edits))
  const versionHash = await sha256(`${modelHash}:${editsHash}`)
  return { editsHash, versionHash }
}

/** An approval counts only for the exact version it was given to. */
export function isApproved(r: ReviewRecord, versionHash: string | null): boolean {
  return !!r.approval && !!versionHash && r.approval.versionHash === versionHash
}

export const REVIEWER_ROLES = [
  'Radiologist',
  'Orthopaedic or trauma surgeon',
  'Other clinician (qualified under the site procedure)',
]
