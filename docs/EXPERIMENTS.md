# Synthetic experiments: what to expect before the sensors arrive

```bash
emgmirror experiment all                    # quick preset, about 2 minutes on 2 cores
emgmirror experiment all --preset full      # bigger pools, longer trials, more repeats (8+ cores)
emgmirror calibrate train --n 300 --jobs 2  # calibration engine (use --jobs = your core count)
```

The results (JSON plus `report.html`) go to `results/experiments/`. Every number below comes from
the simulator: 5 forearm muscle groups, a motor-unit model, a realistic recording chain (motion
artefacts, mains hum, amplifier noise, the board's analog filter, a 10-bit ADC at gain 1000). The
default left-arm injury model is in `configs/default.yaml`. Treat these numbers as hypotheses to
check on the first real recording, not as facts about anyone's arm.

## 1. Effort: what signal to expect

| Movement | Lowest effort with SNR ≥ 6 dB | Right max net RMS | Peak ADC counts at max | Left/right at 20% | Left/right at 100% |
|---|---|---|---|---|---|
| power grip | 5% | 344 µV | 436 / 512 | 71% | 68% |
| wrist flexion | 5% | 339 µV | 304 | 71% | 68% |
| wrist extension | 5% | 339 µV | 300 | 78% | 72% |
| pronation (isometric) | 5% | 219 µV | 192 | 84% | 75% |
| supination (isometric) | 10% | 123 µV | 203 | 72% | 74% |
| hammer curl (brachioradialis) | 5% | 244 µV | 259 | 94% | 93% |

- Below about 2% effort no motor unit is recruited yet; from 5% upward the signal clears the
  noise easily with a reasonable setup.
- At gain 1000, a strong grip peaks near **85% of the 10-bit ADC range**. A real forearm can be
  stronger than the model. **Check for clipping on day 1** and drop the gain if peaks hit ±512
  counts.
- Units recruited at power grip (ground truth): about 16 at 5%, 28 at 10%, 47 at 30%, and the whole
  pool by ~60%. Above that, force grows only through faster firing (8 → 30 Hz).
- The supinator is deep, so a surface electrode over it mostly hears the extensors. Rotation
  strength can't be judged from one surface channel.

## 2. Movements: which muscles fire, and where to put the electrodes

Seventeen movements and exercises were simulated at 60% effort: isometric tasks, rotations, putty
squeeze, wrist curls, tendon glides, finger spreading. One virtual electrode sat over each muscle
group.

| Electrodes | Best set | Movements told apart |
|---|---|---|
| 1 | extensors | 44% |
| 2 | extensors + brachioradialis | 75% |
| 2 (current plan) | flexors + extensors | 65% |
| 3 | flexors + extensors + brachioradialis | 82% |
| 4–5 | + supinators (+ pronators) | 84% |

(Chance = 6%.) **For the left/right deficit question, flexors + extensors stays the right 2-channel
choice**: the flexors carry the grip deficit. If the kit gives a third channel per arm, put it on
**brachioradialis**. A fourth adds little. Pronation shows up on the flexor electrode, because the
pronator teres sits next to it, more than on its own.

## 3. Hand rotation

Same 50% grip, with electrodes placed palm-up:

| Forearm angle | +90 (palm up) | +30 | 0 (thumb up) | −30 | −60 | −90 (palm down) |
|---|---|---|---|---|---|---|
| Flexor signal | 100% | 96% | 91% | 83% | 74% | 62% |
| Extensor signal | 100% | 101% | 100% | 97% | 94% | 90% |

**Rotation alone can move the flexor reading by up to 38%, as large as the injury effect.** Rule:
record both arms in the same posture (palm up, supported) for every comparison task. Run the
rotation tasks as a separate block and compare them only with the same task on the other arm.

## 4. Noise: which cleaning pipeline to use

Mean error of the recovered EMG amplitude against the true clean signal:

| Condition | raw | band-pass | band-pass + notch | band-pass + mains regression |
|---|---|---|---|---|
| clean lab | 2% | 0% | 15% | 2% |
| typical home | 3% | 5% | 15% | **2%** |
| laptop charging (150 µV mains) | 161% | 162% | 15% | **5%** |
| poor contact (motion, pops) | 39% | 42% | 15% | **2%** |
| low gain / coarse ADC (20 µV LSB) | 3% | 5% | 15% | **2%** |
| 500 Hz sampling, weak anti-alias filter | 12% | 5% | 12% | **2%** |

- **The classic notch costs a constant ~15% of the EMG and shifts the median frequency by −16 Hz**:
  it cuts 9 notches, at 50, 100, … 450 Hz. Left/right *ratios* survive, because both sides lose the
  same share, but absolute values and spectra don't.
- **Fitting and subtracting the hum as sinusoids** ("mains regression") works in every condition.
  It is now the default for offline analysis (`processing.mains_removal: regression`). The live
  view still uses a causal notch.
- Noise-floor subtraction (rest power taken off) is what keeps low-effort readings accurate. The
  rest trial at the start of every session matters.

## 5. How long to hold

| Hold | 1 s | 2 s | 3 s | 5 s | 8 s | 12 s | 20 s |
|---|---|---|---|---|---|---|---|
| Activation L/R, spread (SD) | 3.1 | 3.0 | 1.9 | 1.9 | 1.1 | 1.0 | 1.3 pts |
| Firing units L/R, spread (SD) | 36 | 21 | 16 | 16 | 8 | 6 | 6 pts |

Amplitude ratios settle after ~3 s. **The motor-unit estimate needs ≥ 12 s of steady light
grip**, so the light hold in the default protocol is now 15 s.

## 6. Calibration engine (matching synthetic to real)

The engine is trained on simulated recordings with random hidden settings and scored on recordings
it never saw.

**Single-arm engine** (300 training recordings, flexor channel only):

| Hidden setting | R² | Usable? |
|---|---|---|
| amplifier noise | 0.95 | yes: the sim-to-real noise match is reliable |
| surviving motor units | 0.49 | roughly |
| electrode coupling | 0.24 | no |
| unit enlargement, drive cap | < 0.1 | no: not identifiable from one channel |

**Paired engine** (right arm as reference; 200 healthy/injured pairs, electrode coupling drawn
independently per arm):

| Left ÷ right motor units | Error (MAE) | Correlation with truth |
|---|---|---|
| **paired engine** | **9.9 pts** | **0.86** |
| cumulant estimator | 19.3 pts | 0.66 |
| MVC amplitude ratio | 57 pts | ≈ 0 |

Using the right arm as the reference is what makes the motor-unit question answerable: shared
anatomy and hardware cancel out. Drive deficit also becomes partly measurable (R² 0.42); unit
enlargement still doesn't (R² 0.20). **Amplitude ratios mean nothing if electrode coupling differs
between arms.** In this test it varied from 0.5× to 2×, so careful mirrored placement remains
essential for the simple activation %.

See `report.html` for the plots and the "Sensor day" steps below.

## Sensor day: matching the simulator to real data

1. Record the standard protocol on the right arm. Use `configs/extended_protocol.yaml` if you also
   want rotations and exercises.
2. `emgmirror calibrate predict <session>` writes `calibration.json` in the session folder:
   - `frontend_from_rest`: the real noise floor and mains level per channel. Put these into
     `simulation.noise_uv_rms` / `mains_uv` so the simulator's chain matches your hardware.
   - per-arm, per-muscle estimates of the hidden settings, with 80% ranges.
3. Retrain the engine with the matched front-end (`emgmirror calibrate train`), then predict
   again. Record the left arm and compare.
4. Compare real against simulated feature values. Large gaps show where the model of the forearm
   is wrong (e.g. drive weights per movement in `movements.py`). Fix the model there, not the
   data.
