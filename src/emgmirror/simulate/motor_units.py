"""Motor-unit level surface EMG simulator.

Recruitment and rate coding follow Fuglevand, Winter & Patla (1993), "Models of
recruitment and rate coding organization in motor-unit pools", J Neurophysiol
70(6). Each motor unit (MU) has a recruitment threshold, a firing-rate curve, a
twitch and a motor-unit action potential (MUAP). The surface signal is the sum
of every MUAP train, attenuated by depth and crosstalk, plus noise and mains.

The left-arm injury model removes a fraction of MUs (denervation), enlarges the
survivors (collateral reinnervation) and caps the descending drive. Because the
simulator knows every MU, it produces ground truth for validating the
estimators in `emgmirror.analysis`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import fftconvolve

# Fuglevand 1993 parameters (excitation normalised to 0..1)
RECRUIT_RANGE = 30.0  # ratio of largest to smallest recruitment threshold
RECRUIT_MAX = 0.6  # excitation at which the last MU is recruited
MIN_RATE = 8.0  # Hz at recruitment
RATE_GAIN = 40.0  # Hz per unit of excitation above threshold
PEAK_RATE_FIRST = 35.0  # Hz, earliest-recruited MU
PEAK_RATE_DROP = 10.0  # Hz, how much lower the last MU peaks
ISI_CV = 0.2
TWITCH_RANGE = 100.0
TWITCH_T_LONGEST = 0.090  # s
TWITCH_T_RANGE = 3.0

MUAP_GAIN_MV = 0.07  # MUAP peak for the smallest superficial MU; sets overall scale
MUAP_HALF_WINDOW_S = 0.010
CROSSTALK = 0.12


@dataclass
class MotorUnitPool:
    muscle: str
    rte: np.ndarray  # recruitment threshold excitation
    peak_rate: np.ndarray
    twitch_p: np.ndarray
    twitch_t: np.ndarray
    muap_amp: np.ndarray  # mV
    muap_width: np.ndarray  # s (Hermite scale)
    muap_mix: np.ndarray  # biphasic/triphasic blend
    muap_shift: np.ndarray  # sub-sample offset
    depth: np.ndarray  # 0 superficial .. 1 deep
    alive: np.ndarray  # bool
    drive_max: float = 1.0

    @property
    def n_total(self) -> int:
        return len(self.rte)

    @property
    def n_alive(self) -> int:
        return int(self.alive.sum())


def make_pool(muscle: str, n: int, seed: int, impairment: dict | None = None) -> MotorUnitPool:
    """Build a healthy pool, then optionally apply the injury model.

    Both arms use the same seed so their healthy anatomy is statistically
    identical and every left/right difference comes from `impairment` alone.
    """
    rng = np.random.default_rng(seed)
    i = np.arange(1, n + 1)
    rte = np.exp(np.log(RECRUIT_RANGE) * i / n) / RECRUIT_RANGE * RECRUIT_MAX
    peak_rate = PEAK_RATE_FIRST - PEAK_RATE_DROP * (rte / rte[-1])
    twitch_p = np.exp(np.log(TWITCH_RANGE) * i / n)
    c = np.log(TWITCH_RANGE) / np.log(TWITCH_T_RANGE)
    twitch_t = TWITCH_T_LONGEST * twitch_p ** (-1.0 / c)
    muap_amp = MUAP_GAIN_MV * np.sqrt(twitch_p) * rng.lognormal(0.0, 0.2, n)
    muap_width = rng.uniform(0.8e-3, 1.6e-3, n)
    muap_mix = rng.uniform(0.3, 1.0, n)
    muap_shift = rng.uniform(-0.5, 0.5, n)
    depth = rng.uniform(0.0, 1.0, n)
    alive = np.ones(n, dtype=bool)
    drive_max = 1.0

    if impairment:
        frac = float(impairment.get("mu_fraction", 1.0))
        alive = rng.random(n) < frac
        if not alive.any():
            alive[rng.integers(n)] = True
        grow = float(impairment.get("enlargement", 1.0))
        muap_amp = np.where(alive, muap_amp * grow, muap_amp)
        twitch_p = np.where(alive, twitch_p * grow, twitch_p)
        drive_max = float(impairment.get("drive_max", 1.0))

    return MotorUnitPool(muscle, rte, peak_rate, twitch_p, twitch_t, muap_amp, muap_width,
                         muap_mix, muap_shift, depth, alive, drive_max)


def firing_rates(pool: MotorUnitPool, excitation: np.ndarray) -> np.ndarray:
    """Instantaneous firing rate (n_units, n_samples) for an excitation trace."""
    e = np.clip(excitation, 0.0, 1.0)[None, :] * pool.drive_max
    above = e - pool.rte[:, None]
    rate = np.minimum(MIN_RATE + RATE_GAIN * above, pool.peak_rate[:, None])
    rate[above < 0] = 0.0
    rate[~pool.alive] = 0.0
    return rate


def spike_trains(rates: np.ndarray, fs: float, rng: np.random.Generator) -> list[np.ndarray]:
    """Sample indices for each MU by integrating rate and jittering each ISI."""
    trains = []
    n = rates.shape[1]
    for rate in rates:
        phase = np.cumsum(rate) / fs
        total = phase[-1]
        if total < 1.0:
            trains.append(np.empty(0, dtype=np.int64))
            continue
        k = int(total * 1.5) + 5
        steps = np.clip(1.0 + ISI_CV * rng.standard_normal(k), 0.4, 1.6)
        targets = np.cumsum(steps) + rng.uniform(-0.5, 0.5)
        targets = targets[(targets > 0) & (targets < total)]
        idx = np.searchsorted(phase, targets)
        trains.append(idx[idx < n].astype(np.int64))
    return trains


def muap_template(width: float, mix: float, shift: float, fs: float, spread: float = 1.0) -> np.ndarray:
    half = int(round(MUAP_HALF_WINDOW_S * fs))
    t = (np.arange(-half, half + 1) - shift) / fs
    x = t / (width * spread)
    g = np.exp(-(x**2))
    hr1 = x * g
    hr2 = (1 - 2 * x**2) * g
    s = mix * hr1 / np.abs(hr1).max() + (1 - mix) * hr2 / np.abs(hr2).max()
    return s / max(np.abs(s).max(), 1e-12)


def add_muap_trains(signal: np.ndarray, pool: MotorUnitPool, trains: list[np.ndarray],
                    fs: float, same_muscle: bool, crosstalk: float = CROSSTALK,
                    depth_offset: float = 0.0) -> None:
    """Add every MU's train to one channel in place.

    `same_muscle` is the pool under the electrode; otherwise the pool reaches
    the channel as crosstalk, scaled by `crosstalk` and spatially low-passed.
    `depth_offset` pushes a whole muscle deeper (e.g. supinator, pronators).
    """
    n = len(signal)
    for u in np.flatnonzero(pool.alive):
        idx = trains[u]
        if len(idx) == 0:
            continue
        d = pool.depth[u] + depth_offset
        if same_muscle:
            gain, spread = np.exp(-2.0 * d), 1.0 + 0.6 * d
        else:
            gain, spread = crosstalk * np.exp(-d), 1.6 + 0.6 * d
        tpl = pool.muap_amp[u] * gain * muap_template(pool.muap_width[u], pool.muap_mix[u],
                                                       pool.muap_shift[u], fs, spread)
        half = len(tpl) // 2
        pos = idx[:, None] + np.arange(-half, half + 1)[None, :]
        ok = (pos >= 0) & (pos < n)
        np.add.at(signal, pos[ok], np.broadcast_to(tpl, pos.shape)[ok])


def twitch_force(pool: MotorUnitPool, trains: list[np.ndarray], n: int, fs: float,
                 n_bins: int = 12) -> np.ndarray:
    """Sum of twitches. Units with similar contraction time share one convolution."""
    edges = np.geomspace(pool.twitch_t.min(), pool.twitch_t.max() * 1.0001, n_bins + 1)
    which = np.clip(np.searchsorted(edges, pool.twitch_t, side="right") - 1, 0, n_bins - 1)
    force = np.zeros(n)
    for b in range(n_bins):
        imp = np.zeros(n)
        members = [u for u in np.flatnonzero(pool.alive & (which == b)) if len(trains[u])]
        if not members:
            continue
        for u in members:
            np.add.at(imp, trains[u], pool.twitch_p[u])
        T = np.sqrt(edges[b] * edges[b + 1])
        t = np.arange(int(5 * T * fs)) / fs
        force += fftconvolve(imp, (t / T) * np.exp(1 - t / T))[:n]
    return force


def noise(n: int, fs: float, rng: np.random.Generator, noise_uv: float, mains_uv: float,
          mains_hz: float) -> np.ndarray:
    t = np.arange(n) / fs
    white = rng.standard_normal(n) * noise_uv / 1000.0
    ph = rng.uniform(0, 2 * np.pi, 2)
    mains = mains_uv / 1000.0 * (np.sin(2 * np.pi * mains_hz * t + ph[0])
                                 + 0.3 * np.sin(2 * np.pi * 3 * mains_hz * t + ph[1]))
    wander = 0.03 * np.sin(2 * np.pi * rng.uniform(0.1, 0.4) * t + rng.uniform(0, 6.28))
    return white + mains + wander
