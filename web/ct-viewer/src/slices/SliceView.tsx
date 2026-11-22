import { useEffect, useRef, useState } from 'react'
import NiiVue from '@niivue/niivue/webgl2'
import type { Structure } from '../lib/manifest'
import { WINDOW_PRESETS, type WindowState } from '../lib/windows'

export type SliceLayout = 'axial' | 'coronal' | 'sagittal' | 'multi' | 'render'

const SLICE_TYPE: Record<SliceLayout, number> = { axial: 0, coronal: 1, sagittal: 2, multi: 3, render: 4 }
const DRAG = { contrast: 1, pan: 3, windowing: 9, crosshair: 8 }

export interface SliceViewProps {
  ctUrl: string
  labelsUrl: string | null
  structures: Structure[]
  visible: boolean[]
  layout: SliceLayout
  window: WindowState
  onWindow: (w: WindowState) => void
  labelOpacity: number
  dragMode: 'windowing' | 'pan' | 'crosshair'
  dark: boolean
  zoomSeq: { factor: number; seq: number }
  onStatus: (s: { loading: boolean; error?: string; ms?: number }) => void
}

function labelColormap(structures: Structure[], visible: boolean[]) {
  const maxLabel = Math.max(0, ...structures.map((s) => s.label))
  const R = new Array(maxLabel + 1).fill(0)
  const G = [...R], B = [...R], A = [...R]
  const I = R.map((_, i) => i)
  const labels = R.map(() => '')
  structures.forEach((s, i) => {
    R[s.label] = s.rgb[0]
    G[s.label] = s.rgb[1]
    B[s.label] = s.rgb[2]
    A[s.label] = visible[i] === false ? 0 : 255
    labels[s.label] = s.name
  })
  return { R, G, B, A, I, labels }
}

export default function SliceView(p: SliceViewProps) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const nvRef = useRef<NiiVue | null>(null)
  const [ready, setReady] = useState(false)
  const pRef = useRef(p)
  pRef.current = p

  useEffect(() => {
    let cancelled = false
    const nv = new NiiVue({
      backgroundColor: [0, 0, 0, 1],
      isColorbarVisible: false,
      isOrientCubeVisible: true,
      isOrientationTextVisible: true,
      crosshairColor: [0.95, 0.75, 0.2, 0.9],
      isDragDropEnabled: false,
      multiplanarType: 0,
      isRadiological: true,
      isLegendVisible: false,
    } as never)
    nvRef.current = nv
    const t0 = performance.now()
    p.onStatus({ loading: true })
    ;(async () => {
      try {
        await nv.attachToCanvas(canvas.current!)
        const vols: { url: string; name: string; colormap?: string; calMin?: number; calMax?: number; opacity?: number }[] = [
          { url: p.ctUrl, name: 'ct.nii.gz', colormap: 'gray', calMin: p.window.centre - p.window.width / 2, calMax: p.window.centre + p.window.width / 2 },
        ]
        if (p.labelsUrl) {
          nv.addColormap('structures', labelColormap(p.structures, p.visible))
          vols.push({ url: p.labelsUrl, name: 'labels.nii.gz', colormap: 'structures', opacity: p.labelOpacity, isLegendVisible: false } as never)
        }
        await nv.loadVolumes(vols as never)
        if (cancelled) return
        nv.isLegendVisible = false
        nv.sliceType = SLICE_TYPE[p.layout]
        nv.primaryDragMode = DRAG[p.dragMode]
        nv.secondaryDragMode = DRAG.pan
        nv.addEventListener('volumeUpdated', (e) => {
          const d = (e as CustomEvent).detail
          if (d.volumeIndex !== 0) return
          const v = nv.volumes[0] as unknown as { calMin: number; calMax: number }
          const w = { centre: (v.calMin + v.calMax) / 2, width: v.calMax - v.calMin }
          const cur = pRef.current.window
          if (Math.abs(w.centre - cur.centre) > 0.5 || Math.abs(w.width - cur.width) > 0.5) pRef.current.onWindow({ ...w, preset: 'custom' })
        })
        setReady(true)
        p.onStatus({ loading: false, ms: performance.now() - t0 })
      } catch (err) {
        p.onStatus({ loading: false, error: `Could not load the CT slices: ${(err as Error)?.message ?? err}` })
      }
    })()
    return () => {
      cancelled = true
      nv.destroy()
      nvRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.ctUrl, p.labelsUrl])

  useEffect(() => {
    const nv = nvRef.current
    if (!nv || !ready) return
    nv.sliceType = SLICE_TYPE[p.layout]
    if (p.layout === 'render') {
      nv.volumeIllumination = 0.6
      nv.volumeGradientOpacity = 0.5
    }
  }, [p.layout, ready])

  useEffect(() => {
    const nv = nvRef.current
    if (!nv || !ready) return
    const v = nv.volumes[0] as unknown as { calMin: number; calMax: number }
    const lo = p.window.centre - p.window.width / 2, hi = p.window.centre + p.window.width / 2
    if (Math.abs(v.calMin - lo) > 0.5 || Math.abs(v.calMax - hi) > 0.5) void nv.setVolume(0, { calMin: lo, calMax: hi })
  }, [p.window, ready])

  useEffect(() => {
    const nv = nvRef.current
    if (!nv || !ready || nv.volumes.length < 2) return
    nv.addColormap('structures', labelColormap(p.structures, p.visible))
    void nv.setVolume(1, { colormap: 'structures', opacity: p.labelOpacity })
  }, [p.labelOpacity, p.visible, p.structures, ready])

  useEffect(() => {
    const nv = nvRef.current
    if (!nv || !ready) return
    nv.primaryDragMode = DRAG[p.dragMode]
  }, [p.dragMode, ready])

  useEffect(() => {
    const nv = nvRef.current
    if (!nv || !ready || p.zoomSeq.seq === 0) return
    if (p.zoomSeq.factor === 0) {
      nv.scaleMultiplier = 1
      nv.pan2Dxyzmm = [0, 0, 0, 1] as never
    } else {
      nv.scaleMultiplier = Math.min(8, Math.max(0.5, nv.scaleMultiplier * p.zoomSeq.factor))
    }
  }, [p.zoomSeq, ready])

  useEffect(() => {
    const nv = nvRef.current
    if (!nv || !ready) return
    nv.backgroundColor = p.dark ? [0.04, 0.045, 0.055, 1] : [0.08, 0.085, 0.1, 1]
  }, [p.dark, ready])

  return <canvas ref={canvas} className="slice-canvas" aria-label="CT slices. Drag to change brightness and contrast; scroll to move through slices." />
}

export { WINDOW_PRESETS }
