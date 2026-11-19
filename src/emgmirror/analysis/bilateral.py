"""Left-vs-right analysis with the right (healthy) arm as the reference.

Everything is expressed as "left as a percentage of right" for the same muscle
group, same task and the same electrode placement, so skin, electrode and
amplifier effects largely cancel. The output is plain JSON for the API, the
dashboard and the HTML report.
"""

from __future__ import annotations

import numpy as np

from ..config import muscles
from ..processing import features as F
from ..processing.filters import clean_for_analysis, envelope
from ..storage import Session
from . import motor_units

REFERENCE, TEST = "right", "left"
MU_TASK = "grip_low_hold"
RAMP_TASK = "grip_ramp"


def _segments(session: Session, cfg: dict):
    """Yield (arm, muscle, task, filtered_segment, force_segment, level) for every trial."""
    fs = session.fs
    for block in session.blocks:
        filt = clean_for_analysis(block.data, fs, cfg)
        for c, ch in enumerate(block.channels):
            for t in block.trials:
                a, b = int(t.start_s * fs), int(t.end_s * fs)
                force = block.force[a:b] if block.force is not None else None
                yield ch.arm, ch.muscle, t, filt[a:b, c], force


def _core(x: np.ndarray, kind_frac: float = 0.6) -> np.ndarray:
    n = len(x)
    cut = int(n * (1 - kind_frac) / 2)
    return x[cut: n - cut] if n - 2 * cut > 10 else x


def _best_window(x: np.ndarray, fs: float, win_s: float = 1.0) -> np.ndarray:
    w = int(win_s * fs)
    if len(x) <= w:
        return x
    env = np.convolve(x**2, np.ones(w) / w, mode="valid")
    i = int(np.argmax(env))
    return x[i: i + w]


def _ratio(l, r):
    return float(l / r) if (l is not None and r) else None


def analyze(session: Session, cfg: dict) -> dict:
    fs = session.fs
    seg: dict = {}
    for arm, muscle, trial, x, force in _segments(session, cfg):
        seg.setdefault(arm, {}).setdefault(muscle, {})[trial.task] = (x, force, trial)

    arms = sorted(seg)
    groups = [m for m in muscles(cfg) if all(m in seg.get(a, {}) for a in arms)]
    tasks = [s["task"] for s in cfg["protocol"]]

    noise = {}
    for arm in arms:
        noise[arm] = {}
        for m in groups:
            rest = seg[arm][m].get("rest")
            if rest is not None:
                noise[arm][m] = F.rms(_core(rest[0], 0.8))
            else:
                anyx = next(iter(seg[arm][m].values()))[0]
                noise[arm][m] = float(np.percentile(envelope(anyx, fs), 5))

    feats: dict = {}
    for arm in arms:
        feats[arm] = {}
        for m in groups:
            feats[arm][m] = {}
            for task, (x, force, trial) in seg[arm][m].items():
                part = _best_window(x, fs) if task.endswith("_mvc") else (_core(x) if trial.level else x)
                f = F.segment_features(part, fs, noise[arm][m])
                if force is not None and len(force):
                    f["force_peak"] = float(np.max(force))
                feats[arm][m][task] = f

    result = {
        "session_id": session.session_id,
        "source": session.source,
        "is_synthetic": session.source == "synthetic",
        "fs": fs,
        "arms": arms,
        "muscles": groups,
        "tasks": tasks,
        "noise_mv": noise,
        "features": feats,
    }
    if not (REFERENCE in arms and TEST in arms):
        result["warning"] = "Both arms are needed for comparison; only per-arm features were computed."
        return result

    # Per task, per muscle symmetry.
    sym = {}
    for m in groups:
        sym[m] = {}
        for task in tasks:
            r, l = feats[REFERENCE][m].get(task), feats[TEST][m].get(task)
            if not r or not l or task == "rest":
                continue
            sym[m][task] = {
                "rms_ratio": _ratio(l["rms_net_mv"], r["rms_net_mv"]),
                "turns_ratio": _ratio(l["turns_per_s"], r["turns_per_s"]),
                "amp_per_turn_ratio": _ratio(l["amp_per_turn_mv"], r["amp_per_turn_mv"]),
                "median_freq_diff_hz": l["median_freq_hz"] - r["median_freq_hz"],
            }
    result["symmetry"] = sym

    # Activation index: each muscle's strongest task on the reference side.
    activation = {}
    for m in groups:
        mvc_tasks = [t for t in feats[REFERENCE][m] if t.endswith("_mvc") and t in feats[TEST][m]]
        if not mvc_tasks:
            continue
        best = max(mvc_tasks, key=lambda t: feats[REFERENCE][m][t]["rms_net_mv"])
        # A flat reference (net RMS 0: dead electrode, no contraction) leaves the ratio undefined: None, not a crash.
        p = _ratio(feats[TEST][m][best]["rms_net_mv"], feats[REFERENCE][m][best]["rms_net_mv"])
        activation[m] = {"task": best, "pct": 100 * p if p is not None else None}
    result["activation"] = activation

    # Recruitment curve on the slow ramp (net RMS per tenth of the ramp).
    recruitment = {}
    for m in groups:
        recruitment[m] = {}
        for arm in arms:
            item = seg[arm][m].get(RAMP_TASK)
            if item is None:
                continue
            x, _, trial = item
            bins = np.array_split(x, 10)
            recruitment[m][arm] = {
                "level": [float(trial.level * (i + 0.5) / 10) for i in range(10)],
                "rms_net_mv": [float(np.sqrt(max(np.mean(b**2) - noise[arm][m] ** 2, 0))) for b in bins],
            }
    result["recruitment"] = recruitment

    # Motor-unit activity on the light hold.
    mu = {}
    for m in groups:
        if MU_TASK not in seg[REFERENCE][m] or MU_TASK not in seg[TEST][m]:
            continue
        r = _core(seg[REFERENCE][m][MU_TASK][0], 0.8)
        l = _core(seg[TEST][m][MU_TASK][0], 0.8)
        mu[m] = motor_units.compare((r, noise[REFERENCE][m]), (l, noise[TEST][m]), fs)
    result["motor_units"] = mu

    # Force, if a dynamometer channel exists for both arms.
    force = {}
    for arm in arms:
        any_m = groups[0]
        f = {t: v.get("force_peak") for t, v in feats[arm][any_m].items() if v.get("force_peak") is not None}
        if f:
            force[arm] = f
    if force:
        result["force_peak"] = force

    # One headline number per muscle for the 3D view and report.
    summary = {}
    for m in groups:
        summary[m] = {
            "activation_pct": activation.get(m, {}).get("pct"),
            "mu_firing_pct": 100 * mu[m]["ratio_effective"] if m in mu and mu[m]["ratio_effective"] else None,
            "mu_identified_pct": 100 * mu[m]["ratio_identified"] if m in mu and mu[m]["ratio_identified"] else None,
        }
    result["summary"] = summary

    if session.ground_truth:
        result["ground_truth_check"] = _truth_check(session.ground_truth, summary, groups)
    return result


def _truth_check(truth: dict, summary: dict, groups: list[str]) -> dict:
    """Compare estimates with the simulator's known answer (synthetic only)."""
    out = {}
    for m in groups:
        r = truth["arms"][REFERENCE][m]
        l = truth["arms"][TEST][m]
        true_firing = 100 * l["tasks"][MU_TASK]["active_mu"] / max(r["tasks"][MU_TASK]["active_mu"], 1)
        out[m] = {
            "true_mu_firing_pct": true_firing,
            "true_mu_pool_pct": 100 * l["pool_alive"] / r["pool_alive"],
            "estimated_mu_firing_pct": summary[m]["mu_firing_pct"],
            "estimated_identified_pct": summary[m]["mu_identified_pct"],
        }
    return out
