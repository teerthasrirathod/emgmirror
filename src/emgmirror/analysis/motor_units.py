"""Motor-unit activity estimation from a few surface channels (experimental).

Surface EMG cannot count neurons directly. These estimators answer a narrower
question: "relative to the right arm, how many motor units are *firing* in the
left arm during the same light task?" Run `emgmirror validate` to see how well
each one tracks the simulator's ground truth before trusting it.

Why plain amplitude is not enough: after nerve injury, surviving motor units
adopt orphaned muscle fibres and grow. Fewer-but-bigger units can give an EMG
amplitude close to normal, so the RMS ratio overestimates the unit count.

1. Effective firing units (cumulant method). For a sum of independent MUAP
   trains (shot noise), Campbell's theorem gives the cumulants
   k2 = sum(r_i * E2_i) and k4 = sum(r_i * E4_i). The amplitude scale cancels in
   k2^2 / k4, which is therefore proportional to the number of firing units
   times their rate, weighted towards the larger units. Gaussian noise adds to
   k2 but not to k4, so it is removed exactly by subtracting the resting power.
   Works best at light effort, where individual MUAPs are not fully fused.

2. Decomposition (spike sorting). Peaks above the noise are clustered (PCA +
   Gaussian mixture, size chosen by BIC); clusters with a physiological firing
   rate count as identified units. With 1-2 channels per muscle this usually
   finds only a few units; it becomes meaningful with high-density electrode
   grids.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture

MAX_UNITS = 8
MAX_WAVES = 2000
MIN_RATE_HZ = 4.0
MAX_RATE_HZ = 40.0


def effective_units(x: np.ndarray, fs: float, noise_rms: float, window_s: float = 1.0) -> float | None:
    """Median over windows of (k2 - noise^2)^2 / k4, in arbitrary units."""
    w = int(window_s * fs)
    vals = []
    for i in range(0, len(x) - w + 1, w):
        seg = x[i: i + w] - x[i: i + w].mean()
        m2 = np.mean(seg**2)
        k4 = np.mean(seg**4) - 3 * m2**2
        k2 = m2 - noise_rms**2
        if k4 > 0 and k2 > 0:
            vals.append(k2**2 / k4)
    return float(np.median(vals)) if vals else None


def decompose(x: np.ndarray, fs: float, noise_rms: float, k: float = 4.0, seed: int = 0) -> dict:
    """Spike-sort one band-passed channel. Returns identified units."""
    half = max(int(round(0.005 * fs)), 3)
    dur = len(x) / fs
    thr = max(k * noise_rms, 1e-6)
    peaks, _ = find_peaks(np.abs(x), height=thr, distance=max(int(0.003 * fs), 1))
    peaks = peaks[(peaks >= half) & (peaks < len(x) - half)]
    result = {"n_units": 0, "units": [], "n_spikes": int(len(peaks))}
    if len(peaks) < 10:
        return result

    waves = np.stack([x[p - half: p + half + 1] for p in peaks])
    rng = np.random.default_rng(seed)
    fit_idx = rng.choice(len(waves), min(MAX_WAVES, len(waves)), replace=False)
    pca = PCA(n_components=min(4, waves.shape[1]), random_state=seed).fit(waves[fit_idx])
    feats = pca.transform(waves)

    best, best_bic = None, np.inf
    for n in range(1, min(MAX_UNITS, len(fit_idx) // 10) + 1):
        gm = GaussianMixture(n, init_params="k-means++", random_state=seed, reg_covar=1e-6).fit(feats[fit_idx])
        bic = gm.bic(feats[fit_idx])
        if bic < best_bic:
            best, best_bic = gm, bic
    labels = best.predict(feats)

    for lab in np.unique(labels):
        idx = peaks[labels == lab]
        rate = len(idx) / dur
        if not (MIN_RATE_HZ <= rate <= MAX_RATE_HZ):
            continue
        tpl = waves[labels == lab].mean(axis=0)
        result["units"].append({"rate_hz": float(rate), "p2p_mv": float(tpl.max() - tpl.min()),
                                "n_spikes": int(len(idx))})
    result["n_units"] = len(result["units"])
    return result


def compare(right: tuple, left: tuple, fs: float) -> dict:
    """Each side is (band-passed light-hold signal, resting noise RMS)."""
    out = {}
    for arm, (x, nz) in (("right", right), ("left", left)):
        dec = decompose(x, fs, nz)
        out[arm] = {
            "effective_units": effective_units(x, fs, nz),
            "identified_units": dec["n_units"],
            "identified_mean_p2p_mv": float(np.mean([u["p2p_mv"] for u in dec["units"]])) if dec["units"] else None,
        }
    r, l = out["right"], out["left"]
    out["ratio_effective"] = (l["effective_units"] / r["effective_units"]
                              if r["effective_units"] and l["effective_units"] is not None else None)
    out["ratio_identified"] = (l["identified_units"] / r["identified_units"]
                               if r["identified_units"] else None)
    return out
