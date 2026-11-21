import { forwardRef } from 'react'
import { COPY } from './copy'

/** L/R/A/P/S/I letters on the four edges of a view; filled in imperatively. */
const OrientationMarkers = forwardRef<HTMLDivElement>(function OrientationMarkers(_, ref) {
  return (
    <div ref={ref} className="orient" aria-hidden="true" title={COPY.orientTitle}>
      <span data-edge="top" className="o-top" />
      <span data-edge="bottom" className="o-bottom" />
      <span data-edge="left" className="o-left" />
      <span data-edge="right" className="o-right" />
    </div>
  )
})
export default OrientationMarkers
