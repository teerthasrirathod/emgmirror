"""Signal pieces the viewer shows, in Python, as the reference the browser code is checked against.

Everything here works at the ADC pin (mV), where the voltage is measured. The electrode-referred
value needs the front-end gain, which has not been measured on these boards.

  envelope      moving RMS of the cleaned signal (centred window offline, trailing window live)
  activations   periods where envelope / floor stays above `on_x` (hysteresis down to `off_x`),
                gaps shorter than `merge_s` closed, periods shorter than `min_s` dropped
  stats         RMS, mean absolute value, peak, median and mean frequency, SNR against the floor

The browser (static/emgcore.js) implements the same definitions; tests compare the two.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import welch

DEFAULTS = {"env_window_s": 0.1, "on_x": 3.0, "off_x": 2.0, "min_s": 0.3, "merge_s": 0.25}


def envelope_rms(y: np.ndarray, fs: float, window_s: float = 0.1, centered: bool = True) -> np.ndarray:
    """Moving RMS. NaN samples count as 0 and are left out of the mean."""
    y = np.asarray(y, float)
    ok = np.isfinite(y)
    sq = np.where(ok, y, 0.0) ** 2
    w = max(1, int(round(window_s * fs)))
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    cn = np.concatenate([[0], np.cumsum(ok)])
    n = len(y)
    i = np.arange(n)
    if centered:
        lo = np.clip(i - w // 2, 0, n)
        hi = np.clip(i - w // 2 + w, 0, n)
    else:
        lo = np.clip(i - w + 1, 0, n)
        hi = i + 1
    cnt = cn[hi] - cn[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.sqrt((cs[hi] - cs[lo]) / cnt)
    out[cnt == 0] = np.nan
    return out


def activations(ratio: np.ndarray, fs: float, on_x: float = 3.0, off_x: float = 2.0, min_s: float = 0.3,
                merge_s: float = 0.25) -> list[tuple[int, int]]:
    """[start, end) sample positions where the envelope ratio is 'active' (see module doc)."""
    r = np.asarray(ratio, float)
    segs: list[list[int]] = []
    on = False
    start = 0
    for i, v in enumerate(r):
        if not on and np.isfinite(v) and v >= on_x:
            on, start = True, i
        elif on and not (np.isfinite(v) and v >= off_x):
            on = False
            segs.append([start, i])
    if on:
        segs.append([start, len(r)])
    merged: list[list[int]] = []
    gap = int(round(merge_s * fs))
    for s in segs:
        if merged and s[0] - merged[-1][1] < gap:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    keep = int(round(min_s * fs))
    return [(a, b) for a, b in merged if b - a >= keep]


def stats(y: np.ndarray, fs: float, floor: float | None = None) -> dict[str, Any]:
    v = np.asarray(y, float)
    v = v[np.isfinite(v)]
    if len(v) < 16:
        return {"n": int(len(v))}
    rms = float(np.sqrt(np.mean(v ** 2)))
    out: dict[str, Any] = {"n": int(len(v)), "seconds": round(len(v) / fs, 3), "rms": rms,
                           "mav": float(np.mean(np.abs(v))), "peak": float(np.max(np.abs(v))),
                           "p2p": float(np.ptp(v))}
    f, p = welch(v, fs, nperseg=min(256, len(v)))
    sel = (f >= 20) & (f <= 450)
    if sel.any() and p[sel].sum() > 0:
        c = np.cumsum(p[sel])
        out["median_hz"] = float(f[sel][np.searchsorted(c, c[-1] / 2)])
        out["mean_hz"] = float(np.sum(f[sel] * p[sel]) / np.sum(p[sel]))
    if floor:
        out["snr_db"] = float(20 * np.log10(rms / floor)) if rms > 0 else None
        out["net_rms"] = float(np.sqrt(max(rms ** 2 - floor ** 2, 0.0)))
    return out


# ------------------------------------------------------------------ recording metadata
def find_dataset_session(rec_id: str, dataset_root: Path) -> Path | None:
    for sj in sorted(Path(dataset_root).glob("*/*/session.json")):
        try:
            if rec_id in json.loads(sj.read_text(encoding="utf-8")).get("recordings", []):
                return sj.parent
        except (OSError, json.JSONDecodeError):
            continue
    return None


def recording_meta(rec, cfg: dict, dataset_root: Path | None = None) -> dict[str, Any]:
    """What the viewer needs to label a recording: channels, device, integrity, floor, cues, trials."""
    from ..dataset import marker_position, record_positions, trials_from_markers

    m = rec.manifest
    src = m["source"]
    fs = rec.fs
    idx = rec.records["index"].astype(np.int64)
    i0 = int(idx[0]) if len(idx) else 0
    hw = (m.get("config") or {}).get("hardware") or src.get("hardware") or cfg.get("hardware") or {}
    hw_ch = {int(c["ch"]): c for c in hw.get("channels", []) if "ch" in c}
    pins = str(src.get("device", {}).get("pins", "")).split(",")
    chans = []
    for k, c in enumerate(src["channels"]):
        h = hw_ch.get(k + 1, {})
        chans.append({"ch": k + 1, "name": c.get("name"), "arm": c.get("arm"), "muscle": c.get("muscle"),
                      "gpio": h.get("gpio", pins[k] if k < len(pins) and pins[k] else None),
                      "header": h.get("header")})
    floor, floor_src = None, None
    session_dir = find_dataset_session(m["recording_id"], dataset_root) if dataset_root else None
    trials: list[dict] = []
    if session_dir and (session_dir / "floor.json").exists():
        fj = json.loads((session_dir / "floor.json").read_text(encoding="utf-8"))
        floor = [c["floor_pin_mv"] for c in fj["channels"]]
        floor_src = f"dataset session {session_dir.name} floor check ({fj['recording_id']})"
        tpath = session_dir / "trials.csv"
        if tpath.exists():
            with open(tpath, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row["recording_id"] != m["recording_id"]:
                        continue
                    trials.append({"ch": int(row["ch"]), "task": row["task"], "rep": int(row["rep"]),
                                   "target_effort": float(row["target_effort"] or 0),
                                   "start_s": (int(row["start_index"]) - i0) / fs,
                                   "end_s": (int(row["end_index"]) - i0) / fs,
                                   "rms_pin_mv": float(row["rms_pin_mv"]), "snr_db": float(row["snr_db"]),
                                   "active_fraction": float(row["active_fraction"]),
                                   "median_hz": float(row["median_hz"]) if row["median_hz"] else None,
                                   "quality": row["quality"]})
    if floor is None:
        rnf = cfg.get("room_noise_floor") or {}
        pm = rnf.get("pin_mv_rms") or {}
        if pm:
            floor = [pm.get(f"ch{k + 1}") for k in range(len(chans))]
            floor_src = f"config room_noise_floor ({rnf.get('measured', 'date not given')})"
    acq = (m.get("config") or {}).get("acquisition") or cfg.get("acquisition", {})
    tpos = record_positions(rec)  # the same timeline TailReader builds, across device restarts
    cues = [{"task": t["task"], "level": t["level"],
             "start_s": marker_position(rec, t["start"], t.get("run"), tpos) / fs,
             "end_s": marker_position(rec, t["end"], t.get("run"), tpos) / fs}
            for t in trials_from_markers(rec)]
    events = []
    for e in rec.events:
        if e.get("event") in ("marker", "gap", "abort", "error", "disconnect") and e.get("label") not in (
                "trial_start", "trial_end"):
            pos = None if e.get("index") is None else marker_position(rec, int(e["index"]), e.get("run"), tpos) / fs
            events.append({"event": e["event"], "t_s": pos, "label": e.get("label") or e.get("reason") or e.get("cause"),
                           "missing": e.get("missing")})
    integ = rec.integrity()
    return {
        "recording_id": m["recording_id"], "status": m.get("status"), "started": m.get("started"),
        "label": (m.get("metadata") or {}).get("label", ""), "arm": (m.get("metadata") or {}).get("arm"),
        "source": {"kind": src["kind"], "label": src["label"], "real_hardware": src.get("is_real_hardware")},
        "device": {k: src.get("device", {}).get(k) for k in ("fw", "chip", "mac", "pins", "fs", "os")},
        "fs": fs, "nch": rec.nch, "i0": i0, "channels": chans,
        "samples": integ["samples"], "missing_samples": integ.get("missing_samples", 0),
        "invalid_samples": integ.get("invalid_samples", 0), "duration_s": integ.get("duration_s"),
        "floor_pin_mv": floor, "floor_source": floor_src,
        "gain_nominal": float(acq.get("amp_gain", 1.0)), "mains_hz": float(acq.get("mains_hz", 50)),
        "cues": cues, "trials": trials, "events": events,
        "dataset_session": session_dir.name if session_dir else None,
        "defaults": DEFAULTS,
    }


# ------------------------------------------------------------------ incremental reading
class TailReader:
    """Reads samples.bin from where it stopped last time and turns rows into timeline positions.

    position = sample index - first index. Missing samples become NaN at their place in time.
    If the device index restarts (new run), the new run continues right after the last position.
    """

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        m = json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))
        src = m["source"]
        from ..recording.raw import record_dtype

        self.nch = len(src["channels"])
        self.dtype = record_dtype(src["raw_dtype"], self.nch)
        dev = src.get("device", {})
        self.kind = src["kind"]
        self.u2 = src["raw_dtype"] == "<u2"
        self.os = float(dev.get("os", 1))
        self.cal_a = float(dev.get("cal_a", 0.8059))
        self.cal_b = float(dev.get("cal_b", 0.0))
        acq = (m.get("config") or {}).get("acquisition") or {}
        self.bits = int(acq.get("adc_bits", 12))
        self.vref = float(acq.get("adc_vref", 3.3))
        self.rows = 0  # rows consumed
        self.i0: int | None = None
        self.offset = 0  # index offset applied after restarts
        self.last_pos = -1
        self.last_idx: int | None = None

    def pin_mv(self, raw: np.ndarray) -> np.ndarray:
        r = raw.astype(np.float64)
        if self.kind in ("virtual", "replay"):
            return r
        if self.u2:
            out = self.cal_a * r / self.os + self.cal_b
            out[raw == 0xFFFF] = np.nan
            return out
        return r / 2 ** self.bits * self.vref * 1000.0

    def read(self, from_row: int, max_rows: int = 2_000_000) -> tuple[int, np.ndarray, int]:
        """Rows from `from_row` on -> (first position, (n_pos, nch) float32 pin mV with NaN gaps, next row).

        Must be called with from_row == the `next row` it returned last time (or 0 to start over)."""
        if from_row == 0 and self.rows:
            self.__init__(self.folder)
        if from_row != self.rows:
            raise ValueError(f"expected from_row={self.rows}, got {from_row}")
        isz = self.dtype.itemsize
        with open(self.folder / "samples.bin", "rb") as fh:
            fh.seek(from_row * isz)
            blob = fh.read(max_rows * isz)
        n = len(blob) // isz
        if n == 0:
            return self.last_pos + 1, np.zeros((0, self.nch), np.float32), from_row
        rec = np.frombuffer(blob[: n * isz], dtype=self.dtype)
        idx = rec["index"].astype(np.int64)
        if self.i0 is None:
            self.i0 = int(idx[0])
        # A restart is a row whose index is not above the previous one. Between restarts the position is
        # index - i0 + offset; at each restart the offset changes so the new run continues right after the
        # last position. Restarts are rare, so only they are walked in Python.
        prev_idx = np.concatenate([[self.last_idx if self.last_idx is not None else idx[0] - 1], idx[:-1]])
        starts = np.flatnonzero(idx <= prev_idx)
        bounds = np.concatenate([[0], starts, [n]])
        pos = np.empty(n, np.int64)
        off = self.offset
        for a, b in zip(bounds[:-1], bounds[1:]):
            if a == b:
                continue
            if a in starts:
                last = self.last_pos if a == 0 else int(pos[a - 1])
                off = (last + 1) - (int(idx[a]) - self.i0)
            pos[a:b] = idx[a:b] - self.i0 + off
        self.offset, self.last_idx = off, int(idx[-1])
        first = self.last_pos + 1
        span = int(pos[-1] - first + 1)
        out = np.full((span, self.nch), np.nan, np.float32)
        out[pos - first] = self.pin_mv(rec["raw"]).astype(np.float32)
        self.last_pos = int(pos[-1])
        self.rows = from_row + n
        return first, out, self.rows

    def last_host_ns(self) -> int | None:
        """Host clock at the last received chunk (frames.csv tail), for the display latency."""
        p = self.folder / "frames.csv"
        try:
            with open(p, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 512))
                lines = fh.read().decode("utf-8", "replace").strip().splitlines()
            for line in reversed(lines):
                parts = line.split(",")
                if len(parts) >= 6 and parts[5].isdigit():
                    return int(parts[5])
        except OSError:
            pass
        return None
