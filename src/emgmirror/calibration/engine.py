"""Simulation-based calibration: fit the simulator to a real recording.

Idea: the simulator has a handful of hidden settings per arm and muscle (how many
motor units survive, how much they grew, how much drive arrives, how well the
electrode couples, how noisy the chain is). We simulate many recordings with
random settings, summarise each with features that are robust to recording
length, and train a model that maps features -> settings. On a real recording
the same features give the settings back, with an uncertainty range. This is
amortised simulation-based inference.

Flow on sensor day:
  1. `frontend_from_rest`: measure the real noise floor and mains level from
     the rest trial, and set the simulator's front-end to match.
  2. `train`: build the bank with that front-end (or use the generic one).
  3. `predict`: estimate the settings for each arm/muscle; the left/right
     ratio of `mu_fraction` is a model-based answer to "how many motor units".
  4. Re-check: holdout accuracy tells you which settings are identifiable at
     all with your electrode count; don't read the others.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks
from scipy.stats import kurtosis
from sklearn.ensemble import ExtraTreesRegressor

from ..analysis.motor_units import effective_units
from ..processing.features import spectrum_stats, turns_amplitude
from ..processing.filters import clean, pipeline, remove_mains_regression
from ..simulate.frontend import FrontEnd, record
from ..simulate.movements import MOVEMENTS, profiles, simulate

FS_SIM = 2000.0
TASKS = {"rest": (0.0, 4.0), "grip_mvc": (1.0, 4.0), "grip_ramp": (0.6, 10.0), "grip_low_hold": (0.15, 12.0)}
PARAMS = ["mu_fraction", "enlargement", "drive_max", "log_gain", "amp_noise_uv"]
PRIOR = {"mu_fraction": (0.15, 1.0), "enlargement": (1.0, 2.2), "drive_max": (0.6, 1.0),
         "log_gain": (np.log(0.5), np.log(2.0)), "amp_noise_uv": (2.0, 12.0), "mains_uv": (0.0, 100.0)}


# ----------------------------------------------------------------------------- features

def _rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


def _best_window(x, fs, win_s=1.0):
    w = int(win_s * fs)
    if len(x) <= w:
        return x
    p = np.convolve(x**2, np.ones(w) / w, "valid")
    i = int(np.argmax(p))
    return x[i:i + w]


def _trim(x, fs, s=0.5):
    k = int(s * fs)
    return x[k:-k] if len(x) > 3 * k else x


def features(seg: dict[str, np.ndarray], fs: float) -> dict[str, float]:
    """Length-robust summary of one muscle channel. `seg` maps task -> cleaned signal."""
    noise = _rms(_trim(seg["rest"], fs))
    mvc = _best_window(seg["grip_mvc"], fs)
    mvc_net = np.sqrt(max(_rms(mvc) ** 2 - noise**2, 1e-12))
    light = _trim(seg["grip_low_hold"], fs)
    light_net = np.sqrt(max(_rms(light) ** 2 - noise**2, 1e-12))
    f = {
        "log_rest_uv": np.log(1000 * noise),
        "log_mvc_mv": np.log(mvc_net),
        "light_over_mvc": light_net / mvc_net,
        "log_light_units": np.log(effective_units(light, fs, noise) or 1e-6),
        "light_kurtosis": float(kurtosis(light)),
        "mvc_kurtosis": float(kurtosis(mvc)),
        "mf_mvc_hz": spectrum_stats(mvc, fs)["median_freq_hz"],
        "mf_light_hz": spectrum_stats(light, fs)["median_freq_hz"],
    }
    ta = turns_amplitude(light, fs, threshold_mv=max(5 * noise, 0.02))
    f["light_turns_per_s"] = ta["turns_per_s"]
    f["light_amp_per_turn_rel"] = ta["amp_per_turn_mv"] / max(light_net, 1e-9)
    peaks, props = find_peaks(np.abs(light), height=5 * noise, distance=max(int(0.003 * fs), 1))
    f["light_spikes_per_s"] = len(peaks) / (len(light) / fs)
    f["light_spike_amp_rel"] = float(np.median(props["peak_heights"])) / max(light_net, 1e-9) if len(peaks) else 0.0
    ramp = seg["grip_ramp"]
    for i, b in enumerate(np.array_split(ramp, 5)):
        f[f"ramp{i}"] = np.sqrt(max(_rms(b) ** 2 - noise**2, 0.0)) / mvc_net
    return {k: float(v) for k, v in f.items()}


# ----------------------------------------------------------------------------- bank

def _sample_params(rng) -> dict:
    p = {k: float(rng.uniform(*v)) for k, v in PRIOR.items()}
    p["seed"] = int(rng.integers(1, 10_000))
    return p


def simulate_one(params: dict, base_fe: dict, mu: int = 120, arm_seed: int = 0) -> dict:
    fe = FrontEnd(**(base_fe | {"amp_noise_uv": params["amp_noise_uv"], "mains_uv": params["mains_uv"]}))
    imp = {"flexors": {k: params[k] for k in ("mu_fraction", "enlargement", "drive_max")}}
    rng = np.random.default_rng(params["seed"] * 31 + arm_seed)
    seg = {}
    for task, (level, dur) in TASKS.items():
        mv = MOVEMENTS["power_grip"]
        if task == "grip_ramp":
            n = int(dur * FS_SIM)
            drives = {"flexors": np.linspace(0, level, n)}
            angle = np.full(n, 90.0)
        else:
            d, angle = profiles(mv, level, dur, FS_SIM)
            drives = {"flexors": d["flexors"]}
        sim = simulate(drives, angle, FS_SIM, params["seed"], imp, mu, groups=["flexors"], arm_seed=arm_seed + len(seg))
        rec, _ = record(sim["clean"][:, 0] * np.exp(params["log_gain"]), FS_SIM, fe, sim["drive_sum"], rng)
        seg[task] = pipeline(rec, fe.fs_out, "bandpass_regression", fe.mains_hz)
    return features(seg, fe.fs_out)


def _job(args):
    params, base_fe, mu = args
    return params, simulate_one(params, base_fe, mu)


def build_bank(n: int, base_fe: dict, seed: int = 0, jobs: int = 1, mu: int = 120, progress=print) -> list[dict]:
    rng = np.random.default_rng(seed)
    todo = [(_sample_params(rng), base_fe, mu) for _ in range(n)]
    out = []
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as ex:
            for i, (p, f) in enumerate(ex.map(_job, todo, chunksize=4)):
                out.append({"params": p, "features": f})
                if (i + 1) % 25 == 0:
                    progress(f"bank: {i + 1}/{n}")
    else:
        for i, t in enumerate(todo):
            p, f = _job(t)
            out.append({"params": p, "features": f})
            if (i + 1) % 25 == 0:
                progress(f"bank: {i + 1}/{n}")
    return out


# ----------------------------------------------------------------------------- model

class Calibrator:
    def __init__(self, feature_names: list[str]):
        self.feature_names = feature_names
        self.model = ExtraTreesRegressor(n_estimators=300, min_samples_leaf=2, random_state=0, n_jobs=1)
        self.report: dict = {}

    def _X(self, feats: list[dict]) -> np.ndarray:
        return np.array([[f[k] for k in self.feature_names] for f in feats])

    @staticmethod
    def _Y(params: list[dict]) -> np.ndarray:
        return np.array([[p[k] for k in PARAMS] for p in params])

    def fit(self, bank: list[dict]):
        self.model.fit(self._X([b["features"] for b in bank]), self._Y([b["params"] for b in bank]))
        return self

    def predict(self, feats: list[dict], q=(10, 90)) -> list[dict]:
        X = self._X(feats)
        per_tree = np.stack([t.predict(X) for t in self.model.estimators_])  # (trees, n, params)
        mean = per_tree.mean(0)
        lo, hi = np.percentile(per_tree, q[0], 0), np.percentile(per_tree, q[1], 0)
        out = []
        for i in range(len(X)):
            d = {k: {"estimate": float(mean[i, j]), "low": float(lo[i, j]), "high": float(hi[i, j])}
                 for j, k in enumerate(PARAMS)}
            g = d.pop("log_gain")
            d["electrode_gain"] = {k: float(np.exp(v)) for k, v in g.items()}
            out.append(d)
        return out

    def evaluate(self, holdout: list[dict]) -> dict:
        preds = self.predict([b["features"] for b in holdout])
        Y = self._Y([b["params"] for b in holdout])
        rep = {}
        for j, k in enumerate(PARAMS):
            key = "electrode_gain" if k == "log_gain" else k
            est = np.array([p[key]["estimate"] for p in preds])
            lo = np.array([p[key]["low"] for p in preds])
            hi = np.array([p[key]["high"] for p in preds])
            truth = np.exp(Y[:, j]) if k == "log_gain" else Y[:, j]
            ss = np.sum((truth - truth.mean()) ** 2)
            rep[key] = {"r2": float(1 - np.sum((truth - est) ** 2) / ss),
                        "mae": float(np.mean(np.abs(truth - est))),
                        "coverage_80": float(np.mean((truth >= lo) & (truth <= hi)))}
        self.report = rep
        return rep

    def save(self, path: str | Path):
        import joblib

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"feature_names": self.feature_names, "model": self.model, "report": self.report}, path)

    @classmethod
    def load(cls, path: str | Path) -> "Calibrator":
        import joblib

        d = joblib.load(path)
        c = cls(d["feature_names"])
        c.model, c.report = d["model"], d["report"]
        return c


def train(base_fe: dict, n: int = 300, n_holdout: int = 60, jobs: int = 1, seed: int = 0, progress=print):
    bank = build_bank(n + n_holdout, base_fe, seed, jobs, progress=progress)
    names = sorted(bank[0]["features"])
    cal = Calibrator(names).fit(bank[:n])
    cal.evaluate(bank[n:])
    return cal, bank


def paired_test(cal: Calibrator, base_fe: dict, n_pairs: int = 12, seed: int = 99, progress=print) -> dict:
    """Healthy right + injured left with the same anatomy: does the engine recover the L/R unit ratio?"""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_pairs):
        left = _sample_params(rng)
        right = dict(left, mu_fraction=1.0, enlargement=1.0, drive_max=1.0,
                     log_gain=float(rng.uniform(*PRIOR["log_gain"])))
        fl, fr = simulate_one(left, base_fe, arm_seed=1), simulate_one(right, base_fe, arm_seed=2)
        pl, pr = cal.predict([fl, fr])
        rows.append({
            "true_ratio_pct": 100 * left["mu_fraction"],
            "engine_ratio_pct": 100 * pl["mu_fraction"]["estimate"] / pr["mu_fraction"]["estimate"],
            "cumulant_ratio_pct": 100 * float(np.exp(fl["log_light_units"] - fr["log_light_units"])),
            "amplitude_ratio_pct": 100 * float(np.exp(fl["log_mvc_mv"] - fr["log_mvc_mv"])),
        })
        progress(f"paired: {i + 1}/{n_pairs}")
    t = np.array([r["true_ratio_pct"] for r in rows])
    score = {}
    for k in ("engine_ratio_pct", "cumulant_ratio_pct", "amplitude_ratio_pct"):
        e = np.array([r[k] for r in rows])
        score[k] = {"mae_pct_points": float(np.mean(np.abs(e - t))),
                    "corr": float(np.corrcoef(e, t)[0, 1])}
    return {"rows": rows, "scores": score}


# ----------------------------------------------------------------------------- real data

def frontend_from_rest(rest_raw_mv: np.ndarray, fs: float, mains_hz: float = 50.0) -> dict:
    """Measure the recording chain from a relaxed trial (raw, unfiltered mV)."""
    x = rest_raw_mv - np.mean(rest_raw_mv)
    bp = clean(x, fs, (20, min(450, 0.45 * fs)), None)
    no_mains = remove_mains_regression(bp, fs, mains_hz)
    hum = bp - no_mains
    steps = np.diff(np.unique(np.round(rest_raw_mv, 9)))
    return {
        "amp_noise_uv": float(1000 * np.std(_trim(no_mains, fs))),
        "mains_uv": float(1000 * np.sqrt(2) * np.std(_trim(hum, fs))),
        "lsb_uv_observed": float(1000 * np.min(steps)) if len(steps) else None,
    }


def session_features(session, cfg: dict) -> dict:
    """Features per (arm, muscle) from a session recorded with the standard protocol."""
    from ..processing.filters import clean_for_analysis

    out = {}
    for block in session.blocks:
        filt = clean_for_analysis(block.data, session.fs, cfg)
        for c, ch in enumerate(block.channels):
            seg = {}
            for t in block.trials:
                if t.task in TASKS:
                    a, b = int(t.start_s * session.fs), int(t.end_s * session.fs)
                    seg[t.task] = filt[a:b, c]
            if set(seg) == set(TASKS):
                out[f"{ch.arm}/{ch.muscle}"] = features(seg, session.fs)
    return out


def save_json(obj, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------- paired (right arm as reference)

PAIRED_TARGETS = ["mu_fraction", "enlargement", "drive_max"]


def _pair_job(args):
    left, right, base_fe, mu = args
    return simulate_one(left, base_fe, mu, arm_seed=1), simulate_one(right, base_fe, mu, arm_seed=2)


def _sample_pair(rng) -> tuple[dict, dict]:
    left = _sample_params(rng)
    right = dict(left, mu_fraction=1.0, enlargement=1.0, drive_max=1.0,
                 log_gain=float(rng.uniform(*PRIOR["log_gain"])))
    return left, right


def build_pairs(n: int, base_fe: dict, seed: int = 0, jobs: int = 1, mu: int = 120, progress=print) -> list[dict]:
    """Healthy right + injured left with shared anatomy and hardware, independent electrode coupling."""
    rng = np.random.default_rng(seed)
    todo = [(*_sample_pair(rng), base_fe, mu) for _ in range(n)]
    runner = ProcessPoolExecutor(jobs).map(_pair_job, todo, chunksize=2) if jobs > 1 else map(_pair_job, todo)
    out = []
    for i, ((fl, fr), t) in enumerate(zip(runner, todo)):
        out.append({"left": t[0], "right": t[1], "f_left": fl, "f_right": fr})
        if (i + 1) % 25 == 0:
            progress(f"pairs: {i + 1}/{n}")
    return out


class PairedCalibrator(Calibrator):
    """Predicts the left arm's hidden settings from left features and left-minus-right differences."""

    def _Xp(self, pairs: list[tuple[dict, dict]]) -> np.ndarray:
        return np.array([[fl[k] for k in self.feature_names] + [fl[k] - fr[k] for k in self.feature_names]
                         for fl, fr in pairs])

    def fit_pairs(self, bank: list[dict]):
        X = self._Xp([(b["f_left"], b["f_right"]) for b in bank])
        Y = np.array([[b["left"][k] for k in PAIRED_TARGETS] for b in bank])
        self.model.fit(X, Y)
        return self

    def predict_pairs(self, pairs: list[tuple[dict, dict]], q=(10, 90)) -> list[dict]:
        X = self._Xp(pairs)
        per_tree = np.stack([t.predict(X) for t in self.model.estimators_])
        mean, lo, hi = per_tree.mean(0), np.percentile(per_tree, q[0], 0), np.percentile(per_tree, q[1], 0)
        return [{k: {"estimate": float(mean[i, j]), "low": float(lo[i, j]), "high": float(hi[i, j])}
                 for j, k in enumerate(PAIRED_TARGETS)} for i in range(len(X))]

    def evaluate_pairs(self, holdout: list[dict]) -> dict:
        preds = self.predict_pairs([(b["f_left"], b["f_right"]) for b in holdout])
        rep = {}
        for k in PAIRED_TARGETS:
            truth = np.array([b["left"][k] for b in holdout])
            est = np.array([p[k]["estimate"] for p in preds])
            lo, hi = np.array([p[k]["low"] for p in preds]), np.array([p[k]["high"] for p in preds])
            rep[k] = {"r2": float(1 - np.sum((truth - est) ** 2) / np.sum((truth - truth.mean()) ** 2)),
                      "mae": float(np.mean(np.abs(truth - est))),
                      "coverage_80": float(np.mean((truth >= lo) & (truth <= hi)))}
        cum = np.array([np.exp(b["f_left"]["log_light_units"] - b["f_right"]["log_light_units"]) for b in holdout])
        truth = np.array([b["left"]["mu_fraction"] for b in holdout])
        rep["cumulant_baseline_mu_ratio"] = {"mae": float(np.mean(np.abs(cum - truth))),
                                             "corr": float(np.corrcoef(cum, truth)[0, 1])}
        rep["paired_engine_mu_ratio"] = {"mae": rep["mu_fraction"]["mae"],
                                         "corr": float(np.corrcoef([p["mu_fraction"]["estimate"] for p in preds], truth)[0, 1])}
        self.report = rep
        return rep


def train_paired(base_fe: dict, n: int = 200, n_holdout: int = 40, jobs: int = 1, seed: int = 1, progress=print):
    bank = build_pairs(n + n_holdout, base_fe, seed, jobs, progress=progress)
    cal = PairedCalibrator(sorted(bank[0]["f_left"])).fit_pairs(bank[:n])
    cal.evaluate_pairs(bank[n:])
    return cal, bank
