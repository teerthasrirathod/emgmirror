import { useEffect, useRef } from 'react'
import { join, type Manifest, type Structure } from '../lib/manifest'
import { FileLinks } from './Facts'
import { COPY } from './copy'

export default function Downloads({ manifest, structures, base, approved, onClose }: { manifest: Manifest; structures: Structure[]; base: string; approved: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
  }, [])
  return (
    <dialog ref={ref} className="downloads" aria-labelledby="dl-title" onClose={onClose} onCancel={onClose}>
      <div className="dl-head">
        <h2 id="dl-title">{COPY.dlTitle}</h2>
        <button className="close" aria-label={COPY.close} onClick={onClose} autoFocus>
          ×
        </button>
      </div>
      <p>{COPY.dlIntro}</p>
      <p className={approved ? 'ok-text' : 'warn-text'}>{approved ? COPY.dlApproved : COPY.dlNotApproved}</p>
      <h3>{COPY.dlWhole}</h3>
      <ul className="dl-whole">
        <li>
          <a href={join(base, manifest.combined.web)} download>
            {COPY.dlWeb}
          </a>
        </li>
        {manifest.combined.print && (
          <li>
            <a href={join(base, manifest.combined.print)} download>
              {COPY.dlPrint}
            </a>
          </li>
        )}
      </ul>
      <h3>{COPY.dlPer}</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">{COPY.dlName}</th>
            <th scope="col">{COPY.dlFiles}</th>
          </tr>
        </thead>
        <tbody>
          {structures.map((s) => (
            <tr key={s.id}>
              <th scope="row">{s.name}</th>
              <td>
                <FileLinks s={s} base={base} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </dialog>
  )
}
