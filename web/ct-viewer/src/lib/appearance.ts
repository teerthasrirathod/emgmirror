import type { MaterialKind } from './manifest'

/** Default opacity per material kind (organs are slightly translucent). */
export function defaultOpacity(kind: MaterialKind, id: string): number {
  if (kind !== 'organ') return 1
  if (/lung/.test(id)) return 0.28
  if (/(colon|small_bowel|duodenum|stomach)/.test(id)) return 0.5
  return 0.62
}

