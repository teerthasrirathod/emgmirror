"""Validate the estimators against simulator ground truth.

Sweeps the left-arm injury model from healthy to severe, runs the full
analysis on each synthetic session and reports how closely every headline
metric follows the known answer. Re-run this whenever an estimator, the
protocol or the electrode count changes.
"""

from __future__ import annotations

import copy

import numpy as np
from scipy.stats import spearmanr

from ..config import muscles
from ..simulate.synthetic import synthetic_session
from .bilateral import analyze

# (mu_fraction, enlargement, drive_max)
SCENARIOS = [
    (1.00, 1.0, 1.00),
    (0.80, 1.1, 1.00),
    (0.65, 1.2, 0.95),
    (0.50, 1.4, 0.90),
    (0.35, 1.5, 0.80),
    (0.20, 2.0, 0.75),
]


def run(cfg: dict, seeds=(7,), scenarios=SCENARIOS, progress=print) -> dict:
    rows = []
    groups = muscles(cfg)
    for seed in seeds:
        for frac, grow, drive in scenarios:
            c = copy.deepcopy(cfg)
            c["simulation"]["seed"] = int(seed)
            imp = {m: {"mu_fraction": frac, "enlargement": grow, "drive_max": drive} for m in groups}
            res = analyze(synthetic_session(c, overrides=imp, session_id="validation"), c)
            for m in groups:
                gt = res["ground_truth_check"][m]
                rows.append({
                    "seed": seed, "muscle": m, "mu_fraction": frac, "enlargement": grow, "drive_max": drive,
                    "true_pool_pct": gt["true_mu_pool_pct"],
                    "true_firing_pct": gt["true_mu_firing_pct"],
                    "activation_pct": res["summary"][m]["activation_pct"],
                    "mu_firing_pct": res["summary"][m]["mu_firing_pct"],
                })
            progress(f"seed {seed} | units {frac:.2f} enlarge {grow:.1f} drive {drive:.2f} done")

    def score(est_key, true_key):
        pairs = [(r[est_key], r[true_key]) for r in rows if r[est_key] is not None]
        if len(pairs) < 3:
            return None
        est, true = np.array(pairs).T
        return {
            "spearman": float(spearmanr(est, true).statistic),
            "mae_pct_points": float(np.mean(np.abs(est - true))),
            "bias_pct_points": float(np.mean(est - true)),
            "n": len(pairs),
        }

    scores = {
        "activation_pct_vs_true_pool": score("activation_pct", "true_pool_pct"),
        "mu_firing_pct_vs_true_firing": score("mu_firing_pct", "true_firing_pct"),
    }
    return {"rows": rows, "scores": scores}


def format_table(result: dict) -> str:
    lines = [f"{'muscle':<10}{'units':>6}{'enl':>5}{'drive':>6} | {'true pool':>9}{'true fire':>10} | "
             f"{'activ.%':>8}{'MU fire%':>9}"]
    for r in result["rows"]:
        a = f"{r['activation_pct']:.0f}" if r["activation_pct"] is not None else "-"
        f = f"{r['mu_firing_pct']:.0f}" if r["mu_firing_pct"] is not None else "-"
        lines.append(f"{r['muscle']:<10}{r['mu_fraction']:>6.2f}{r['enlargement']:>5.1f}{r['drive_max']:>6.2f} | "
                     f"{r['true_pool_pct']:>9.0f}{r['true_firing_pct']:>10.0f} | {a:>8}{f:>9}")
    lines.append("")
    for name, s in result["scores"].items():
        if s:
            lines.append(f"{name}: spearman={s['spearman']:.2f}  MAE={s['mae_pct_points']:.1f} pts  "
                         f"bias={s['bias_pct_points']:+.1f} pts  (n={s['n']})")
    return "\n".join(lines)
