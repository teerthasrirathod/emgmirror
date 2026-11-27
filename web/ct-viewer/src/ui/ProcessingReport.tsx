import type { ProcessingSummary } from '../lib/manifest'

interface Props {
  report?: ProcessingSummary
}

function label(value: string): string {
  return value.replace(/^totalsegmentator:/, '').replace(/^moose:/, 'MOOSE ').replace(/_/g, ' ')
}

export default function ProcessingReport({ report }: Props) {
  if (!report) return null

  const regions = Object.entries(report.detected_regions ?? {})
  const plannedRegions = Object.entries(report.planned_regions ?? {})
  const planned = (report.planned_models ?? []).filter(Boolean)
  const runs = report.model_runs ?? []
  const coverage = report.anatomy_coverage ?? []
  const warnings = report.warnings ?? []
  const statusLabels: Record<string, string> = {
    segmented: 'Segmented',
    partial: 'Partial',
    not_scheduled: 'Not scheduled',
    unavailable: 'Unavailable',
    failed: 'Failed',
    not_detected: 'Not detected',
  }
  const statusOrder = ['segmented', 'partial', 'not_scheduled', 'unavailable', 'failed', 'not_detected']
  const counts = statusOrder.map((status) => ({
    status,
    count: coverage.filter((item) => item.status === status).length,
  })).filter((item) => item.count > 0)

  return (
    <section className="processing-report" aria-labelledby="processing-report-title">
      <h2 id="processing-report-title">Processing report</h2>
      <p className="processing-note">This reports model activity. It does not establish complete anatomy coverage.</p>

      {report.profile && <p className="processing-note">Segmentation profile: <strong>{label(report.profile)}</strong></p>}

      <h3>Baseline marker summary</h3>
      {regions.length ? (
        <ul>
          {regions.map(([region, volumeMl]) => (
            <li key={region}>{label(region)} <span>{volumeMl.toFixed(1)} mL</span></li>
          ))}
        </ul>
      ) : <p className="processing-note">No marker volumes passed the summary threshold.</p>}

      <h3>Regions used by planner</h3>
      {plannedRegions.length ? (
        <ul>{plannedRegions.map(([region, volumeMl]) => <li key={region}>{label(region)} <span>{volumeMl.toFixed(2)} mL marker evidence</span></li>)}</ul>
      ) : <p className="processing-note">No positive region marker was returned by the baseline models.</p>}

      <h3>Planner selected</h3>
      {planned.length ? (
        <ul>{planned.map((model) => <li key={model}>{label(model)}</li>)}</ul>
      ) : <p className="processing-note">No region tasks were selected.</p>}

      <h3>Model runs</h3>
      {runs.length ? (
        <ul>
          {runs.map((run) => (
            <li key={run.source}>
              {label(run.source)} <span className={run.status === 'ok' ? 'run-ok' : 'run-failed'}>
                {run.status === 'ok' ? 'completed' : run.status === 'unavailable' ? 'unavailable' : 'failed'}
                {typeof run.seconds === 'number' ? ` · ${run.seconds.toFixed(1)} s` : ''}
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="processing-note">No model run details were recorded.</p>}

      <h3>Anatomy coverage</h3>
      {coverage.length ? (
        <>
          <ul className="coverage-counts">
            {counts.map(({ status, count }) => <li key={status}><strong>{statusLabels[status]}</strong><span>{count}</span></li>)}
          </ul>
          <details className="coverage-details">
            <summary>Show anatomy catalog states ({coverage.length})</summary>
            <ul>
              {coverage.map((item) => (
                <li key={item.anatomy_id}>
                  <span>
                    {item.name}{item.laterality !== 'not_applicable' ? `, ${item.laterality}` : ''}
                    <small className="coverage-models">{item.capable_models.length ? `Catalog models: ${item.capable_models.map(label).join(', ')}` : 'No allowed model is configured for this anatomy.'}</small>
                    {item.field_of_view_status === 'not_assessed' && <small className="coverage-models">Scan coverage: not assessed</small>}
                  </span>
                  <strong className={`coverage-${item.status}`}>{statusLabels[item.status]}</strong>
                </li>
              ))}
            </ul>
          </details>
          <p className="processing-note">Segmented means a model returned a label; partial means it touches the scan edge. Not scheduled means no capable model was run. Unavailable means the model or runtime was not available. Failed means a scheduled model errored. Not detected means a completed model returned no label; scan coverage was not assessed, so this does not mean the anatomy is absent.</p>
        </>
      ) : <p className="processing-note">No model-to-anatomy coverage report was recorded.</p>}

      {!!report.geometry && (
        <details className="geometry-details">
          <summary>Volume geometry and conversions</summary>
          <pre>{JSON.stringify(report.geometry, null, 2)}</pre>
        </details>
      )}

      {!!warnings.length && (
        <details>
          <summary>Processing warnings ({warnings.length})</summary>
          <ul>{warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
        </details>
      )}
    </section>
  )
}
