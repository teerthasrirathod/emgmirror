"""Derived layers from a raw recording. The raw recording itself is never modified.

    data/raw/<id>/              raw layer (RawRecorder)
    data/derived/<id>/
        integrity.json          gaps, completeness, rates, per-channel pin statistics
        validated.npz           index, pin_mv, mv, valid (NaN where invalid; gaps NOT filled)
    data/sessions/<session>/    processed layer for the existing analysis (Session format),
                                with `provenance` pointing back to the raw recording

Gaps are only ever filled when converting to a Session (the analysis needs a uniform time base), and
only on request; each fill is listed in the session's provenance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..storage import Block, Channel, Session, Trial
from .raw import RawRecording


def channel_stats(pin_mv: np.ndarray | None, mv: np.ndarray, thresholds: dict) -> list[dict[str, Any]]:
    x_all = pin_mv if pin_mv is not None else mv
    out = []
    for k in range(x_all.shape[1]):
        x = x_all[:, k]
        good = x[np.isfinite(x)]
        s: dict[str, Any] = {"ch": k + 1, "units": "mV at ADC pin" if pin_mv is not None else "mV",
                             "valid_fraction": round(len(good) / max(len(x), 1), 6)}
        if len(good):
            mean, p2p = float(good.mean()), float(np.ptp(good))
            s.update(mean=round(mean, 3), min=round(float(good.min()), 3), max=round(float(good.max()), 3),
                     p2p=round(p2p, 3), std=round(float(good.std()), 4))
            if pin_mv is not None:
                if mean < thresholds["rail_low_mv"] and p2p < 50:
                    s["verdict"] = "stuck_low"
                elif mean > thresholds["rail_high_mv"] and p2p < 50:
                    s["verdict"] = "stuck_high"
                elif p2p < thresholds["flat_p2p_mv"]:
                    s["verdict"] = "flat"
                else:
                    s["verdict"] = "varying"
        else:
            s["verdict"] = "no_valid_samples"
        out.append(s)
    return out


def validate(rec: RawRecording, out_root: str | Path, thresholds: dict) -> dict[str, Any]:
    out = Path(out_root) / rec.manifest["recording_id"]
    out.mkdir(parents=True, exist_ok=True)
    integ = rec.integrity()
    pin = rec.to_pin_mv()
    mv = rec.to_mv()
    integ["channels"] = channel_stats(pin, mv, thresholds)
    integ["conversion"] = rec.manifest["source"].get("calibration", {})
    np.savez_compressed(out / "validated.npz", index=rec.records["index"], mv=mv.astype(np.float32),
                        pin_mv=(pin if pin is not None else np.empty((0, 0))).astype(np.float32),
                        valid=np.isfinite(mv))
    (out / "integrity.json").write_text(json.dumps(integ, indent=2, default=str), encoding="utf-8")
    return integ


def _trials_from_markers(markers: list[dict], index0: int, fs: float, idx_to_pos) -> list[Trial]:
    trials, open_ = [], {}
    for m in markers:
        if m.get("index") is None:
            continue
        if m["label"] == "trial_start":
            open_[m.get("task")] = m
        elif m["label"] == "trial_end" and m.get("task") in open_:
            s = open_.pop(m["task"])
            a, b = idx_to_pos(int(s["index"])), idx_to_pos(int(m["index"]))
            if a is not None and b is not None and b > a:
                trials.append(Trial(task=m["task"], start_s=a / fs, end_s=b / fs, level=float(s.get("level", 0.0))))
    return trials


def to_session(rec: RawRecording, session_id: str, arm: str | None = None, allow_gaps: bool = False,
               max_fill_s: float = 2.0) -> Session:
    """One Block per device run. Missing samples become explicit 0 mV fill only if allow_gaps."""
    integ = rec.integrity()
    if integ["missing_samples"] and not allow_gaps:
        raise ValueError(f"{integ['missing_samples']} samples missing in {len(integ['gaps'])} gap(s); pass "
                         "allow_gaps=True to fill them with 0 mV (listed in the session provenance)")
    src = rec.manifest["source"]
    fs = rec.fs
    chans = [Channel(**c) for c in src["channels"]]
    if arm:
        chans = [Channel(c.name, arm, c.muscle) for c in chans]
    mv = np.nan_to_num(rec.to_mv(), nan=0.0)
    idx = rec.records["index"].astype(np.int64)
    markers = [e for e in rec.events if e.get("event") == "marker"]
    runs = rec.record_runs()
    # split into runs where the index restarts
    cuts = [0] + [i + 1 for i in np.flatnonzero(np.diff(idx) <= 0)] + [len(idx)]
    blocks, fills = [], []
    for a, b in zip(cuts[:-1], cuts[1:]):
        seg_idx, seg = idx[a:b], mv[a:b]
        if len(seg_idx) == 0:
            continue
        # The index restarts with every run, so a marker belongs only to the segment of its own run.
        # Markers without a run (recordings made before markers carried one) match by index alone.
        seg_run = None if runs is None else int(runs[a])
        seg_markers = [m for m in markers if m.get("run") is None or seg_run is None or int(m["run"]) == seg_run]
        span = int(seg_idx[-1] - seg_idx[0] + 1)
        pos = (seg_idx - seg_idx[0]).astype(np.int64)
        d = np.diff(seg_idx)
        if (d > max_fill_s * fs + 1).any():
            raise ValueError(f"a gap longer than {max_fill_s} s: split the recording instead of filling it")
        data = np.zeros((span, len(chans)))
        data[pos] = seg
        for i in np.flatnonzero(d > 1):
            fills.append({"after_index": int(seg_idx[i]), "missing": int(d[i] - 1), "filled_with": "0 mV"})

        def idx_to_pos(i, lo=int(seg_idx[0]), hi=int(seg_idx[-1])):
            return i - lo if lo <= i <= hi + 1 else None

        trials = _trials_from_markers(seg_markers, int(seg_idx[0]), fs, idx_to_pos)
        blocks.append(Block(channels=chans, trials=trials, data=data))
    meta = rec.manifest.get("metadata", {})
    return Session(
        session_id=session_id, source=src["kind"], fs=fs, blocks=blocks,
        notes=f"Converted from raw recording {rec.manifest['recording_id']}. "
              "mv = electrode-referred estimate (nominal gain, assumed bias); see provenance.",
        provenance={"raw_recording": rec.manifest["recording_id"], "raw_dir": str(rec.dir),
                    "raw_status": rec.manifest.get("status"), "device": src.get("device", {}),
                    "calibration": src.get("calibration", {}), "is_real_hardware": src.get("is_real_hardware"),
                    "integrity": {k: integ[k] for k in ("missing_samples", "completeness", "invalid_samples",
                                                         "observed_rate_device_clock_hz",
                                                         "observed_rate_host_clock_hz")},
                    "fills": fills, "metadata": meta},
    )
