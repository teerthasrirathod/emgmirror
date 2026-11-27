import { useMemo, useState } from 'react'
import { SYSTEM_LABEL, SYSTEM_ORDER, type Structure } from '../lib/manifest'
import type { StructureState } from '../model/ModelView'
import { COPY } from './copy'

interface Props {
  structures: Structure[]
  states: StructureState[]
  selected: number
  onToggle: (i: number) => void
  onOpacity: (i: number, o: number) => void
  onSetMany: (idx: number[], visible: boolean) => void
  /** show only these structures (null = show everything) */
  onShowOnly: (idx: number[] | null) => void
  onSelect: (i: number) => void
  onIsolate: (i: number) => void
  excluded: Set<string>
  readOnly: boolean
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export default function StructureList(p: Props) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [view, setView] = useState<string>('all')
  const bySystem = (sys: string) => p.structures.map((s, i) => ({ s, i })).filter(({ s }) => s.system === sys).map(({ i }) => i)
  const presets = SYSTEM_ORDER.filter((sys) => p.structures.some((s) => s.system === sys))
  const applyView = (v: string) => {
    setView(v)
    p.onShowOnly(v === 'all' ? null : bySystem(v))
  }
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return SYSTEM_ORDER.map((sys) => ({
      sys,
      items: p.structures
        .map((s, i) => ({ s, i }))
        .filter(({ s }) => s.system === sys)
        .filter(({ s }) => !needle || s.name.toLowerCase().includes(needle) || s.id.toLowerCase().includes(needle))
        .sort((a, b) => collator.compare(a.s.name, b.s.name)),
    })).filter((g) => g.items.length > 0)
  }, [p.structures, q])
  const shown = p.states.filter((s) => s.visible).length

  return (
    <div className="structure-list">
      <div className="list-head">
        <label className="search">
          <span className="sr-only">{COPY.search}</span>
          <input type="search" placeholder={COPY.searchPlaceholder} value={q} onChange={(e) => setQ(e.target.value)} aria-label={COPY.search} />
        </label>
        <div className="quick-views" role="group" aria-label="Quick views">
          <button aria-pressed={view === 'all'} onClick={() => applyView('all')}>
            Everything
          </button>
          {presets.map((sys) => (
            <button key={sys} aria-pressed={view === sys} onClick={() => applyView(sys)}>
              {SYSTEM_LABEL[sys]}
            </button>
          ))}
        </div>
        <p className="count" aria-live="polite">
          {COPY.count(shown, p.structures.length)}
        </p>
      </div>
      {groups.length === 0 && <p className="empty">{COPY.noMatch}</p>}
      {groups.map(({ sys, items }) => {
        const isOpen = q ? true : open[sys]
        const allVisible = items.every(({ i }) => p.states[i]?.visible)
        const label = SYSTEM_LABEL[sys]
        return (
          <section key={sys} className="group-block">
            <div className="group-head">
              <button className="disclosure" aria-expanded={isOpen} onClick={() => setOpen((o) => ({ ...o, [sys]: !isOpen }))}>
                <span className="chev" aria-hidden="true">
                  ▸
                </span>
                {label} <span className="n">({items.length})</span>
              </button>
              <button className="link" onClick={() => p.onSetMany(items.map((x) => x.i), !allVisible)}>
                {allVisible ? COPY.hideGroup(label) : COPY.showGroup(label)}
              </button>
            </div>
            {isOpen && (
              <ul>
                {items.map(({ s, i }) => {
                  const st = p.states[i]
                  return (
                    <li key={s.id} className={`row ${p.selected === i ? 'selected' : ''} ${st?.visible ? '' : 'off'}`}>
                      <button
                        className="eye"
                        aria-pressed={!!st?.visible}
                        aria-label={COPY.toggleItem(s.name, !!st?.visible)}
                        title={st?.visible ? COPY.hide : COPY.show}
                        onClick={() => p.onToggle(i)}
                        disabled={p.excluded.has(s.id)}
                      >
                        <span className="swatch" style={{ background: `rgb(${s.rgb.join(',')})` }} aria-hidden="true" />
                      </button>
                      <button className="name" onClick={() => p.onSelect(i)} onDoubleClick={() => p.onIsolate(i)} title={COPY.isolateItem(s.name)}>
                        {s.name}
                        {p.excluded.has(s.id) && <span className="tag-excluded">{COPY.excludedTag}</span>}
                        {(s.sourceStatus === 'partial' || s.qc.cut_by_scan_edge || (typeof s.qc.minimum_scan_edge_distance_mm === 'number' && s.qc.minimum_scan_edge_distance_mm <= 5)) && (
                          <span className="dot-warn" title={s.sourceStatus === 'partial' ? (s.sourceStatusReason ?? 'Source annotation is incomplete.') : s.qc.cut_by_scan_edge ? COPY.qcExplain.Cut : `Near scan edge: ${s.qc.minimum_scan_edge_distance_mm?.toFixed(1)} mm`}>
                            <span className="sr-only">{s.sourceStatus === 'partial' ? 'Source annotation is incomplete.' : s.qc.cut_by_scan_edge ? COPY.qcExplain.Cut : `Near scan edge: ${s.qc.minimum_scan_edge_distance_mm?.toFixed(1)} mm`}</span>
                          </span>
                        )}
                      </button>
                      {!p.readOnly && p.selected === i && <input
                        className="opacity"
                        type="range"
                        min={0.05}
                        max={1}
                        step={0.05}
                        value={st?.opacity ?? 1}
                        aria-label={`${COPY.opacity}: ${s.name}`}
                        aria-valuetext={`${Math.round((st?.opacity ?? 1) * 100)}%`}
                        onChange={(e) => p.onOpacity(i, parseFloat(e.target.value))}
                      />}
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
        )
      })}
    </div>
  )
}
