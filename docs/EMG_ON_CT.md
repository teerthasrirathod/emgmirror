# EMG on the CT forearm

`emgmirror ct emg-overlay --session data/dataset/<subject>/<session_id> --ct-arm <left|right> --emg-arm <left|right>`

Writes `results/ct/emg/scene.json` (CT meshes, regions, electrode sites) and `emg_on_ct.json`. Both are
personal health data: kept local and git-ignored. Research use, not for diagnosis or surgical planning.
The scene is shown in the signal viewer (`emgmirror view`, docs/SIGNAL_VIEWER.md), next to the
signals and in sync with the time cursor.

## What it computes

1. **Forearm frame from the CT bones.** The forearm axis is the principal axis of radius and ulna,
   pointing to the hand. The elbow is where the proximal ends of radius and ulna sit, and the wrist
   where their distal ends sit.
2. **Palm (volar) side, from anatomy.** For a left forearm in supination,
   `volar = axis × (ulna → radius)`. For a right forearm the order is reversed. This holds however
   the whole arm was rotated in the scanner. It assumes the forearm was supinated (radius and ulna
   side by side) and stays marked `volar_confirmed: false` until a person confirms it in the viewer.
3. **Electrode sites.** These go at the fraction along the forearm given in the session's setup:
   palm side for the flexor channel, back for the extensor channel, projected onto the CT skin.
4. **CT depths under each site,** along the inward normal: fat (skin to muscle surface), skin to
   bone or metal, and muscle thickness in between.
5. **Replay and live.** In the signal viewer, the palm-side and back halves of the forearm muscle, in
   the electrode band, are coloured by each channel's envelope relative to the room floor at the cursor
   time (grey at 1×, the channel colour at 10× or more).
6. **Mirroring.** If the EMG arm differs from the CT arm, everything is mirrored (x → −x, faces
   flipped) and labelled as mirrored.

## Limits (stated in the viewer and the JSON)

- **Two channels give two numbers per moment.** This is a two-region map, not individual muscles.
  The CT does not separate individual muscles either: it labels muscle and fat as tissue classes.
- **Electrode positions come from the setup text** (a fraction from the elbow), not measured on the
  skin.
- **The CT meshes come from `results/ct/meshes/`** (the full build, with its accuracy checks in
  `report.md`) unless `--meshes` names another folder. The muscle and fat surfaces are display
  surfaces, not closed solids.
- **When the CT arm and the EMG arm differ,** the depths describe the CT arm, mirrored onto the EMG
  side.

## Results

The depths, the bone length and the arm assignment for a person are in `results/ct/emg/emg_on_ct.json`
and `results/ct/emg/first_result_2026-11-17.md`. Both are git-ignored, because they describe one
person's anatomy. One check worth repeating on any result: a third of the way down the forearm, the
flexor compartment is normally the thicker one (several muscle layers). If the palm-side site shows
the thicker muscle, that supports the palm-side rule, but it does not prove it.

Tests: `tests/test_ct_emg_bridge.py` builds a synthetic forearm from cylinders and checks the
ray-triangle hits, the palm-side rule for both arms, the mirror, the depths and the region split
against known answers.
