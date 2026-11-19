"""Realistic recording chain: everything between the muscle and the saved number.

The simulator produces the *clean* surface EMG at a high internal rate. This
module adds what a hobby EMG kit adds, in the order it happens physically:

  skin/electrode     motion artefacts at movement transitions, electrode "pops",
                     slow baseline wander
  environment        mains interference (fundamental + odd harmonics, drifting amplitude)
  amplifier          white input noise, front-end low-pass (anti-alias filter)
  ADC                sampling at the device rate (aliasing if the filter is weak),
                     quantisation, clipping at the rails

Because the clean signal is kept, experiments can measure exactly how much of
the true EMG each processing pipeline recovers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.signal import butter, sosfilt, sosfiltfilt


@dataclass
class FrontEnd:
    fs_out: float = 1000.0
    amp_noise_uv: float = 4.0  # RMS, referred to the electrodes
    mains_uv: float = 15.0
    mains_hz: float = 50.0
    mains_drift: float = 0.3  # relative slow amplitude modulation
    wander_uv: float = 30.0
    motion_uv: float = 60.0  # artefact size per unit of drive change per second
    pops_per_min: float = 0.0
    hw_lowpass_hz: float = 450.0  # analog anti-alias filter of the board
    hw_order: int = 2
    adc_bits: int = 10
    adc_vref: float = 5.0
    gain: float = 1000.0

    @property
    def lsb_uv(self) -> float:
        return self.adc_vref / 2**self.adc_bits / self.gain * 1e6

    @property
    def range_mv(self) -> float:
        return self.adc_vref / 2 / self.gain * 1e3

    def as_dict(self) -> dict:
        return asdict(self) | {"lsb_uv": self.lsb_uv, "range_mv": self.range_mv}


def _lowfreq_noise(n: int, fs: float, cutoff: float, rng: np.random.Generator) -> np.ndarray:
    sos = butter(2, cutoff, fs=fs, output="sos")
    x = sosfiltfilt(sos, rng.standard_normal(n))
    return x / max(np.std(x), 1e-12)


def record(clean_mv: np.ndarray, fs_sim: float, fe: FrontEnd, drive: np.ndarray | None,
           rng: np.random.Generator, quantise: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Pass clean EMG (n,) or (n, ch) through the chain.

    Returns (recorded, clean_at_device_rate), both in mV at `fe.fs_out`.
    `drive` (n,) is the summed motor command; its rate of change sets motion artefacts.
    """
    x = np.atleast_2d(clean_mv.T).T.astype(float)
    n, n_ch = x.shape
    t = np.arange(n) / fs_sim
    noisy = x.copy()

    for c in range(n_ch):
        # Mains: each channel picks it up with its own phase and level.
        mod = 1 + fe.mains_drift * _lowfreq_noise(n, fs_sim, 0.2, rng)
        ph = rng.uniform(0, 2 * np.pi, 3)
        f0 = fe.mains_hz + rng.uniform(-0.05, 0.05)  # grid frequency is never exact
        mains = sum(a * np.sin(2 * np.pi * h * f0 * t + p)
                    for h, a, p in zip((1, 3, 5), (1.0, 0.3, 0.1), ph))
        noisy[:, c] += fe.mains_uv / 1000 * rng.uniform(0.6, 1.4) * mod * mains
        noisy[:, c] += fe.wander_uv / 1000 * _lowfreq_noise(n, fs_sim, 0.5, rng)
        if drive is not None and fe.motion_uv > 0:
            rate = np.abs(np.gradient(drive) * fs_sim)
            noisy[:, c] += fe.motion_uv / 1000 * rate * _lowfreq_noise(n, fs_sim, 12.0, rng)
        n_pops = rng.poisson(fe.pops_per_min * n / fs_sim / 60)
        for start in rng.integers(0, n, n_pops):
            tail = np.arange(n - start) / fs_sim
            noisy[start:, c] += rng.choice([-1, 1]) * rng.uniform(0.2, 1.0) * np.exp(-tail / 0.15)

    # Amplifier noise is white at the input, then shaped by the board's filter.
    noisy += fe.amp_noise_uv / 1000 * rng.standard_normal(noisy.shape) * np.sqrt(fs_sim / (2 * fe.hw_lowpass_hz))
    sos = butter(fe.hw_order, min(fe.hw_lowpass_hz, 0.45 * fs_sim), fs=fs_sim, output="sos")
    noisy = sosfilt(sos, noisy, axis=0)
    clean_f = sosfilt(sos, x, axis=0)

    step = int(round(fs_sim / fe.fs_out))
    noisy, clean_f = noisy[::step], clean_f[::step]  # the ADC just samples: aliasing is real

    if quantise:
        lsb = fe.lsb_uv / 1000
        noisy = np.clip(np.round(noisy / lsb) * lsb, -fe.range_mv, fe.range_mv)
    if clean_mv.ndim == 1:
        return noisy[:, 0], clean_f[:, 0]
    return noisy, clean_f
