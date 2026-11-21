// Build-time labelling. Set VITE_LABEL_STATUS=investigational for an
// investigational-device build; anything else (or unset) is research use only.
// It is deliberately not switchable from the URL.
export type LabelStatus = 'research' | 'investigational'

export const LABEL_STATUS: LabelStatus =
  import.meta.env.VITE_LABEL_STATUS === 'investigational' ? 'investigational' : 'research'
