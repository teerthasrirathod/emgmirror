# Validation against simulator ground truth

Command: `emgmirror validate --seeds 7 11 --out docs/validation_synthetic.json`
(default config: 1 kHz, 1 channel per muscle group, 120 motor units per healthy pool.)

The injury model was swept from healthy to severe. Each step sets the fraction of surviving motor
units, the enlargement of the survivors (reinnervation) and the drive cap. There are 2 seeds × 6
scenarios × 2 muscle groups = 24 comparisons.

| Metric | Compared with | Spearman ρ | Mean abs. error | Bias |
|---|---|---|---|---|
| Activation % (MVC amplitude ratio) | true surviving-unit pool % | 0.47 | 33 pts | **+33 pts** |
| Firing motor units % (cumulant estimator, light hold) | true firing-unit % | **0.84** | 19 pts | +16 pts |

## How to read this

- **Amplitude is not a motor-unit count.** With 35% of units left, the left flexors still reach about
  70% of right amplitude, because the surviving units are bigger. Activation % answers "how much
  electrical output can the muscle produce", which matters for function, but it hides denervation.
- **The firing-unit estimator tracks the real count much better** (ρ 0.84) but is noisy: ±20
  points on a single session, with a slight upward bias. Use it for **trends across sessions**
  (weekly recordings), not for a single absolute number.
- Extensors are less reliable than flexors in this protocol. During a light grip only a few
  extensor units fire, so the estimate rests on little data.

## What would improve it

1. More electrodes per muscle (or a high-density grid). Cumulant estimates can be averaged across
   channels, and real spike sorting becomes possible.
2. Longer light holds, or several of them: the estimator's variance falls with recording length.
3. A dedicated light-hold task per muscle group (e.g. a light wrist extension for the extensors).
4. Re-validate with the *real* noise floor and sampling rate once the kit arrives. Set
   `simulation.noise_uv_rms` to the resting RMS you measure, then re-run.
