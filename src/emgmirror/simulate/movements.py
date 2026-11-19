"""Movement library and a five-group forearm simulator.

Extends the two-group protocol simulator to the forearm muscles that matter
for hand rotation and rehabilitation exercises:

  flexors          FCR, PL, FDS, FDP, FCU     grip, wrist flexion
  extensors        ECRL/B, EDC, ECU          wrist/finger extension, grip stabilisation
  brachioradialis  BR                        elbow flexion in neutral, rotation to neutral
  pronators        pronator teres (deep-ish) pronation
  supinators       supinator (deep)          supination (biceps also supinates, but sits above the elbow)

Each group has one virtual electrode. Every electrode also hears the other
groups through crosstalk, which grows when the forearm rotates away from the
posture the electrodes were placed in (skin and muscle slide under the
electrodes during pronation/supination).

Drive weights are approximate and taken from typical surface/fine-wire EMG
patterns. They are a starting point to be recalibrated against real
recordings (see `emgmirror.calibration`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .motor_units import add_muap_trains, firing_rates, make_pool, spike_trains

GROUPS = ["flexors", "extensors", "brachioradialis", "pronators", "supinators"]
POOL_SIZE = {"flexors": 1.0, "extensors": 0.85, "brachioradialis": 0.5, "pronators": 0.5, "supinators": 0.4}
DEPTH_OFFSET = {"flexors": 0.0, "extensors": 0.0, "brachioradialis": 0.0, "pronators": 0.35, "supinators": 0.7}

_XT = {
    ("flexors", "extensors"): 0.06, ("flexors", "brachioradialis"): 0.12, ("flexors", "pronators"): 0.35,
    ("flexors", "supinators"): 0.03, ("extensors", "brachioradialis"): 0.30, ("extensors", "pronators"): 0.04,
    ("extensors", "supinators"): 0.25, ("brachioradialis", "pronators"): 0.15,
    ("brachioradialis", "supinators"): 0.20, ("pronators", "supinators"): 0.05,
}


def crosstalk(electrode: str, source: str) -> float:
    return _XT.get((electrode, source)) or _XT.get((source, electrode)) or 0.02


def coupling(angle_deg: np.ndarray, ref_deg: float, own: bool) -> np.ndarray:
    """Electrode-to-muscle coupling vs forearm rotation (+90 supinated .. -90 pronated)."""
    d = np.abs(np.asarray(angle_deg, float) - ref_deg) / 180.0
    return 1 - 0.4 * d**2 if own else 1 + 0.8 * d


@dataclass(frozen=True)
class Movement:
    name: str
    category: str  # isometric | rotation | exercise
    description: str
    drive: dict[str, float]  # group -> weight at full effort
    angle_deg: float = 90.0  # forearm posture while doing it
    pattern: str = "hold"  # hold | reps | alternate | sine
    period_s: float = 2.0
    alt_drive: dict[str, float] = field(default_factory=dict)  # second phase for "alternate"
    alt_angle_deg: float | None = None


def _d(f=0.0, e=0.0, b=0.0, p=0.0, s=0.0):
    return {"flexors": f, "extensors": e, "brachioradialis": b, "pronators": p, "supinators": s}


MOVEMENTS: dict[str, Movement] = {m.name: m for m in [
    Movement("rest", "isometric", "Forearm relaxed, supported, palm up.", _d()),
    Movement("power_grip", "isometric", "Squeeze a ball or dynamometer.", _d(1.0, 0.5, 0.3, 0.2, 0.15)),
    Movement("key_pinch", "isometric", "Press thumb onto the side of the index finger.", _d(0.6, 0.35, 0.1, 0.1, 0.1)),
    Movement("wrist_flexion", "isometric", "Bend the wrist toward the palm against resistance.", _d(1.0, 0.1, 0.1, 0.2, 0.05)),
    Movement("wrist_extension", "isometric", "Bend the wrist back against resistance.", _d(0.15, 1.0, 0.3, 0.05, 0.2)),
    Movement("radial_deviation", "isometric", "Tilt the hand toward the thumb against resistance.", _d(0.5, 0.6, 0.4, 0.1, 0.1), 0.0),
    Movement("ulnar_deviation", "isometric", "Tilt the hand toward the little finger against resistance.", _d(0.5, 0.6, 0.05, 0.05, 0.1), 0.0),
    Movement("finger_extension", "isometric", "Straighten the fingers against a rubber band.", _d(0.1, 0.8, 0.1, 0.05, 0.1)),
    Movement("hammer_curl", "isometric", "Bend the elbow with the thumb up, holding a weight.", _d(0.2, 0.2, 1.0, 0.15, 0.1), 0.0),
    Movement("pronation_hold", "rotation", "Turn the palm down against resistance (isometric).", _d(0.35, 0.1, 0.3, 1.0, 0.05), 0.0),
    Movement("supination_hold", "rotation", "Turn the palm up against resistance (isometric).", _d(0.05, 0.25, 0.3, 0.05, 1.0), 0.0),
    Movement("pro_sup_rotation", "rotation", "Turn palm down, then up, once every 2 s (screwdriver).",
             _d(0.3, 0.1, 0.25, 1.0, 0.05), 0.0, "alternate", 2.0, _d(0.05, 0.25, 0.25, 0.05, 1.0), 0.0),
    Movement("putty_squeeze", "exercise", "Squeeze therapy putty and release, once every 2 s.", _d(1.0, 0.45, 0.25, 0.15, 0.1), 90.0, "reps", 2.0),
    Movement("wrist_curls", "exercise", "Palm up, curl a light weight up and down (4 s per rep).", _d(1.0, 0.15, 0.1, 0.15, 0.05), 90.0, "sine", 4.0),
    Movement("reverse_wrist_curls", "exercise", "Palm down, lift the back of the hand up and down (4 s per rep).", _d(0.15, 1.0, 0.35, 0.05, 0.2), -90.0, "sine", 4.0),
    Movement("tendon_glides", "exercise", "Cycle straight hand -> hook fist -> full fist (1 s each).",
             _d(0.6, 0.35, 0.05, 0.05, 0.05), 90.0, "alternate", 2.0, _d(0.15, 0.6, 0.05, 0.05, 0.05)),
    Movement("finger_spread_band", "exercise", "Open the fingers against a rubber band, once every 2 s.", _d(0.1, 0.7, 0.05, 0.05, 0.1), 90.0, "reps", 2.0),
]}


def profiles(mv: Movement, effort: float, duration_s: float, fs: float):
    """Per-group excitation traces and the posture trace for one trial."""
    n = int(duration_s * fs)
    t = np.arange(n) / fs
    edge = np.clip(np.minimum(t, duration_s - t) / 0.3, 0, 1)  # 0.3 s soft on/off
    if mv.pattern == "hold":
        phase_a = np.ones(n)
        phase_b = np.zeros(n)
    elif mv.pattern == "reps":
        phase_a = ((t % mv.period_s) < mv.period_s / 2).astype(float)
        phase_b = np.zeros(n)
    elif mv.pattern == "sine":
        phase_a = 0.5 - 0.5 * np.cos(2 * np.pi * t / mv.period_s)
        phase_b = np.zeros(n)
    elif mv.pattern == "alternate":
        phase_a = ((t % mv.period_s) < mv.period_s / 2).astype(float)
        phase_b = 1 - phase_a
    else:
        raise ValueError(mv.pattern)
    # Smooth square waves a little: real contractions take ~100 ms to build.
    k = max(int(0.1 * fs), 1)
    kern = np.ones(k) / k
    phase_a, phase_b = np.convolve(phase_a, kern, "same"), np.convolve(phase_b, kern, "same")
    drives = {g: effort * edge * (mv.drive.get(g, 0) * phase_a + mv.alt_drive.get(g, 0) * phase_b)
              for g in GROUPS}
    if mv.alt_angle_deg is None or mv.pattern != "alternate":
        angle = np.full(n, mv.angle_deg)
    else:
        # Rotation: move between full pronation and supination.
        angle = np.where(phase_a > 0.5, -80.0, 80.0)
        angle = np.convolve(angle, np.ones(int(0.4 * fs)) / int(0.4 * fs), "same")
    return drives, angle


def simulate(drives: dict[str, np.ndarray], angle: np.ndarray, fs: float, seed: int,
             impairment: dict | None = None, mu_per_muscle: int = 120, ref_angle: float = 90.0,
             groups=GROUPS, arm_seed: int = 0) -> dict:
    """Clean EMG for one virtual electrode per group, plus motor-unit ground truth.

    `impairment` maps group -> {mu_fraction, enlargement, drive_max}. Healthy
    anatomy is seeded by `seed` only, so both arms share it.
    """
    n = len(angle)
    rng = np.random.default_rng(seed * 7919 + arm_seed)
    pools, trains = {}, {}
    for gi, g in enumerate(groups):
        pools[g] = make_pool(g, max(int(mu_per_muscle * POOL_SIZE[g]), 10), seed + 31 * gi,
                             (impairment or {}).get(g))
        rates = firing_rates(pools[g], drives[g])
        trains[g] = spike_trains(rates, fs, rng)

    clean = np.zeros((n, len(groups)))
    for ci, electrode in enumerate(groups):
        for source in groups:
            contrib = np.zeros(n)
            own = source == electrode
            add_muap_trains(contrib, pools[source], trains[source], fs, same_muscle=own,
                            crosstalk=crosstalk(electrode, source), depth_offset=DEPTH_OFFSET[source])
            clean[:, ci] += contrib * coupling(angle, ref_angle, own)

    dur = n / fs
    truth = {}
    for g in groups:
        counts = np.array([len(tr) for tr in trains[g]])
        active = counts >= 2
        truth[g] = {
            "pool_healthy": pools[g].n_total,
            "pool_alive": pools[g].n_alive,
            "active_mu": int(active.sum()),
            "active_pct_of_alive": 100 * active.sum() / max(pools[g].n_alive, 1),
            "mean_rate_hz": float(counts[active].mean() / dur) if active.any() else 0.0,
            "largest_active_rank": int(np.flatnonzero(active).max() + 1) if active.any() else 0,
        }
    return {"clean": clean, "groups": list(groups), "truth": truth, "fs": fs,
            "drive_sum": sum(drives[g] for g in groups)}


def simulate_movement(name: str, effort: float, duration_s: float, fs: float, seed: int,
                      impairment: dict | None = None, mu_per_muscle: int = 120, arm_seed: int = 0) -> dict:
    mv = MOVEMENTS[name]
    drives, angle = profiles(mv, effort, duration_s, fs)
    out = simulate(drives, angle, fs, seed, impairment, mu_per_muscle, arm_seed=arm_seed)
    out["angle"] = angle
    out["drives"] = drives
    return out
