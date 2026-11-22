// CT display windows (Hounsfield units). Standard radiology display settings,
// used here only to make structures easier to see.
export type PresetName = 'soft' | 'bone' | 'lung' | 'angio' | 'custom'

export interface WindowState {
  centre: number
  width: number
  preset: PresetName
}

export const WINDOW_PRESETS: { name: Exclude<PresetName, 'custom'>; label: string; centre: number; width: number }[] = [
  { name: 'soft', label: 'Soft tissue', centre: 40, width: 400 },
  { name: 'bone', label: 'Bone', centre: 500, width: 2000 },
  { name: 'lung', label: 'Lung', centre: -600, width: 1500 },
  { name: 'angio', label: 'Vessels', centre: 170, width: 600 },
]

export function preset(name: Exclude<PresetName, 'custom'>): WindowState {
  const p = WINDOW_PRESETS.find((x) => x.name === name)!
  return { centre: p.centre, width: p.width, preset: name }
}
