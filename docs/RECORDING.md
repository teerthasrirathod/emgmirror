# Recording guide

The analysis compares left and right *with the same electrodes in mirrored positions*. Skin, fat
and electrode differences cancel only if placement and preparation match. Most of the accuracy of
this project comes from this page.

## Safety first

- **Electrodes on a person are blocked until [ELECTRICAL_SAFETY.md](ELECTRICAL_SAFETY.md) passes for
  this exact rig.** The points below are part of that gate. They do not replace it.
- Power the EMG board only from a **battery-powered laptop with the charger and every other
  mains-connected device (monitor, dock, Ethernet, powered hub) unplugged**, or through a suitable
  USB isolator. Never connect electrodes to anything mains-powered.
- Don't put electrodes over broken skin, fresh scars or surgical hardware without clearance from
  your surgeon.
- Stop if anything tingles, burns or hurts. A maximal-effort task on a recovering arm can be
  contraindicated, so ask your physiotherapist which tasks and effort levels are safe. Edit
  `protocol` in your config to drop or soften tasks.

## Landmarks and placement (same on both arms)

Measure the **forearm length**: elbow crease to wrist crease. Write positions as a percentage of
that length and mark them with a skin pen on both arms.

| Channel | Where | How to find it |
|---|---|---|
| Flexors | Palm side, ~30% of the length down from the elbow crease, slightly toward the little-finger side of the midline | Make a fist and flex the wrist: the bulge under your fingers |
| Extensors | Back of the forearm, ~25–30% down from the elbow, centre of the extensor bulge | Lift the fingers/wrist back against resistance |
| Reference / ground | A bony spot with no muscle: the elbow tip (olecranon) or the wrist bone on the little-finger side | Whatever the board calls REF/GND |

- Electrode pairs run **along** the muscle fibres (along the forearm), ~2 cm centre to centre.
- Clean the skin with alcohol and let it dry. Use fresh gel electrodes from the same packet on both
  arms.
- Take photos of the placement for repeat sessions.
- Tape the cables so they don't tug the electrodes during movement.

## Tape measurements (for the 3D model)

Under `subject` in your config, for each arm: `length_cm`, `circ_proximal_cm` (the widest girth,
~1/3 down from the elbow) and `circ_distal_cm` (just above the wrist bones). Measure relaxed, arm
supported, palm up.

## Session order

1. Settle for 2 minutes with the electrodes on.
2. Sequential mode: record the **right** arm first (it becomes the reference), then move the
   electrodes to the mirrored marks on the left. Use the same `--session` name for both.
3. Follow the prompts. Rest tasks matter: they measure the noise floor that is subtracted from
   everything else.
4. Repeat the right arm once on a test day to see how repeatable a "100%" is. That spread is the
   smallest left-vs-right difference you can trust.

## Checking signal quality

- At rest the envelope should be flat and small (a few µV after filtering). Large rest values mean
  poor contact, a cable moving, or mains interference.
- In the dashboard, a clean contraction looks like a dense, symmetric burst. A periodic 50 Hz
  pattern that doesn't change with effort is interference: re-seat the reference electrode.
