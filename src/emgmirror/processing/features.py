"""Standard sEMG features on a band-passed segment (1-D, millivolts)."""

from __future__ import annotations

import numpy as np
from scipy.signal import welch


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def mav(x: np.ndarray) -> float:
    return float(np.mean(np.abs(x)))


def waveform_length(x: np.ndarray) -> float:
    return float(np.sum(np.abs(np.diff(x))))


def spectrum_stats(x: np.ndarray, fs: float) -> dict[str, float]:
    f, p = welch(x, fs=fs, nperseg=min(len(x), int(fs // 4) or 1))
    total = p.sum()
    if total <= 0:
        return {"median_freq_hz": 0.0, "mean_freq_hz": 0.0}
    cum = np.cumsum(p)
    return {
        "median_freq_hz": float(f[np.searchsorted(cum, total / 2)]),
        "mean_freq_hz": float((f * p).sum() / total),
    }


def turns_amplitude(x: np.ndarray, fs: float, threshold_mv: float) -> dict[str, float]:
    """Willison-style turns/amplitude analysis.

    A *turn* is a local extremum that differs from the previous accepted turn by
    at least `threshold_mv`. Neurogenic patterns (fewer, larger motor units)
    give fewer turns per second with a larger mean amplitude per turn.
    """
    d = np.diff(x)
    ext = np.flatnonzero(np.sign(d[1:]) != np.sign(d[:-1])) + 1
    if len(ext) == 0:
        return {"turns_per_s": 0.0, "amp_per_turn_mv": 0.0}
    accepted, amps = [ext[0]], []
    for i in ext[1:]:
        step = abs(x[i] - x[accepted[-1]])
        if step >= threshold_mv:
            amps.append(step)
            accepted.append(i)
    dur = len(x) / fs
    return {
        "turns_per_s": len(amps) / dur,
        "amp_per_turn_mv": float(np.mean(amps)) if amps else 0.0,
    }


def segment_features(x: np.ndarray, fs: float, noise_rms: float) -> dict[str, float]:
    out = {"rms_mv": rms(x), "mav_mv": mav(x), "wl_mv_per_s": waveform_length(x) / (len(x) / fs)}
    # Noise-corrected RMS: remove the resting floor in power.
    out["rms_net_mv"] = float(np.sqrt(max(out["rms_mv"] ** 2 - noise_rms**2, 0.0)))
    out.update(spectrum_stats(x, fs))
    out.update(turns_amplitude(x, fs, threshold_mv=max(5 * noise_rms, 0.02)))
    return out
