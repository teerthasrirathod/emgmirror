import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
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
import { LABEL_STATUS } from './lib/build'
import { modelHash } from './lib/hash'
import { builtAssets, offlineUrls, registerSW, saveOffline } from './lib/offline'
import { isApproved, loadRecord, saveRecord, versionOf, type AuditEvent, type Edit, type ReviewRecord, type Reviewer } from './review/review'
import ReviewPanel from './review/ReviewPanel'
import StatusBar from './ui/StatusBar'
import OrientationMarkers from './ui/OrientationMarkers'

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
  // device groundwork: reference mode, review/approval, offline
  const [reference, setReference] = useState(() => new URLSearchParams(location.search).get('mode') === 'reference')
  const [record, setRecord] = useState<ReviewRecord | null>(null)
  const [version, setVersion] = useState<{ editsHash: string; versionHash: string } | null>(null)
  const [reviewer, setReviewerState] = useState<Reviewer | null>(() => {
    try {
      return JSON.parse(sessionStorage.getItem('ctv-reviewer') ?? 'null')
    } catch {
      return null
    }
  })
  const [reviewOpen, setReviewOpen] = useState(false)
  const [tool, setTool] = useState<'view' | 'erase'>('view')
  const [radiusMm, setRadiusMm] = useState(8)
  const [online, setOnline] = useState(navigator.onLine)
  const offlineKey = `ctv-offline:${base}`
  const [savedAt, setSavedAt] = useState<string | null>(() => {
    try {
      return localStorage.getItem(offlineKey)
    } catch {
      return null
    }
  })
  const [saveState, setSaveState] = useState<string | null>(null)
  const orientRef = useRef<HTMLDivElement>(null)
  const orientRef2 = useRef<HTMLDivElement>(null)
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
    document.documentElement.dataset.theme = dark || reference ? 'dark' : 'light'
  }, [dark, reference])

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

  useEffect(() => {
    void registerSW()
    const on = () => setOnline(navigator.onLine)
    window.addEventListener('online', on)
    window.addEventListener('offline', on)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', on)
    }
  }, [])

  useEffect(() => {
    const u = new URL(location.href)
    if (reference) u.searchParams.set('mode', 'reference')
    else u.searchParams.delete('mode')
    history.replaceState(null, '', u)
    if (reference) {
      setTool('view')
      setReviewOpen(false)
      setShowDownloads(false)
    }
  }, [reference])

  // version of the result as delivered, then the review record for it
  useEffect(() => {
    if (!manifest) return
    modelHash(join(base, 'manifest.json'), join(base, manifest.combined.web))
      .then((h) => setRecord(loadRecord(h)))
      .catch(() => setRecord(null))
  }, [manifest, base])

  useEffect(() => {
    if (!record) return
    let live = true
    versionOf(record.modelHash, record.edits).then((v) => live && setVersion(v))
    return () => {
      live = false
    }
  }, [record])

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

  const excluded = useMemo(() => new Set((record?.edits ?? []).filter((e) => e.kind === 'exclude').map((e) => e.structure)), [record])
  const effStates = useMemo(() => states.map((st, i) => (excluded.has(structures[i]?.id) ? { ...st, visible: false } : st)), [states, excluded, structures])
  const unitScale = manifest?.units === 'mm' ? 1 : 0.001
  const erasures = useMemo(
    () =>
      (record?.edits ?? [])
        .filter((e): e is Extract<Edit, { kind: 'erase' }> => e.kind === 'erase')
        .map((e) => ({ index: structures.findIndex((x) => x.id === e.structure), center: new THREE.Vector3(...e.center), radius: e.radiusMm * unitScale }))
        .filter((e) => e.index >= 0),
    [record, structures, unitScale],
  )
  const approved = !!record && isApproved(record, version?.versionHash ?? null)
  const canEdit = !!reviewer && !reference && !!record

  /** Change the record, recompute its version and log it; an old approval lapses. */
  const commit = useCallback(
    async (change: (r: ReviewRecord) => ReviewRecord, action: AuditEvent['action'], detail: string) => {
      if (!record) return
      const next = change(record)
      const v = await versionOf(next.modelHash, next.edits)
      const time = new Date().toISOString()
      const user = reviewer?.name ?? 'unknown'
      const audit = [...next.audit, { time, user, action, detail, versionHash: v.versionHash }]
      if (record.approval && next.approval && next.approval.versionHash !== v.versionHash && action !== 'revoke')
        audit.push({ time, user, action: 'invalidate', detail: 'models changed after approval', versionHash: v.versionHash })
      const saved = { ...next, audit }
      saveRecord(saved)
      setRecord(saved)
    },
    [record, reviewer],
  )
  const nameOf = (id: string) => structures.find((x) => x.id === id)?.name ?? id
  const addEdit = (e: Edit) =>
    commit((r) => ({ ...r, edits: [...r.edits, e] }), 'edit', e.kind === 'exclude' ? COPY.editExclude(nameOf(e.structure)) : COPY.editErase(nameOf(e.structure), e.radiusMm))
  const setReviewer = (r: Reviewer | null) => {
    setReviewerState(r)
    if (!r) setTool('view')
    try {
      if (r) sessionStorage.setItem('ctv-reviewer', JSON.stringify(r))
      else sessionStorage.removeItem('ctv-reviewer')
    } catch {
      /* storage unavailable */
    }
  }
  const onErase = useCallback(
    (index: number, p: THREE.Vector3) => {
      const s = structures[index]
      if (!s || !canEdit) return
      void addEdit({ kind: 'erase', structure: s.id, center: [p.x, p.y, p.z], radiusMm })
      setAnnounce(COPY.editErase(s.name, radiusMm))
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [structures, canEdit, radiusMm, commit],
  )
  const doSaveOffline = async () => {
    if (!manifest) return
    const v = manifest.volumes ?? {}
    const files = ['manifest.json', manifest.combined.web, v.ct ?? 'ct.nii.gz', v.labels ?? 'labels.nii.gz', ...(v.ct_preview ? [v.ct_preview] : [])].map((f) => join(base, f))
    setSaveState(COPY.savingOffline(0, files.length))
    try {
      const urls = [...new Set([...offlineUrls(files), ...(await builtAssets())])]
      const r = await saveOffline(urls, (d, t) => setSaveState(COPY.savingOffline(d, t)))
      if (r.failed.length) throw new Error(`${r.failed.length} files failed`)
      setSavedAt(r.time)
      try {
        localStorage.setItem(offlineKey, r.time)
      } catch {
        /* storage unavailable */
      }
      setSaveState(COPY.savedOffline)
    } catch (err) {
      setSaveState(COPY.saveFailed((err as Error).message))
    }
  }

  const visibleList = useMemo(() => effStates.map((s) => s.visible), [effStates])
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
    <div className={`app ${panelOpen ? 'panel-open' : ''} ${reference ? 'reference' : ''} ${reviewOpen ? 'review-open' : ''}`}>
      <a className="skip" href="#main">
        {COPY.skip}
      </a>
      <StatusBar
        study={manifest?.study ?? null}
        versionHash={version?.versionHash ?? null}
        approval={record?.approval ?? null}
        approved={approved}
        reference={reference}
        offline={{ online, savedAt }}
        onOpenReview={reference ? undefined : () => setReviewOpen(true)}
      />
      <header className="topbar">
        <div className="brand">
          <span className="brand-name">{COPY.product}</span>
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
          {!reference && (
            <button className="ghost" onClick={() => setReviewOpen((v) => !v)} aria-expanded={reviewOpen} disabled={!record}>
              {COPY.review}
            </button>
          )}
          {!reference && (
            <button className="ghost" onClick={() => setShowDownloads(true)} disabled={!manifest}>
              {COPY.downloads}
            </button>
          )}
          <button className="ghost" aria-pressed={reference} onClick={() => setReference((v) => !v)}>
            {reference ? COPY.exitReference : COPY.referenceMode}
          </button>
          {!reference && <button className="ghost icon" onClick={setTheme} aria-label={dark ? COPY.toLight : COPY.toDark} title={dark ? COPY.toLight : COPY.toDark}>
            {dark ? '☀' : '☾'}
          </button>}
        </div>
      </header>
      {reference && (
        <div className="ref-bar" role="region" aria-label={COPY.referenceMode}>
          <span>{COPY.referenceIntro}</span>
          <button onClick={doSaveOffline} disabled={!manifest || !!saveState?.startsWith('Saving')}>
            {COPY.saveOffline}
          </button>
          {saveState && <span role="status">{saveState}</span>}
        </div>
      )}

      <aside id="sidebar" className="sidebar" aria-label={COPY.structures}>
        {structures.length > 0 && (
          <StructureList
            structures={structures}
            states={effStates}
            excluded={excluded}
            readOnly={reference}
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
                  states={effStates}
                  volume={volume}
                  lpsToGltf={manifest.lps_to_gltf}
                  clip={clip}
                  window={win}
                  ctOpacity={ctOpacity}
                  isolated={isolated}
                  selected={selected}
                  preset={viewPreset}
                  dark={dark || reference}
                  reducedMotion={reducedMotion}
                  lowPower={lowPower}
                  onLoaded={onLoaded}
                  onError={setError}
                  onHover={(index, x, y) => setHover({ index, x, y })}
                  onPick={onPick}
                  onBounds={setBounds}
                  tool={canEdit ? tool : 'view'}
                  onErase={onErase}
                  erasures={erasures}
                  hoverEnabled={!reference}
                  orientEl={orientRef}
                />
              </Suspense>
            )}
            <OrientationMarkers ref={orientRef} />
            {!modelReady && !error && (
              <div className="loading" role="status">
                <span className="spinner" aria-hidden="true" />
                {COPY.loadingModel}
              </div>
            )}
            {hovered && hover.index !== selected && !reference && (
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
                  {canEdit && (
                    <button
                      onClick={() => {
                        const i = record!.edits.findIndex((e) => e.kind === 'exclude' && e.structure === sel.id)
                        if (i >= 0) void commit((r) => ({ ...r, edits: r.edits.filter((_, k) => k !== i) }), 'undo', COPY.editExclude(sel.name))
                        else void addEdit({ kind: 'exclude', structure: sel.id, reason: 'reviewer' })
                      }}
                    >
                      {excluded.has(sel.id) ? COPY.include : COPY.exclude}
                    </button>
                  )}
                  {!reference && <FileLinks s={sel} base={base} />}
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
                <ObliqueView volume={(fullVolume || volume)!} lpsToGltf={manifest.lps_to_gltf} bounds={bounds} state={oblique} window={win} onWindow={setWin} orientEl={orientRef2} />
              </Suspense>
            )}
            {sliceMode === 'oblique' && <OrientationMarkers ref={orientRef2} />}
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
          {LABEL_STATUS === 'research' ? (
            <>
              <strong>{COPY.ruoShort}.</strong> {COPY.notice}
            </>
          ) : (
            COPY.noticeInvestigational
          )}
        </p>
        <p className="meta">
          {manifest?.source?.dataset ? COPY.sourceLine(manifest.source.dataset, manifest.source.subject ?? '') : null}
          {loadInfo && <span className="perf"> {COPY.loadedIn(loadInfo.ms, loadInfo.triangles)}</span>}
        </p>
      </footer>

      {showDownloads && manifest && (
        <Downloads manifest={manifest} structures={structures} base={base} approved={approved} onClose={() => setShowDownloads(false)} />
      )}
      {reviewOpen && record && !reference && (
        <ReviewPanel
          record={record}
          structures={structures}
          versionHash={version?.versionHash ?? null}
          approved={approved}
          reviewer={reviewer}
          onReviewer={setReviewer}
          slicesSeen={slicesOpened}
          tool={tool}
          onTool={(t) => {
            setTool(t)
            if (t === 'erase') setTab('model')
          }}
          radiusMm={radiusMm}
          onRadius={setRadiusMm}
          onRemoveEdit={(i) => {
            const e = record.edits[i]
            void commit((r) => ({ ...r, edits: r.edits.filter((_, k) => k !== i) }), 'undo', e.kind === 'exclude' ? COPY.editExclude(nameOf(e.structure)) : COPY.editErase(nameOf(e.structure), e.radiusMm))
          }}
          onApprove={() => {
            if (!reviewer || !version) return
            const approval = { user: reviewer.name, role: reviewer.role, time: new Date().toISOString(), modelHash: record.modelHash, editsHash: version.editsHash, versionHash: version.versionHash }
            void commit((r) => ({ ...r, approval }), 'approve', `${record.edits.length} corrections`)
            setAnnounce(COPY.approvedLong(approval))
          }}
          onRevoke={() => void commit((r) => ({ ...r, approval: null }), 'revoke', 'approval withdrawn')}
          onClose={() => {
            setReviewOpen(false)
            setTool('view')
          }}
        />
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

