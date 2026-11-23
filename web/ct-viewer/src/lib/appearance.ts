import type { MaterialKind } from './manifest'

/**
 * Default opacity per material kind. Everything is solid by default so shapes read clearly;
 * only the lungs start see-through, because they would otherwise hide the heart and vessels.
 * The viewer can make any structure see-through on request.
 */
export function defaultOpacity(kind: MaterialKind, id: string): number {
  if (kind === 'organ' && /lung/.test(id)) return 0.3
  return 1
}
