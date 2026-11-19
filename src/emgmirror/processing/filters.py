"""Filtering for offline analysis (zero-phase) and live streaming (causal)."""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, sosfiltfilt, tf2sos


def design(fs: float, band_hz=(20.0, 450.0), mains_hz: float | None = 50.0) -> np.ndarray:
    """Band-pass plus mains notches (fundamental and harmonics inside the band)."""
    lo, hi = band_hz
    hi = min(hi, 0.45 * fs)
    sections = [butter(4, [lo, hi], btype="bandpass", fs=fs, output="sos")]
    if mains_hz:
        f = mains_hz
        while f < hi:
            b, a = iirnotch(f, Q=30.0, fs=fs)
            sections.append(tf2sos(b, a))
            f += mains_hz
    return np.vstack(sections)


def clean(x: np.ndarray, fs: float, band_hz=(20.0, 450.0), mains_hz: float | None = 50.0) -> np.ndarray:
    """Zero-phase filter along axis 0. Works on (n,) or (n, channels)."""
    return sosfiltfilt(design(fs, band_hz, mains_hz), x, axis=0)


def envelope(x: np.ndarray, fs: float, window_s: float = 0.1) -> np.ndarray:
    """Moving RMS envelope of an already band-passed signal."""
    w = max(int(round(window_s * fs)), 1)
    kernel = np.ones(w) / w
    sq = np.asarray(x) ** 2
    if sq.ndim == 1:
        return np.sqrt(np.convolve(sq, kernel, mode="same"))
    return np.sqrt(np.column_stack([np.convolve(sq[:, c], kernel, mode="same") for c in range(sq.shape[1])]))


class StreamingFilter:
    """Causal filter + RMS envelope that keeps state across chunks."""

    def __init__(self, fs: float, n_channels: int, band_hz=(20.0, 450.0), mains_hz=50.0, window_s=0.1):
        self.sos = design(fs, band_hz, mains_hz)
        self.zi = np.repeat(sosfilt_zi(self.sos)[:, :, None], n_channels, axis=2) * 0.0
        self.win = max(int(round(window_s * fs)), 1)
        self.buf = np.zeros((self.win, n_channels))
        self.n = n_channels

    def process(self, chunk: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (filtered chunk, envelope at the end of the chunk per channel)."""
        y, self.zi = sosfilt(self.sos, chunk, axis=0, zi=self.zi)
        self.buf = np.vstack([self.buf, y])[-self.win:]
        return y, np.sqrt(np.mean(self.buf**2, axis=0))


def remove_mains_regression(x: np.ndarray, fs: float, mains_hz: float = 50.0, block_s: float = 0.5,
                            max_hz: float | None = None) -> np.ndarray:
    """Subtract mains as fitted sinusoids (fundamental + harmonics) in short blocks.

    Unlike a notch, this removes only the phase-locked interference and leaves
    the EMG power that happens to sit at 50/100/150 Hz. The fit is redone per
    block, so slow drifts in mains amplitude and phase are followed.
    """
    x = np.asarray(x, float)
    two_d = x.ndim == 2
    y = x.reshape(len(x), -1).copy()
    top = max_hz or 0.45 * fs
    harmonics = np.arange(1, int(top // mains_hz) + 1) * mains_hz
    w = max(int(block_s * fs), 8)
    for i in range(0, len(y), w):
        seg = slice(i, min(i + w, len(y)))
        t = np.arange(seg.start, seg.stop) / fs
        basis = np.column_stack([f(2 * np.pi * h * t) for h in harmonics for f in (np.sin, np.cos)])
        coef, *_ = np.linalg.lstsq(basis, y[seg], rcond=None)
        y[seg] -= basis @ coef
    return y if two_d else y[:, 0]


def clean_for_analysis(x: np.ndarray, fs: float, cfg: dict) -> np.ndarray:
    """Offline cleaning as configured in `processing` (band-pass + mains removal)."""
    p = cfg["processing"]
    band = tuple(p["band_hz"])
    mains = cfg["acquisition"]["mains_hz"] if p.get("notch", True) else None
    if mains and p.get("mains_removal", "notch") == "regression":
        return remove_mains_regression(clean(x, fs, band, None), fs, mains, max_hz=min(band[1], 0.45 * fs))
    return clean(x, fs, band, mains)


def pipeline(x: np.ndarray, fs: float, name: str, mains_hz: float = 50.0) -> np.ndarray:
    """Named cleaning pipelines compared in the noise experiments."""
    hi = min(450.0, 0.45 * fs)
    if name == "raw":
        return x - np.mean(x, axis=0)
    if name == "bandpass":
        return clean(x, fs, (20, hi), None)
    if name == "bandpass_notch":
        return clean(x, fs, (20, hi), mains_hz)
    if name == "bandpass30_notch":
        return clean(x, fs, (30, hi), mains_hz)
    if name == "bandpass_regression":
        return remove_mains_regression(clean(x, fs, (20, hi), None), fs, mains_hz)
    raise ValueError(f"unknown pipeline {name}")


PIPELINES = ["raw", "bandpass", "bandpass_notch", "bandpass30_notch", "bandpass_regression"]
