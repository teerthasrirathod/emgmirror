"""Generate complete synthetic sessions that look exactly like real recordings."""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from .. import protocol
from ..config import muscles
from ..storage import Block, Channel, Session, new_session_id
from .motor_units import (add_muap_trains, firing_rates, make_pool, noise, spike_trains,
                          twitch_force)

ARMS = ("right", "left")


def _excitation(cfg: dict, fs: float, muscle: str, rng: np.random.Generator):
    target, trials, masks = protocol.build_timeline(cfg, fs)
    weight = np.zeros_like(target)
    for task, mask in masks.items():
        weight[mask] = cfg["task_muscle_drive"].get(task, {}).get(muscle, 0.0)
    # Slow physiological fluctuation in drive (~3 %).
    sos = butter(2, 2.0, fs=fs, output="sos")
    wobble = sosfiltfilt(sos, rng.standard_normal(len(target)))
    wobble = 1 + 0.03 * wobble / max(np.abs(wobble).max(), 1e-9)
    return np.clip(target * weight * wobble, 0, 1), trials


def simulate_arms(cfg: dict, fs: float | None = None, overrides: dict | None = None):
    """Simulate every (arm, muscle) pool over the protocol.

    Returns per-arm EMG for each muscle-channel, force, trials and ground truth.
    `overrides` replaces `simulation.left_impairment` (used by validation sweeps).
    """
    sim = cfg["simulation"]
    fs = float(fs or cfg["acquisition"]["fs"])
    seed = int(sim["seed"])
    n_mu = int(sim["motor_units_per_muscle"])
    impair = overrides if overrides is not None else sim.get("left_impairment", {})
    groups = muscles(cfg)

    out: dict = {"fs": fs, "arms": {}, "truth": {"arms": {}}}
    for a_i, arm in enumerate(ARMS):
        rng = np.random.default_rng(seed * 1000 + a_i)
        pools, trains, trials = {}, {}, None
        for m_i, muscle in enumerate(groups):
            pool = make_pool(muscle, n_mu, seed + 17 * m_i,
                             impair.get(muscle) if arm == "left" else None)
            exc, trials = _excitation(cfg, fs, muscle, rng)
            rates = firing_rates(pool, exc)
            pools[muscle] = pool
            trains[muscle] = spike_trains(rates, fs, rng)
        n = len(exc)

        emg = {}
        for muscle in groups:
            sig = noise(n, fs, rng, sim["noise_uv_rms"], sim["mains_uv"], cfg["acquisition"]["mains_hz"])
            for other in groups:
                add_muap_trains(sig, pools[other], trains[other], fs, same_muscle=(other == muscle))
            emg[muscle] = sig

        flex = groups[0]
        force = twitch_force(pools[flex], trains[flex], n, fs)

        truth = {}
        for muscle in groups:
            pool = pools[muscle]
            per_task = {}
            for t in trials:
                a, b = int(t.start_s * fs), int(t.end_s * fs)
                counts = np.array([np.sum((tr >= a) & (tr < b)) for tr in trains[muscle]])
                active = counts >= 2
                dur = (b - a) / fs
                per_task[t.task] = {
                    "active_mu": int(active.sum()),
                    "mean_rate_hz": float(counts[active].mean() / dur) if active.any() else 0.0,
                }
            truth[muscle] = {
                "pool_healthy": pool.n_total,
                "pool_alive": pool.n_alive,
                "drive_max": pool.drive_max,
                "mean_muap_mv_alive": float(pool.muap_amp[pool.alive].mean()),
                "tasks": per_task,
            }
        out["arms"][arm] = {"emg": emg, "force": force, "trials": trials}
        out["truth"]["arms"][arm] = truth

    # Force is reported relative to the healthy arm's best effort.
    ref = max(out["arms"]["right"]["force"].max(), 1e-9)
    for arm in ARMS:
        out["arms"][arm]["force"] = out["arms"][arm]["force"] / ref
    out["truth"]["impairment"] = impair
    out["truth"]["note"] = "Synthetic ground truth. Never present for real recordings."
    return out


def synthetic_session(cfg: dict, mode: str = "mirror", fs: float | None = None,
                      overrides: dict | None = None, session_id: str | None = None) -> Session:
    """Build a Session in `mirror` (one block, both arms) or `sequential` mode."""
    sim = simulate_arms(cfg, fs, overrides)
    fs = sim["fs"]
    groups = muscles(cfg)
    names = {(c["arm"], c["muscle"]): c["name"] for c in cfg["channels"]}

    def chan(arm, m):
        return Channel(name=names.get((arm, m), f"{arm[0].upper()}_{m}"), arm=arm, muscle=m)

    blocks = []
    if mode == "mirror":
        chans, cols = [], []
        for arm in ARMS:
            for m in groups:
                chans.append(chan(arm, m))
                cols.append(sim["arms"][arm]["emg"][m])
        # One dynamometer: in mirror mode we keep the left (impaired) hand's force.
        blocks.append(Block(chans, sim["arms"]["left"]["trials"], np.column_stack(cols),
                            sim["arms"]["left"]["force"]))
    elif mode == "sequential":
        for arm in ARMS:
            data = np.column_stack([sim["arms"][arm]["emg"][m] for m in groups])
            blocks.append(Block([chan(arm, m) for m in groups], sim["arms"][arm]["trials"], data,
                                sim["arms"][arm]["force"]))
    else:
        raise ValueError("mode must be 'mirror' or 'sequential'")

    return Session(
        session_id=session_id or new_session_id(f"synthetic-{mode}"),
        source="synthetic",
        fs=fs,
        blocks=blocks,
        subject=cfg.get("subject", {}),
        ground_truth=sim["truth"],
        notes="Synthetic data from the motor-unit simulator (placeholder until real sensors arrive).",
    )
