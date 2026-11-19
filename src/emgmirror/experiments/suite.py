"""Synthetic experiments that prepare for the first real recordings.

Each `run_*` function returns plain JSON-able results. `emgmirror experiment`
saves them under `results/experiments/` and builds an HTML report.

effort      What signal (mV, ADC counts, SNR) and how many motor units to expect
            from 2% to 100% effort, per movement, healthy vs injured arm.
movements   Which muscle groups each movement and physio exercise drives, and
            which 2/3/4 electrode positions best tell the movements apart.
rotation    How much forearm rotation (pronation/supination) changes the
            signal under fixed electrodes.
noise       Which cleaning pipeline recovers the true EMG best under different
            noise conditions, and the lowest effort that is still measurable.
duration    How long each hold must be for a stable left/right ratio.
"""

from __future__ import annotations

import itertools

import numpy as np
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis

from ..analysis.motor_units import effective_units
from ..processing.features import spectrum_stats
from ..processing.filters import PIPELINES, clean, pipeline
from ..simulate.frontend import FrontEnd, record
from ..simulate.movements import GROUPS, MOVEMENTS, profiles, simulate, simulate_movement

FS_SIM = 2000.0
EDGE_S = 0.5


def _impairment(cfg: dict) -> dict:
    return cfg["simulation"]["left_impairment"]


def _frontend(cfg: dict, **kw) -> FrontEnd:
    a, sim = cfg["acquisition"], cfg["simulation"]
    base = dict(fs_out=a["fs"], amp_noise_uv=sim["noise_uv_rms"], mains_uv=sim["mains_uv"], mains_hz=a["mains_hz"],
                adc_bits=a["adc_bits"], adc_vref=a["adc_vref"], gain=a["amp_gain"])
    return FrontEnd(**(base | kw))


def _core(x: np.ndarray, fs: float) -> np.ndarray:
    k = int(EDGE_S * fs)
    return x[k:-k] if len(x) > 3 * k else x


def _rms(x, axis=0):
    return np.sqrt(np.mean(np.square(x), axis=axis))


def _band(fs: float):
    return (20.0, min(450.0, 0.45 * fs))


def _rest_noise(fe: FrontEnd, n_ch: int, seconds: float, rng, pipe="bandpass_notch") -> np.ndarray:
    n = int(seconds * FS_SIM)
    rec, _ = record(np.zeros((n, n_ch)), FS_SIM, fe, np.zeros(n), rng)
    return _rms(_core(pipeline(rec, fe.fs_out, pipe, fe.mains_hz), fe.fs_out))


def _net(rms, noise):
    return np.sqrt(np.maximum(np.square(rms) - np.square(noise), 0.0))


# ----------------------------------------------------------------------------- effort

EFFORT_MOVES = ["power_grip", "wrist_flexion", "wrist_extension", "pronation_hold", "supination_hold", "hammer_curl"]
EFFORTS = [0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0]


def run_effort(cfg: dict, seed: int = 7, duration_s: float = 4.0, mu: int = 120, progress=print) -> dict:
    fe = _frontend(cfg)
    rng = np.random.default_rng(seed)
    rows = []
    for arm, imp in (("right", None), ("left", _impairment(cfg))):
        noise = _rest_noise(fe, len(GROUPS), 4.0, rng)
        for name in EFFORT_MOVES:
            mv = MOVEMENTS[name]
            target = max(mv.drive, key=mv.drive.get)
            ci = GROUPS.index(target)
            for eff in EFFORTS:
                sim = simulate_movement(name, eff, duration_s, FS_SIM, seed, imp, mu, arm_seed=int(eff * 1000))
                rec, cl = record(sim["clean"], FS_SIM, fe, sim["drive_sum"], rng)
                fs = fe.fs_out
                p_rec = _core(pipeline(rec, fs, "bandpass_notch", fe.mains_hz), fs)
                p_cl = _core(pipeline(cl, fs, "bandpass_notch", fe.mains_hz), fs)
                sig, resid = _rms(p_cl[:, ci]), _rms(p_rec[:, ci] - p_cl[:, ci])
                t = sim["truth"][target]
                rows.append({
                    "arm": arm, "movement": name, "target": target, "effort": eff,
                    "net_rms_mv": float(_net(_rms(p_rec[:, ci]), noise[ci])),
                    "true_rms_mv": float(sig),
                    "snr_db": float(20 * np.log10(max(sig, 1e-9) / max(resid, 1e-9))),
                    "adc_peak_counts": float(np.max(np.abs(rec[:, ci])) / (fe.lsb_uv / 1000)),
                    "clipped_pct": float(100 * np.mean(np.abs(rec[:, ci]) >= fe.range_mv * 0.999)),
                    "active_mu": t["active_mu"], "pool_healthy": t["pool_healthy"],
                    "active_pct_of_healthy": 100 * t["active_mu"] / t["pool_healthy"],
                    "mean_rate_hz": t["mean_rate_hz"],
                    "median_freq_hz": spectrum_stats(p_rec[:, ci], fs)["median_freq_hz"],
                    "all_channels_net_mv": dict(zip(GROUPS, _net(_rms(p_rec), noise).round(4).tolist())),
                })
            progress(f"effort: {arm} {name} done")

    summary = {}
    for name in EFFORT_MOVES:
        for arm in ("right", "left"):
            rr = [r for r in rows if r["movement"] == name and r["arm"] == arm]
            ok = [r["effort"] for r in rr if r["snr_db"] >= 6]
            summary.setdefault(name, {})[arm] = {
                "min_effort_snr6db": min(ok) if ok else None,
                "mvc_net_rms_mv": rr[-1]["net_rms_mv"],
                "mvc_adc_peak_counts": rr[-1]["adc_peak_counts"],
            }
        r_r = {r["effort"]: r for r in rows if r["movement"] == name and r["arm"] == "right"}
        summary[name]["left_pct_of_right_by_effort"] = {
            str(r["effort"]): 100 * r["net_rms_mv"] / r_r[r["effort"]]["net_rms_mv"] if r_r[r["effort"]]["net_rms_mv"] else None
            for r in rows if r["movement"] == name and r["arm"] == "left"}
    return {"rows": rows, "summary": summary, "frontend": fe.as_dict(), "efforts": EFFORTS}


# ----------------------------------------------------------------------------- movements

def _window_features(x: np.ndarray, fs: float, win_s=0.25, step_s=0.125) -> np.ndarray:
    w, s = int(win_s * fs), int(step_s * fs)
    out = []
    for i in range(0, len(x) - w + 1, s):
        seg = x[i:i + w]
        rms = _rms(seg)
        wl = np.mean(np.abs(np.diff(seg, axis=0)), axis=0)
        zc = np.mean(np.diff(np.signbit(seg).astype(int), axis=0) != 0, axis=0)
        out.append(np.concatenate([np.log(rms + 1e-6), np.log(wl + 1e-6), zc]))
    return np.array(out)


def run_movements(cfg: dict, seed: int = 7, effort: float = 0.6, duration_s: float = 6.0, mu: int = 100,
                  progress=print) -> dict:
    fe = _frontend(cfg)
    rng = np.random.default_rng(seed + 1)
    names = list(MOVEMENTS)
    noise = _rest_noise(fe, len(GROUPS), 4.0, rng)
    activation, truth, feats = {}, {}, {}
    for name in names:
        per_trial = []
        for trial in (0, 1):
            sim = simulate_movement(name, effort, duration_s, FS_SIM, seed, None, mu, arm_seed=100 + trial)
            rec, _ = record(sim["clean"], FS_SIM, fe, sim["drive_sum"], rng)
            p = pipeline(rec, fe.fs_out, "bandpass_notch", fe.mains_hz)
            per_trial.append(p)
            if trial == 0:
                activation[name] = dict(zip(GROUPS, _net(_rms(_core(p, fe.fs_out)), noise).round(4).tolist()))
                truth[name] = {g: round(sim["truth"][g]["active_pct_of_alive"], 1) for g in GROUPS}
        feats[name] = [_window_features(_core(p, fe.fs_out), fe.fs_out) for p in per_trial]
        progress(f"movements: {name} done")

    # Which electrode subsets separate the movements? Train on trial 0, test on trial 1 and back.
    n_ch = len(GROUPS)

    def accuracy(chans):
        cols = [k * n_ch + c for k in range(3) for c in chans]
        scores = []
        for tr, te in ((0, 1), (1, 0)):
            Xtr = np.vstack([feats[m][tr][:, cols] for m in names])
            ytr = np.concatenate([[i] * len(feats[m][tr]) for i, m in enumerate(names)])
            Xte = np.vstack([feats[m][te][:, cols] for m in names])
            yte = np.concatenate([[i] * len(feats[m][te]) for i, m in enumerate(names)])
            clf = LinearDiscriminantAnalysis().fit(Xtr, ytr)
            scores.append(float(np.mean(clf.predict(Xte) == yte)))
        return float(np.mean(scores))

    subsets = []
    for k in range(1, n_ch + 1):
        for chans in itertools.combinations(range(n_ch), k):
            subsets.append({"channels": [GROUPS[c] for c in chans], "n": k, "accuracy": accuracy(chans)})
    best = {k: max((s for s in subsets if s["n"] == k), key=lambda s: s["accuracy"]) for k in range(1, n_ch + 1)}
    default_2 = next(s for s in subsets if s["channels"] == ["flexors", "extensors"])
    return {
        "movements": {n: {"category": MOVEMENTS[n].category, "description": MOVEMENTS[n].description} for n in names},
        "groups": GROUPS, "effort": effort,
        "activation_net_mv": activation, "truth_active_pct": truth,
        "subsets": subsets, "best_by_count": {str(k): v for k, v in best.items()},
        "default_flexor_extensor_accuracy": default_2["accuracy"],
        "chance": 1 / len(names),
    }


# ----------------------------------------------------------------------------- rotation

ANGLES = [-90, -60, -30, 0, 30, 60, 90]


def run_rotation(cfg: dict, seed: int = 7, effort: float = 0.5, duration_s: float = 4.0, mu: int = 100,
                 progress=print) -> dict:
    fe = _frontend(cfg)
    rng = np.random.default_rng(seed + 2)
    noise = _rest_noise(fe, len(GROUPS), 4.0, rng)
    mv = MOVEMENTS["power_grip"]
    out = {}
    for ang in ANGLES:
        drives, _ = profiles(mv, effort, duration_s, FS_SIM)
        angle = np.full(len(next(iter(drives.values()))), float(ang))
        sim = simulate(drives, angle, FS_SIM, seed, None, mu, arm_seed=7)  # same spikes at every angle
        rec, _ = record(sim["clean"], FS_SIM, fe, sim["drive_sum"], rng)
        out[ang] = _net(_rms(_core(pipeline(rec, fe.fs_out, "bandpass_notch", fe.mains_hz), fe.fs_out)), noise)
    ref = out[90]
    rel = {str(a): dict(zip(GROUPS, (100 * v / np.maximum(ref, 1e-9)).round(1).tolist())) for a, v in out.items()}
    progress("rotation: angle sweep done")

    # Dynamic screwdriver rotation, for the trace figure.
    sim = simulate_movement("pro_sup_rotation", 0.6, 8.0, FS_SIM, seed, None, mu, arm_seed=9)
    rec, _ = record(sim["clean"], FS_SIM, fe, sim["drive_sum"], rng)
    fs = fe.fs_out
    p = pipeline(rec, fs, "bandpass_notch", fe.mains_hz)
    w = int(0.1 * fs)
    env = np.column_stack([np.sqrt(np.convolve(p[:, c] ** 2, np.ones(w) / w, "same")) for c in range(p.shape[1])])
    step = int(0.02 * fs)
    progress("rotation: dynamic trace done")
    return {
        "angles": ANGLES, "reference_angle": 90, "effort": effort, "relative_pct": rel,
        "trace": {"t": (np.arange(0, len(env), step) / fs).round(3).tolist(),
                  "angle": sim["angle"][:: int(FS_SIM / fs)][::step].round(1).tolist(),
                  "envelope": {g: env[::step, i].round(4).tolist() for i, g in enumerate(GROUPS)}},
    }


# ----------------------------------------------------------------------------- noise

CONDITIONS = {
    "clean_lab": dict(amp_noise_uv=2.0, mains_uv=5.0, motion_uv=20.0),
    "typical_home": dict(),
    "laptop_charging": dict(mains_uv=150.0, mains_drift=0.5),
    "poor_contact": dict(amp_noise_uv=12.0, mains_uv=60.0, motion_uv=250.0, pops_per_min=3.0, wander_uv=200.0),
    "low_gain_coarse_adc": dict(gain=250.0),
    "500Hz_weak_antialias": dict(fs_out=500.0, hw_lowpass_hz=1000.0, hw_order=1),
}
NOISE_EFFORTS = [0.03, 0.05, 0.1, 0.2, 0.5, 1.0]  # >= first recruitment threshold


def run_noise(cfg: dict, seed: int = 7, duration_s: float = 4.0, mu: int = 120, repeats: int = 2,
              progress=print) -> dict:
    clean_sims = {}
    for eff in NOISE_EFFORTS:
        sims = []
        for r in range(repeats):
            drives, angle = profiles(MOVEMENTS["power_grip"], eff, duration_s, FS_SIM)
            drives = {"flexors": drives["flexors"], "extensors": drives["extensors"]}
            sims.append(simulate(drives, angle, FS_SIM, seed, None, mu, groups=["flexors", "extensors"],
                                 arm_seed=50 + r))
        clean_sims[eff] = sims
    progress("noise: clean signals simulated")

    rows = []
    for cond, kw in CONDITIONS.items():
        fe = _frontend(cfg, **kw)
        fs = fe.fs_out
        rng = np.random.default_rng(seed + 3)
        n_rest = int(4.0 * FS_SIM)
        rest_rec, _ = record(np.zeros(n_rest), FS_SIM, fe, np.zeros(n_rest), rng)
        rest = {p: _rms(_core(pipeline(rest_rec, fs, p, fe.mains_hz), fs)) for p in PIPELINES}
        for eff, sims in clean_sims.items():
            for r, sim in enumerate(sims):
                rec, cl = record(sim["clean"][:, 0], FS_SIM, fe, sim["drive_sum"], rng)
                true = _core(clean(cl, fs, _band(fs), None), fs)
                true_rms, true_mf = _rms(true), spectrum_stats(true, fs)["median_freq_hz"]
                for p in PIPELINES:
                    y = _core(pipeline(rec, fs, p, fe.mains_hz), fs)
                    est = _net(_rms(y), rest[p])
                    rows.append({"condition": cond, "pipeline": p, "effort": eff, "repeat": r,
                                 "true_rms_mv": float(true_rms), "est_rms_mv": float(est),
                                 "rms_error_pct": float(100 * (est - true_rms) / true_rms),
                                 "median_freq_error_hz": float(spectrum_stats(y, fs)["median_freq_hz"] - true_mf),
                                 "rest_rms_uv": float(1000 * rest[p])})
        progress(f"noise: {cond} done")

    table = {}
    for cond in CONDITIONS:
        table[cond] = {}
        for p in PIPELINES:
            by_eff = {}
            for eff in NOISE_EFFORTS:
                errs = [abs(r["rms_error_pct"]) for r in rows if r["condition"] == cond and r["pipeline"] == p and r["effort"] == eff]
                by_eff[str(eff)] = float(np.mean(errs))
            reliable = [e for e in NOISE_EFFORTS if by_eff[str(e)] <= 15.0]
            rest_uv = next(r["rest_rms_uv"] for r in rows if r["condition"] == cond and r["pipeline"] == p)
            table[cond][p] = {"abs_error_pct_by_effort": by_eff, "min_reliable_effort": min(reliable) if reliable else None,
                              "mean_abs_error_pct": float(np.mean(list(by_eff.values()))), "rest_rms_uv": rest_uv}
        best = min(PIPELINES, key=lambda p: table[cond][p]["mean_abs_error_pct"])
        table[cond]["best_pipeline"] = best
    return {"conditions": {k: _frontend(cfg, **v).as_dict() for k, v in CONDITIONS.items()},
            "pipelines": PIPELINES, "efforts": NOISE_EFFORTS, "table": table, "rows": rows}


# ----------------------------------------------------------------------------- duration

DURATIONS = [1, 2, 3, 5, 8, 12, 20]


def run_duration(cfg: dict, seed: int = 7, repeats: int = 6, mu: int = 120, progress=print) -> dict:
    fe = _frontend(cfg)
    rng = np.random.default_rng(seed + 4)
    imp = _impairment(cfg)
    longest = max(DURATIONS) + 2 * EDGE_S
    groups = ["flexors", "extensors"]
    act = {d: [] for d in DURATIONS}
    mu_ratio = {d: [] for d in DURATIONS}
    rms_cv = {d: [] for d in DURATIONS}
    noise = _rest_noise(fe, 1, 4.0, rng)[0]
    for r in range(repeats):
        sig = {}
        for arm, im in (("right", None), ("left", imp)):
            for task, eff in (("mvc", 1.0), ("light", 0.15)):
                drives, angle = profiles(MOVEMENTS["power_grip"], eff, longest, FS_SIM)
                drives = {g: drives[g] for g in groups}
                sim = simulate(drives, angle, FS_SIM, seed, im, mu, groups=groups, arm_seed=1000 * r + len(sig))
                rec, _ = record(sim["clean"][:, 0], FS_SIM, fe, sim["drive_sum"], rng)
                sig[(arm, task)] = _core(pipeline(rec, fe.fs_out, "bandpass_notch", fe.mains_hz), fe.fs_out)
        fs = fe.fs_out
        for d in DURATIONS:
            n = int(d * fs)
            seg = {k: v[:n] for k, v in sig.items()}
            act[d].append(100 * _net(_rms(seg[("left", "mvc")]), noise) / _net(_rms(seg[("right", "mvc")]), noise))
            l = effective_units(seg[("left", "light")], fs, noise, window_s=min(1.0, d))
            rr = effective_units(seg[("right", "light")], fs, noise, window_s=min(1.0, d))
            mu_ratio[d].append(100 * l / rr if (l and rr) else np.nan)
            rms_cv[d].append(_rms(seg[("right", "light")]))
        progress(f"duration: repeat {r + 1}/{repeats} done")

    def stats(v):
        v = np.array(v, float)
        v = v[np.isfinite(v)]
        return {"mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else None, "n": int(len(v))}

    return {
        "durations_s": DURATIONS, "repeats": repeats,
        "activation_pct": {str(d): stats(act[d]) for d in DURATIONS},
        "mu_firing_pct": {str(d): stats(mu_ratio[d]) for d in DURATIONS},
        "light_hold_rms_cv_pct": {str(d): 100 * float(np.std(rms_cv[d], ddof=1) / np.mean(rms_cv[d])) for d in DURATIONS},
    }


EXPERIMENTS = {"effort": run_effort, "movements": run_movements, "rotation": run_rotation,
               "noise": run_noise, "duration": run_duration}

# Presets: "quick" fits a 2-core laptop in a few minutes; "full" is for a bigger machine.
PRESETS = {
    "quick": {"effort": dict(mu=120, duration_s=3.0), "movements": dict(mu=80, duration_s=4.0),
              "rotation": dict(mu=80), "noise": dict(mu=120, repeats=1), "duration": dict(mu=120, repeats=4)},
    "full": {"effort": dict(mu=120, duration_s=5.0), "movements": dict(mu=120, duration_s=8.0),
             "rotation": dict(mu=120), "noise": dict(mu=120, repeats=5), "duration": dict(mu=120, repeats=20)},
}
