import { useEffect, useRef, useState } from 'react'
import { zipSync } from 'fflate'

type Phase =
  | { kind: 'idle' }
  | { kind: 'packing'; files: number }
  | { kind: 'uploading'; pct: number }
  | { kind: 'processing'; job: string; step: string; since: number }
  | { kind: 'failed'; message: string }

const STEP_LABEL: Record<string, string> = {
  received: 'Waiting in line',
  reading: 'Reading the scan',
  segmenting: 'Finding bones, organs and vessels',
  'building 3D': 'Building the 3D model',
  done: 'Done',
}
const ORDER = ['reading', 'segmenting', 'building 3D']

const PART = 16 * 1024 * 1024 // the host accepts at most 32 MB per request

async function api(method: string, url: string, body?: Blob): Promise<any> {
  let last: unknown
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(url, { method, body })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `Upload failed (${r.status})`)
      return j
    } catch (e) {
      last = e
      if (e instanceof Error && /Expected part|larger than|Upload a /.test(e.message)) break
      await new Promise((res) => setTimeout(res, 1500 * (attempt + 1)))
    }
  }
  throw last instanceof Error ? last : new Error('Upload failed: check your connection and try again.')
}

/** Upload in 16 MB parts, then start processing. Returns the job id. */
async function upload(blob: Blob, name: string, onPct: (p: number) => void): Promise<string> {
  const { job } = await api('POST', `/api/jobs?name=${encodeURIComponent(name)}`)
  const parts = Math.max(1, Math.ceil(blob.size / PART))
  for (let n = 0; n < parts; n++) {
    try {
      await api('PUT', `/api/jobs/${job}/part?n=${n}`, blob.slice(n * PART, (n + 1) * PART))
    } catch (e) {
      // the server already has this part (its reply was lost): carry on
      if (!(e instanceof Error && e.message === `Expected part ${n + 1}.`)) throw e
    }
    onPct(Math.round(((n + 1) / parts) * 100))
  }
  await api('POST', `/api/jobs/${job}/start`)
  return job
}

async function packFolder(files: File[]): Promise<Blob> {
  const entries: Record<string, Uint8Array> = {}
  for (const f of files) {
    const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
    if (entries[rel]) {
      // Duplicate relative path – abort and surface a clear error to the user.
      throw new Error(`Duplicate file path "${rel}" in folder upload. Rename or remove the duplicate files.`)
    }
    entries[rel] = new Uint8Array(await f.arrayBuffer())
  }
  return new Blob([zipSync(entries, { level: 0 })], { type: 'application/zip' })
}

export default function UploadHome() {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const [drag, setDrag] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const folderRef = useRef<HTMLInputElement>(null)
  const [, tick] = useState(0)

  useEffect(() => {
    document.documentElement.dataset.theme = 'dark'
  }, [])

  // poll the job until the 3D model is ready, then open the viewer
  useEffect(() => {
    if (phase.kind !== 'processing') return
    let stop = false
    const t = setInterval(async () => {
      tick((n) => n + 1)
      try {
        const r = await fetch(`/api/jobs/${phase.job}`)
        if (!r.ok) throw new Error('This job is no longer available.')
        const s = await r.json()
        if (stop) return
        if (s.state === 'done') location.href = `/?result=${encodeURIComponent(`/results/${phase.job}/`)}`
        else if (s.state === 'failed') setPhase({ kind: 'failed', message: s.error || 'Processing failed.' })
        else if (s.step !== phase.step) setPhase({ ...phase, step: s.step })
      } catch (e) {
        if (!stop) setPhase({ kind: 'failed', message: (e as Error).message })
      }
    }, 3000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [phase])

  async function start(files: File[], fromFolder: boolean) {
    if (!files.length) return
    try {
      let blob: Blob
      let name: string
      const one = files[0]
      if (files.length === 1 && /\.(nhdr|mhd|hdr|img|raw|zraw)$/i.test(one.name)) {
        throw new Error('This format uses paired files. Select the header and its data file together, or choose the folder containing the complete scan.')
      }
      if (!fromFolder && files.length === 1 && /\.(zip|nii|nii\.gz|nrrd|mha)$/i.test(one.name)) {
        blob = one
        name = one.name
      } else {
        setPhase({ kind: 'packing', files: files.length })
        blob = await packFolder(files)
        name = 'scan.zip'
      }
      setPhase({ kind: 'uploading', pct: 0 })
      const job = await upload(blob, name, (pct) => setPhase({ kind: 'uploading', pct }))
      setPhase({ kind: 'processing', job, step: 'received', since: Date.now() })
    } catch (e) {
      setPhase({ kind: 'failed', message: (e as Error).message })
    }
  }

  const busy = phase.kind === 'packing' || phase.kind === 'uploading' || phase.kind === 'processing'
  const elapsed = phase.kind === 'processing' ? Math.floor((Date.now() - phase.since) / 1000) : 0

  return (
    <div className="home">
      <header className="home-top">
        <span className="home-brand">CT to 3D</span>
        <span className="home-ruo">Research use only. Not for diagnosis or surgical planning.</span>
      </header>
      <main className="home-main">
        <h1>Upload a CT scan. Get a 3D model.</h1>
        <p className="home-lede">Bones, organs, vessels and muscles are found automatically and shown in 3D, next to the original CT slices.</p>

        {!busy && (
          <>
            <div
              className={`drop ${drag ? 'over' : ''} ${agreed ? '' : 'locked'}`}
              onDragOver={(e) => {
                e.preventDefault()
                setDrag(true)
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDrag(false)
                if (agreed) void start(Array.from(e.dataTransfer.files), false)
              }}
            >
              <p className="drop-title">Drop the scan here</p>
              <p className="drop-sub">DICOM folder or ZIP, NIfTI, NRRD, MetaImage, or a volume with paired files</p>
              <div className="drop-actions">
                <button className="primary" disabled={!agreed} onClick={() => folderRef.current?.click()}>
                  Choose scan folder
                </button>
                <button disabled={!agreed} onClick={() => fileRef.current?.click()}>
                  Choose a file
                </button>
              </div>
              <input
                ref={folderRef}
                type="file"
                hidden
                // @ts-expect-error non-standard but supported by all major browsers
                webkitdirectory=""
                multiple
                onChange={(e) => void start(Array.from(e.target.files ?? []), true)}
              />
              <input ref={fileRef} type="file" hidden multiple accept=".zip,.nii,.gz,.nrrd,.nhdr,.mha,.mhd,.hdr,.img,.raw,.zraw" onChange={(e) => void start(Array.from(e.target.files ?? []), false)} />
            </div>
            <label className="consent">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              <span>
                I understand this is a research tool, not a medical device. Uploaded source files and derived processing data are stored on the processing server. Jobs are eligible for cleanup after the configured retention period (default 24 hours); cleanup runs when a later upload creates a job. Original DICOM files may contain identifying information.
              </span>
            </label>

            <p className="home-try">
              <a className="sample-link" href="/?result=sample/">
                See the multi-structure CT sample
              </a>{' '}
              (thorax, abdomen and pelvis only; dataset reference labels, not emgmirror output)
            </p>
            <p className="home-try">
              <a className="sample-link" href="/?result=visible-human/">
                Open the full-body skeletal CT reference
              </a>{' '}
              (142 labelled bones from real whole-body CT; reference data, not emgmirror inference; elbow coverage is incomplete in the source scan)
            </p>
            <p className="home-try">Works best with thin-slice CT (1–2 mm). The 3D model shows the body parts the scan covers.</p>
          </>
        )}

        {phase.kind === 'packing' && <Progress title={`Preparing ${phase.files} files`} />}
        {phase.kind === 'uploading' && <Progress title="Uploading" pct={phase.pct} />}
        {phase.kind === 'processing' && (
          <div className="progress-card" role="status" aria-live="polite">
            <p className="pc-title">Working on your scan</p>
            <ol className="steps">
              {ORDER.map((s) => {
                const cur = ORDER.indexOf(phase.step)
                const me = ORDER.indexOf(s)
                const state = phase.step === 'received' ? 'todo' : me < cur ? 'done' : me === cur ? 'now' : 'todo'
                return (
                  <li key={s} className={state}>
                    {STEP_LABEL[s]}
                  </li>
                )
              })}
            </ol>
            <p className="pc-note">
              {phase.step === 'received' ? STEP_LABEL.received : 'This usually takes a few minutes.'} {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}
            </p>
          </div>
        )}
        {phase.kind === 'failed' && (
          <div className="progress-card failed" role="alert">
            <p className="pc-title">That did not work</p>
            <p>{phase.message}</p>
            <button className="primary" onClick={() => setPhase({ kind: 'idle' })}>
              Try another scan
            </button>
          </div>
        )}
      </main>
    </div>
  )
}

function Progress({ title, pct }: { title: string; pct?: number }) {
  return (
    <div className="progress-card" role="status" aria-live="polite">
      <p className="pc-title">{title}</p>
      <div className="bar">
        <span style={{ width: `${pct ?? 30}%` }} className={pct === undefined ? 'indeterminate' : ''} />
      </div>
      {pct !== undefined && <p className="pc-note">{pct}%</p>}
    </div>
  )
}
