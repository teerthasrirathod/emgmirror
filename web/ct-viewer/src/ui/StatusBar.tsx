import { LABEL_STATUS } from '../lib/build'
import { short } from '../lib/hash'
import type { StudyInfo } from '../lib/manifest'
import type { Approval } from '../review/review'
import { COPY } from './copy'

interface Props {
  study: StudyInfo | null
  versionHash: string | null
  approval: Approval | null
  approved: boolean
  reference: boolean
  offline: { online: boolean; savedAt: string | null }
  onOpenReview?: () => void
}

export default function StatusBar(p: Props) {
  const missing = !p.study || !p.study.study_hash
  return (
    <div className="statusbar">
      <div className={`label-banner ${LABEL_STATUS}`} role="note">
        {COPY.label[LABEL_STATUS]}
      </div>
      <div className="idbar" aria-label={COPY.idLabel} data-testid="study-ids">
        {missing ? (
          <span className="id-missing" role="alert">
            {COPY.studyMissing}
          </span>
        ) : (
          <>
            <span>
              <b>{COPY.studyDate}</b> {p.study!.study_date || COPY.notRecorded}
            </span>
            <span>
              <b>{COPY.series}</b> {p.study!.series_description || COPY.notRecorded}
            </span>
            <span>
              <b>{COPY.studyId}</b> <code>{short(p.study!.study_hash)}</code>
            </span>
          </>
        )}
        <span>
          <b>{COPY.version}</b> <code data-testid="version-hash">{short(p.versionHash)}</code>
        </span>
        <button
          className={`approval ${p.approved ? 'ok' : 'no'}`}
          data-testid="approval-status"
          onClick={p.onOpenReview}
          disabled={!p.onOpenReview}
          aria-label={p.approved && p.approval ? COPY.approvedLong(p.approval) : COPY.notApprovedLong}
        >
          {p.approved && p.approval ? COPY.approvedShort(p.approval) : COPY.notApproved}
        </button>
        {(p.reference || p.offline.savedAt || !p.offline.online) && (
          <span className={`net ${p.offline.online ? '' : 'off'}`}>
            {p.offline.online ? COPY.online : COPY.offlineNow}
            {p.offline.savedAt ? ` · ${COPY.savedAt(p.offline.savedAt)}` : ''}
          </span>
        )}
      </div>
      {p.reference && !p.approved && (
        <div className="ref-warning" role="alert">
          {COPY.refNotApproved}
        </div>
      )}
    </div>
  )
}
