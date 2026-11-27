import type { AnalysisResult } from '../lib/manifest'

interface Props {
  analysis?: AnalysisResult | null
  error?: string | null
  onOpenEvidence: (pointLpsMm: [number, number, number]) => void
}

const STATUS_LABEL: Record<AnalysisResult['status'], string> = {
  not_scheduled: 'Not scheduled',
  unavailable: 'Unavailable',
  failed: 'Failed',
  partial: 'Partial',
  complete: 'Complete',
}

function assertionLabel(assertion: string, source: AnalysisResult['findings'][number]['source']['kind']): string {
  if (assertion === 'present' && source === 'model') return 'Model suggestion'
  if (assertion === 'present' && source === 'human') return 'Human-entered observation'
  return ({ present: 'Present', absent: 'Reviewed absent', uncertain: 'Uncertain', not_assessed: 'Not assessed' } as Record<string, string>)[assertion] ?? 'Unknown'
}

export default function FindingsPanel({ analysis, error, onOpenEvidence }: Props) {
  if (!analysis && !error) return null
  return (
    <section className="findings-panel" aria-labelledby="findings-title">
      <h2 id="findings-title">CT findings review</h2>
      <p className="processing-note">Finding analysis is separate from segmentation. Model suggestions are not diagnoses; an omitted finding is not a negative result.</p>
      {error ? <p className="findings-error" role="status">{error}</p> : analysis ? (
        <>
          <p className="findings-status">Analysis: <strong>{STATUS_LABEL[analysis.status]}</strong></p>
          {analysis.runs.length > 0 && (
            <ul className="findings-runs">
              {analysis.runs.map((run, index) => (
                <li key={`${run.task_id}-${index}`}>
                  {run.model_id ? `${run.model_id} · ` : ''}{run.task_id}: {STATUS_LABEL[run.status]}
                </li>
              ))}
            </ul>
          )}
          {analysis.findings.length > 0 ? (
            <ul className="findings-list">
              {analysis.findings.map((finding) => (
                <li key={finding.finding_id}>
                  <strong>{finding.finding_type.replace(/_/g, ' ')}</strong>
                  <span>{assertionLabel(finding.assertion, finding.source.kind)} · {finding.review_status.replace(/_/g, ' ')} · review {finding.review_coverage}</span>
                  <p>{finding.summary}</p>
                  <small>{finding.region_id.replace(/_/g, ' ')}{finding.anatomy_id ? ` · ${finding.anatomy_id.replace(/_/g, ' ')}` : ''} · {finding.source.kind === 'model' ? `${finding.source.id}${finding.source.version ? ` ${finding.source.version}` : ''}` : finding.source.id}</small>
                  <button type="button" onClick={() => onOpenEvidence(finding.evidence.point_xyz_mm)}>
                    Open CT evidence
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="processing-note">No structured findings were recorded. This does not indicate a negative scan.</p>}
          {!!analysis.warnings?.length && (
            <details>
              <summary>Analysis warnings ({analysis.warnings.length})</summary>
              <ul>{analysis.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
            </details>
          )}
        </>
      ) : null}
    </section>
  )
}
