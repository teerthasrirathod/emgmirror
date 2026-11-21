import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type * as THREE from 'three'
import { join, loadManifest, normalise, type Manifest, type Structure } from './lib/manifest'
import { loadVolume, type VolumeResult } from './lib/volume'
import { preset, WINDOW_PRESETS, type WindowState } from './lib/windows'
import { DEFAULT_CLIP, type ClipState } from './model/clip'
import type { StructureState, ViewPreset } from './model/ModelView'
import type { SliceLayout } from './slices/SliceView'
import type { ObliqueState } from './slices/ObliqueView'
import StructureList from './ui/StructureList'
import Downloads from './ui/Downloads'
import { FileLinks, StructureFacts } from './ui/Facts'
import { COPY } from './ui/copy'
import { defaultOpacity } from './lib/appearance'

const ModelView = lazy(() => import('./model/ModelView'))
const SliceView = lazy(() => import('./slices/SliceView'))
const ObliqueView = lazy(() => import('./slices/ObliqueView'))

type Tab = 'model' | 'slices'
type SliceMode = SliceLayout | 'oblique'

function useMedia(q: string): boolean {
  const [m, setM] = useState(() => matchMedia(q).matches)
  useEffect(() => {
    const mq = matchMedia(q)
    const f = () => setM(mq.matches)
    mq.addEventListener('change', f)
    return () => mq.removeEventListener('change', f)
  }, [q])
  return m
}

const PRESETS: { name: ViewPreset; label: string; short: string }[] = [
  { name: 'anterior', label: 'Anterior', short: 'A' },
  { name: 'posterior', label: 'Posterior', short: 'P' },
  { name: 'left', label: 'Left', short: 'L' },
  { name: 'right', label: 'Right', short: 'R' },
  { name: 'superior', label: 'Superior', short: 'S' },
  { name: 'inferior', label: 'Inferior', short: 'I' },
]

export default function App() {
  const base = useMemo(() => new URLSearchParams(location.search).get('result') || 'sample/', [])
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [structures, setStructures] = useState<Structure[]>([])
  const [states, setStates] = useState<StructureState[]>([])
  const [error, setError] = useState<string | null>(null)
  const [modelReady, setModelReady] = useState(false)
  const [loadInfo, setLoadInfo] = useState<{ ms: number; triangles: number } | null>(null)
  const [volume, setVolume] = useState<VolumeResult | null>(null)
  const [fullVolume, setFullVolume] = useState<VolumeResult | null>(null)
  const [bounds, setBounds] = useState<THREE.Box3 | null>(null)
  const [tab, setTab] = useState<Tab>('model')
  const [sliceMode, setSliceMode] = useState<SliceMode>('multi')
  const [slicesOpened, setSlicesOpened] = useState(false)
  const [sliceStatus, setSliceStatus] = useState<{ loading: boolean; error?: string }>({ loading: false })
  const [win, setWin] = useState<WindowState>(preset('soft'))
  const [clip, setClip] = useState<ClipState>(DEFAULT_CLIP)
  const [ctOpacity, setCtOpacity] = useState(0.92)
  const [oblique, setOblique] = useState<ObliqueState>({ axis: 'axial', pos: 0.5, tiltA: 20, tiltB: 0 })
  const [selected, setSelected] = useState(-1)
  const [isolated, setIsolated] = useState(-1)
  const [hover, setHover] = useState<{ index: number; x: number; y: number }>({ index: -1, x: 0, y: 0 })
  const [viewPreset, setViewPreset] = useState<{ name: ViewPreset; seq: number }>({ name: 'anterior', seq: 0 })
  const [labelOpacity, setLabelOpacity] = useState(0.45)
  const [dragMode, setDragMode] = useState<'windowing' | 'pan' | 'crosshair'>('windowing')
  const [zoomSeq, setZoomSeq] = useState({ factor: 1, seq: 0 })
  const [showDownloads, setShowDownloads] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const [announce, setAnnounce] = useState('')
  const systemDark = useMedia('(prefers-color-scheme: dark)')
  const [themePref, setThemePref] = useState<'system' | 'light' | 'dark'>(() => {
    try {
      return (localStorage.getItem('ctv-theme') as 'light' | 'dark' | null) ?? 'system'
    } catch {
      return 'system'
    }
  })
  const dark = themePref === 'system' ? systemDark : themePref === 'dark'
  const reducedMotion = useMedia('(prefers-reduced-motion: reduce)')
  const narrow = useMedia('(max-width: 760px)')
  const coarse = useMedia('(pointer: coarse)')
  const lowPower = narrow || coarse

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }, [dark])

  // manifest first, then the model; the CT preview loads in a worker alongside
  useEffect(() => {
    performance.mark('ctv-start')
    loadManifest(base)
      .then((m) => {
        setManifest(m)
        const s = normalise(m)
        setStructures(s)
        setStates(s.map((x) => ({ visible: true, opacity: defaultOpacity(x.material, x.id) })))
      })
      .catch((e) => setError(String(e?.message ?? e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base])

  // the CT preview (for the slice on the cut) starts only once the model is on
  // screen, so it never competes with the model for bandwidth
  useEffect(() => {
    if (!modelReady || !manifest || volume) return
    const prev = manifest.volumes?.ct_preview ?? manifest.volumes?.ct ?? 'ct.nii.gz'
    loadVolume(join(base, prev), lowPower ? 192 : 256)
      .then(setVolume)
      .catch(() => setVolume(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelReady, manifest])

  // a finer volume for the oblique view, only when asked for
  useEffect(() => {
    if (tab !== 'slices' || sliceMode !== 'oblique' || fullVolume || !manifest) return
    loadVolume(join(base, manifest.volumes?.ct ?? 'ct.nii.gz'), lowPower ? 256 : 512)
      .then(setFullVolume)
      .catch(() => undefined)
  }, [tab, sliceMode, fullVolume, manifest, base, lowPower])

  const onLoaded = useCallback((info: { ms: number; triangles: number }) => {
    setModelReady(true)
    performance.mark('ctv-model-ready')
    const total = performance.measure('ctv-load', 'ctv-start', 'ctv-model-ready').duration
    setLoadInfo({ ms: total, triangles: info.triangles })
    ;(window as unknown as { __ctv: unknown }).__ctv = { modelReadyMs: total, triangles: info.triangles }
    setAnnounce(COPY.modelLoaded)
  }, [])

  const onPick = useCallback(
    (index: number) => {
      if (index < 0) {
        setIsolated(-1)
        setSelected(-1)
        return
      }
      setSelected(index)
      setIsolated((cur) => (cur === index ? -1 : index))
      setAnnounce(COPY.isolated(structures[index]?.name ?? ''))
    },
    [structures],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsolated(-1)
        setSelected(-1)
        setShowDownloads(false)
        setPanelOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const visibleList = useMemo(() => states.map((s) => s.visible), [states])
  const hovered = hover.index >= 0 ? structures[hover.index] : null
  const sel = selected >= 0 ? structures[selected] : null
  const ctUrl = manifest ? join(base, manifest.volumes?.ct ?? 'ct.nii.gz') : ''
  const labelsUrl = manifest ? join(base, manifest.volumes?.labels ?? 'labels.nii.gz') : null

  const setTheme = () => {
    const next = dark ? 'light' : 'dark'
    setThemePref(next)
    try {
      localStorage.setItem('ctv-theme', next)
    } catch {
      /* storage unavailable */
    }
  }

  const windowSelect = (
    <label className="field">
      <span>{COPY.windowLabel}</span>
      <select
        value={win.preset}
        onChange={(e) => e.target.value !== 'custom' && setWin(preset(e.target.value as 'soft'))}
      >
        {WINDOW_PRESETS.map((p) => (
          <option key={p.name} value={p.name}>
            {p.label}
          </option>
        ))}
        {win.preset === 'custom' && <option value="custom">{COPY.windowCustom(win)}</option>}
      </select>
    </label>
  )

  return (
    <div className={`app ${panelOpen ? 'panel-open' : ''}`}>
      <a className="skip" href="#main">
        {COPY.skip}
      </a>
      <header className="topbar">
        <div className="brand">
          <span className="brand-name">{COPY.product}</span>
          <span className="badge-ruo" title={COPY.ruoLong}>
            {COPY.ruoShort}
          </span>
        </div>
        <nav className="tabs" role="tablist" aria-label={COPY.viewsLabel}>
          <button role="tab" aria-selected={tab === 'model'} aria-controls="main" onClick={() => setTab('model')}>
            {COPY.tabModel}
          </button>
          <button
            role="tab"
            aria-selected={tab === 'slices'}
            aria-controls="main"
            onClick={() => {
              setTab('slices')
              setSlicesOpened(true)
            }}
          >
            {COPY.tabSlices}
          </button>
        </nav>
        <div className="top-actions">
          <button className="ghost panel-toggle" aria-expanded={panelOpen} aria-controls="sidebar" onClick={() => setPanelOpen((v) => !v)}>
            {COPY.structures}
          </button>
          <button className="ghost" onClick={() => setShowDownloads(true)} disabled={!manifest}>
            {COPY.downloads}
          </button>
          <button className="ghost icon" onClick={setTheme} aria-label={dark ? COPY.toLight : COPY.toDark} title={dark ? COPY.toLight : COPY.toDark}>
            {dark ? '☀' : '☾'}
          </button>
        </div>
      </header>

      <aside id="sidebar" className="sidebar" aria-label={COPY.structures}>
        {structures.length > 0 && (
          <StructureList
            structures={structures}
            states={states}
            selected={selected}
            onToggle={(i) => setStates((p) => p.map((s, k) => (k === i ? { ...s, visible: !s.visible } : s)))}
            onOpacity={(i, o) => setStates((p) => p.map((s, k) => (k === i ? { ...s, opacity: o } : s)))}
            onSetMany={(idx, visible) => setStates((p) => p.map((s, k) => (idx.includes(k) ? { ...s, visible } : s)))}
            onSelect={(i) => {
              setSelected(i)
              setIsolated(-1)
              if (narrow) setPanelOpen(false)
            }}
            onIsolate={(i) => {
              setSelected(i)
              setIsolated(i)
              setTab('model')
              if (narrow) setPanelOpen(false)
            }}
          />
        )}
      </aside>

      <main id="main" className="main" tabIndex={-1}>
        {error && (
          <div className="error" role="alert">
            <strong>{COPY.errorTitle}</strong> {error}
          </div>
        )}

        <section className={`view model-view ${tab === 'model' ? '' : 'hidden'}`} aria-label={COPY.tabModel}>
          <div className="toolbar" role="toolbar" aria-label={COPY.viewToolbar}>
            <div className="group" role="group" aria-label={COPY.cameraLabel}>
              {PRESETS.map((p) => (
                <button
                  key={p.name}
                  onClick={() => setViewPreset((v) => ({ name: p.name, seq: v.seq + 1 }))}
                  title={COPY.presetTitle(p.label)}
                  aria-label={COPY.presetTitle(p.label)}
                  aria-pressed={viewPreset.name === p.name && viewPreset.seq > 0}
                >
                  <span className="long">{p.label}</span>
                  <span className="short" aria-hidden="true">
                    {p.short}
                  </span>
                </button>
              ))}
            </div>
            <div className="group" role="group" aria-label={COPY.cutLabel}>
              <span className="group-label">{COPY.cutLabel}</span>
              {(['off', 'plane', 'box'] as const).map((m) => (
                <button key={m} aria-pressed={clip.mode === m} onClick={() => setClip((c) => ({ ...c, mode: m }))}>
                  {COPY.cutMode[m]}
                </button>
              ))}
            </div>
          </div>

          {clip.mode !== 'off' && (
            <div className="cut-panel" role="group" aria-label={COPY.cutSettings}>
              {clip.mode === 'plane' && (
                <>
                  <label className="field">
                    <span>{COPY.orientation}</span>
                    <select value={clip.axis} onChange={(e) => setClip((c) => ({ ...c, axis: e.target.value as ClipState['axis'] }))}>
                      <option value="axial">{COPY.axial}</option>
                      <option value="coronal">{COPY.coronal}</option>
                      <option value="sagittal">{COPY.sagittal}</option>
                    </select>
                  </label>
                  <Range label={COPY.position} value={clip.pos} min={0} max={1} step={0.005} onChange={(v) => setClip((c) => ({ ...c, pos: v }))} fmt={(v) => `${Math.round(v * 100)}%`} />
                  <Range label={COPY.tilt1} value={clip.tiltA} min={-60} max={60} step={1} onChange={(v) => setClip((c) => ({ ...c, tiltA: v }))} fmt={(v) => `${v}°`} />
                  <Range label={COPY.tilt2} value={clip.tiltB} min={-60} max={60} step={1} onChange={(v) => setClip((c) => ({ ...c, tiltB: v }))} fmt={(v) => `${v}°`} />
                  <button aria-pressed={clip.flip} onClick={() => setClip((c) => ({ ...c, flip: !c.flip }))}>
                    {COPY.flip}
                  </button>
                </>
              )}
              {clip.mode === 'box' &&
                (['x', 'y', 'z'] as const).map((ax) => (
                  <div key={ax} className="pair">
                    <Range label={COPY.boxAxis[ax][0]} value={clip.box[ax][0]} min={0} max={1} step={0.005} onChange={(v) => setClip((c) => ({ ...c, box: { ...c.box, [ax]: [Math.min(v, c.box[ax][1] - 0.02), c.box[ax][1]] } }))} fmt={(v) => `${Math.round(v * 100)}%`} />
                    <Range label={COPY.boxAxis[ax][1]} value={clip.box[ax][1]} min={0} max={1} step={0.005} onChange={(v) => setClip((c) => ({ ...c, box: { ...c.box, [ax]: [c.box[ax][0], Math.max(v, c.box[ax][0] + 0.02)] } }))} fmt={(v) => `${Math.round(v * 100)}%`} />
                  </div>
                ))}
              <Range label={COPY.ctOnCut} value={ctOpacity} min={0} max={1} step={0.01} onChange={setCtOpacity} fmt={(v) => (v < 0.01 ? COPY.off : `${Math.round(v * 100)}%`)} />
              {windowSelect}
              {!volume && <span className="hint">{COPY.ctLoading}</span>}
            </div>
          )}

          <div className="canvas-wrap" data-testid="model-canvas" tabIndex={0} role="img" aria-roledescription={COPY.canvasRole} aria-label={COPY.canvasLabel}>
            {manifest && (
              <Suspense fallback={null}>
                <ModelView
                  glbUrl={join(base, manifest.combined.web)}
                  structures={structures}
                  states={states}
                  volume={volume}
                  lpsToGltf={manifest.lps_to_gltf}
                  clip={clip}
                  window={win}
                  ctOpacity={ctOpacity}
                  isolated={isolated}
                  selected={selected}
                  preset={viewPreset}
                  dark={dark}
                  reducedMotion={reducedMotion}
                  lowPower={lowPower}
                  onLoaded={onLoaded}
                  onError={setError}
                  onHover={(index, x, y) => setHover({ index, x, y })}
                  onPick={onPick}
                  onBounds={setBounds}
                />
              </Suspense>
            )}
            {!modelReady && !error && (
              <div className="loading" role="status">
                <span className="spinner" aria-hidden="true" />
                {COPY.loadingModel}
              </div>
            )}
            {hovered && hover.index !== selected && (
              <div className="tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }} role="tooltip">
                <StructureFacts s={hovered} compact />
              </div>
            )}
            {isolated >= 0 && (
              <div className="isolate-bar" role="status">
                {COPY.isolatedBar(structures[isolated]?.name ?? '')}
                <button onClick={() => setIsolated(-1)}>{COPY.showAll}</button>
              </div>
            )}
            {sel && (
              <div className="info-card" aria-live="polite">
                <button className="close" aria-label={COPY.close} onClick={() => { setSelected(-1); setIsolated(-1) }}>
                  ×
                </button>
                <StructureFacts s={sel} />
                <div className="card-actions">
                  <button onClick={() => setIsolated((c) => (c === selected ? -1 : selected))} aria-pressed={isolated === selected}>
                    {isolated === selected ? COPY.showAll : COPY.isolate}
                  </button>
                  <FileLinks s={sel} base={base} />
                </div>
              </div>
            )}
          </div>
        </section>

        <section className={`view slice-view ${tab === 'slices' ? '' : 'hidden'}`} aria-label={COPY.tabSlices}>
          <div className="toolbar" role="toolbar" aria-label={COPY.sliceToolbar}>
            <div className="group" role="group" aria-label={COPY.layoutLabel}>
              {(['axial', 'coronal', 'sagittal', 'multi', 'oblique', 'render'] as SliceMode[]).map((m) => (
                <button key={m} aria-pressed={sliceMode === m} onClick={() => setSliceMode(m)}>
                  {COPY.sliceMode[m]}
                </button>
              ))}
            </div>
            <div className="group" role="group" aria-label={COPY.dragLabel}>
              <span className="group-label">{COPY.dragLabel}</span>
              {(['windowing', 'pan', 'crosshair'] as const).map((m) => (
                <button key={m} aria-pressed={dragMode === m} onClick={() => setDragMode(m)} disabled={sliceMode === 'oblique'}>
                  {COPY.drag[m]}
                </button>
              ))}
              <button aria-label={COPY.zoomIn} title={COPY.zoomIn} onClick={() => setZoomSeq((z) => ({ factor: 1.25, seq: z.seq + 1 }))} disabled={sliceMode === 'oblique'}>
                +
              </button>
              <button aria-label={COPY.zoomOut} title={COPY.zoomOut} onClick={() => setZoomSeq((z) => ({ factor: 0.8, seq: z.seq + 1 }))} disabled={sliceMode === 'oblique'}>
                −
              </button>
              <button onClick={() => setZoomSeq((z) => ({ factor: 0, seq: z.seq + 1 }))} disabled={sliceMode === 'oblique'}>
                {COPY.reset}
              </button>
            </div>
            <div className="group">
              {windowSelect}
              <Range label={COPY.labelOverlay} value={labelOpacity} min={0} max={1} step={0.05} onChange={setLabelOpacity} fmt={(v) => (v < 0.01 ? COPY.off : `${Math.round(v * 100)}%`)} />
            </div>
          </div>
          {sliceMode === 'oblique' && (
            <div className="cut-panel" role="group" aria-label={COPY.obliqueSettings}>
              <label className="field">
                <span>{COPY.startFrom}</span>
                <select value={oblique.axis} onChange={(e) => setOblique((o) => ({ ...o, axis: e.target.value as ObliqueState['axis'] }))}>
                  <option value="axial">{COPY.axial}</option>
                  <option value="coronal">{COPY.coronal}</option>
                  <option value="sagittal">{COPY.sagittal}</option>
                </select>
              </label>
              <Range label={COPY.position} value={oblique.pos} min={0} max={1} step={0.005} onChange={(v) => setOblique((o) => ({ ...o, pos: v }))} fmt={(v) => `${Math.round(v * 100)}%`} />
              <Range label={COPY.tilt1} value={oblique.tiltA} min={-80} max={80} step={1} onChange={(v) => setOblique((o) => ({ ...o, tiltA: v }))} fmt={(v) => `${v}°`} />
              <Range label={COPY.tilt2} value={oblique.tiltB} min={-80} max={80} step={1} onChange={(v) => setOblique((o) => ({ ...o, tiltB: v }))} fmt={(v) => `${v}°`} />
            </div>
          )}
          <div className="canvas-wrap slice-wrap" data-testid="slice-canvas">
            {slicesOpened && manifest && sliceMode !== 'oblique' && (
              <Suspense fallback={null}>
                <SliceView
                  ctUrl={ctUrl}
                  labelsUrl={labelsUrl}
                  structures={structures}
                  visible={visibleList}
                  layout={sliceMode as SliceLayout}
                  window={win}
                  onWindow={setWin}
                  labelOpacity={labelOpacity}
                  dragMode={dragMode}
                  dark={dark}
                  zoomSeq={zoomSeq}
                  onStatus={setSliceStatus}
                />
              </Suspense>
            )}
            {sliceMode === 'oblique' && (fullVolume || volume) && bounds && manifest && (
              <Suspense fallback={null}>
                <ObliqueView volume={(fullVolume || volume)!} lpsToGltf={manifest.lps_to_gltf} bounds={bounds} state={oblique} window={win} onWindow={setWin} />
              </Suspense>
            )}
            {sliceStatus.loading && sliceMode !== 'oblique' && (
              <div className="loading" role="status">
                <span className="spinner" aria-hidden="true" />
                {COPY.loadingSlices}
              </div>
            )}
            {sliceStatus.error && (
              <div className="error" role="alert">
                {sliceStatus.error}
              </div>
            )}
            <p className="slice-hint">{sliceMode === 'oblique' ? COPY.obliqueHint : COPY.sliceHint}</p>
          </div>
        </section>
      </main>

      <footer className="notice">
        <p>
          <strong>{COPY.ruoShort}.</strong> {COPY.notice}
        </p>
        <p className="meta">
          {manifest?.source?.dataset ? COPY.sourceLine(manifest.source.dataset, manifest.source.subject ?? '') : null}
          {loadInfo && <span className="perf"> {COPY.loadedIn(loadInfo.ms, loadInfo.triangles)}</span>}
        </p>
      </footer>

      {showDownloads && manifest && (
        <Downloads manifest={manifest} structures={structures} base={base} onClose={() => setShowDownloads(false)} />
      )}
      <div className="sr-only" aria-live="polite">
        {announce}
      </div>
    </div>
  )
}

function Range(props: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; fmt: (v: number) => string }) {
  const id = useRef(`r${Math.random().toString(36).slice(2, 8)}`).current
  return (
    <div className="field range">
      <label htmlFor={id}>{props.label}</label>
      <input
        id={id}
        type="range"
        min={props.min}
        max={props.max}
        step={props.step}
        value={props.value}
        aria-valuetext={props.fmt(props.value)}
        onChange={(e) => props.onChange(parseFloat(e.target.value))}
      />
      <output htmlFor={id}>{props.fmt(props.value)}</output>
    </div>
  )
}

