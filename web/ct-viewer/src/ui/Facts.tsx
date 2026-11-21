import { join, qcBadges, type Structure } from '../lib/manifest'
import { COPY } from './copy'

export function StructureFacts({ s, compact }: { s: Structure; compact?: boolean }) {
  const badges = qcBadges(s.qc)
  return (
    <div className="facts">
      <div className="facts-name">
        <span className="swatch" style={{ background: `rgb(${s.rgb.join(',')})` }} aria-hidden="true" />
        {s.name}
      </div>
      <div className="facts-row">
        {s.volumeMl != null && <span>{COPY.volume(s.volumeMl)}</span>}
        {!compact && s.areaCm2 != null && <span>{COPY.area(s.areaCm2)}</span>}
      </div>
      {badges.length > 0 && (
        <ul className="badges">
          {badges.map((b) => (
            <li key={b.text} className={`qc ${b.tone}`} title={COPY.qcExplain[b.text.split(' ')[0]] ?? ''}>
              {b.text}
            </li>
          ))}
        </ul>
      )}
      {!compact && s.qc.cut_by_scan_edge && <p className="note">{COPY.cutNote}</p>}
    </div>
  )
}

export function FileLinks({ s, base }: { s: Structure; base: string }) {
  const items: [string, string | undefined][] = [
    ['STL', s.files.stl],
    ['3MF', s.files['3mf']],
    ['GLB', s.files.glb],
  ]
  return (
    <span className="file-links">
      {items
        .filter(([, f]) => f)
        .map(([label, f]) => (
          <a key={label} href={join(base, f!)} download aria-label={COPY.downloadOne(label, s.name)}>
            {label}
          </a>
        ))}
    </span>
  )
}
