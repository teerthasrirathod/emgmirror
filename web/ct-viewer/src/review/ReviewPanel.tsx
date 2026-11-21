import { useEffect, useMemo, useRef, useState } from 'react'
import { short } from '../lib/hash'
import type { Structure } from '../lib/manifest'
import { COPY } from '../ui/copy'
import { REVIEWER_ROLES, type Edit, type ReviewRecord, type Reviewer } from './review'

interface Props {
  record: ReviewRecord
  structures: Structure[]
  versionHash: string | null
  approved: boolean
  reviewer: Reviewer | null
  onReviewer: (r: Reviewer | null) => void
  slicesSeen: boolean
  tool: 'view' | 'erase'
  onTool: (t: 'view' | 'erase') => void
  radiusMm: number
  onRadius: (r: number) => void
  onRemoveEdit: (i: number) => void
  onApprove: () => void
  onRevoke: () => void
  onClose: () => void
}

export default function ReviewPanel(p: Props) {
  const [name, setName] = useState(p.reviewer?.name ?? '')
  const [role, setRole] = useState(p.reviewer?.role ?? '')
  const [attest, setAttest] = useState(false)
  const firstField = useRef<HTMLInputElement>(null)
  useEffect(() => firstField.current?.focus(), [])
  // any change to the edits clears the attestation
  useEffect(() => setAttest(false), [p.versionHash])
  const byId = useMemo(() => new Map(p.structures.map((s) => [s.id, s.name])), [p.structures])
  const blockers = [
    !p.reviewer && COPY.needReviewer,
    !p.slicesSeen && COPY.needSlices,
    !attest && COPY.needAttest,
    !p.versionHash && COPY.needVersion,
  ].filter(Boolean) as string[]
  const exportUrl = useMemo(
    () => URL.createObjectURL(new Blob([JSON.stringify({ ...p.record, versionHash: p.versionHash }, null, 2)], { type: 'application/json' })),
    [p.record, p.versionHash],
  )
  useEffect(() => () => URL.revokeObjectURL(exportUrl), [exportUrl])

  const describe = (e: Edit) =>
    e.kind === 'exclude' ? COPY.editExclude(byId.get(e.structure) ?? e.structure) : COPY.editErase(byId.get(e.structure) ?? e.structure, e.radiusMm)

  return (
    <aside className="review-panel" aria-labelledby="review-title">
      <div className="rp-head">
        <h2 id="review-title">{COPY.reviewTitle}</h2>
        <button className="close" aria-label={COPY.close} onClick={p.onClose}>
          ×
        </button>
      </div>
      <p className="rp-intro">{COPY.reviewIntro}</p>

      <section>
        <h3>{COPY.stepReviewer}</h3>
        {p.reviewer ? (
          <p>
            {COPY.signedInAs(p.reviewer.name, p.reviewer.role)}{' '}
            <button className="link" onClick={() => p.onReviewer(null)}>
              {COPY.change}
            </button>
          </p>
        ) : (
          <form
            className="rp-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (name.trim() && role) p.onReviewer({ name: name.trim(), role })
            }}
          >
            <label>
              {COPY.yourName}
              <input ref={firstField} value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" />
            </label>
            <label>
              {COPY.yourRole}
              <select value={role} onChange={(e) => setRole(e.target.value)} required>
                <option value="">{COPY.choose}</option>
                {REVIEWER_ROLES.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </label>
            <button type="submit" className="primary">
              {COPY.startReview}
            </button>
            <p className="hint">{COPY.identityNote}</p>
          </form>
        )}
      </section>

      <section>
        <h3>{COPY.stepCompare}</h3>
        <p className={p.slicesSeen ? 'ok-text' : 'warn-text'}>{p.slicesSeen ? COPY.slicesSeen : COPY.slicesNotSeen}</p>
      </section>

      <section>
        <h3>{COPY.stepEdit}</h3>
        <div className="rp-tools">
          <button aria-pressed={p.tool === 'erase'} disabled={!p.reviewer} onClick={() => p.onTool(p.tool === 'erase' ? 'view' : 'erase')}>
            {COPY.eraseTool}
          </button>
          <label className="field range">
            <span>{COPY.eraseSize}</span>
            <input type="range" min={2} max={25} step={1} value={p.radiusMm} onChange={(e) => p.onRadius(parseFloat(e.target.value))} aria-valuetext={`${p.radiusMm} mm`} />
            <output>{p.radiusMm} mm</output>
          </label>
        </div>
        <p className="hint">{p.tool === 'erase' ? COPY.eraseHint : COPY.excludeHint}</p>
        {p.record.edits.length === 0 ? (
          <p className="hint">{COPY.noEdits}</p>
        ) : (
          <ol className="edits">
            {p.record.edits.map((e, i) => (
              <li key={i}>
                {describe(e)}{' '}
                <button className="link" onClick={() => p.onRemoveEdit(i)} disabled={!p.reviewer} aria-label={COPY.removeEdit(describe(e))}>
                  {COPY.undo}
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section>
        <h3>{COPY.stepApprove}</h3>
        {p.approved && p.record.approval ? (
          <>
            <p className="ok-text">{COPY.approvedLong(p.record.approval)}</p>
            <button onClick={p.onRevoke} disabled={!p.reviewer}>
              {COPY.revoke}
            </button>
          </>
        ) : (
          <>
            {p.record.approval && <p className="warn-text">{COPY.approvalInvalid(p.record.approval)}</p>}
            <label className="check">
              <input type="checkbox" checked={attest} onChange={(e) => setAttest(e.target.checked)} disabled={!p.reviewer} />
              {COPY.attest}
            </label>
            <button className="primary" disabled={blockers.length > 0} onClick={p.onApprove} aria-describedby="approve-blockers">
              {COPY.approve(short(p.versionHash))}
            </button>
            {blockers.length > 0 && (
              <ul id="approve-blockers" className="blockers">
                {blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <section>
        <h3>{COPY.stepRecord}</h3>
        <a href={exportUrl} download={`review-${short(p.record.modelHash)}.json`}>
          {COPY.exportRecord}
        </a>
        <details>
          <summary>{COPY.auditLog(p.record.audit.length)}</summary>
          <ol className="audit">
            {p.record.audit
              .slice()
              .reverse()
              .map((a, i) => (
                <li key={i}>
                  <time dateTime={a.time}>{new Date(a.time).toLocaleString()}</time> · {a.user} · {a.action}: {a.detail} · <code>{short(a.versionHash, 8)}</code>
                </li>
              ))}
          </ol>
        </details>
        <p className="hint">{COPY.storageNote}</p>
      </section>
    </aside>
  )
}
