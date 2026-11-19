"""Append-only raw recording: exactly what the source delivered, nothing filtered or converted.

Layout of one recording (``data/raw/<recording_id>/``)::

    manifest.json    format, source identity, channel map, sampling, calibration, config snapshot,
                     status ("recording" while open; "complete" or "interrupted" when closed)
    samples.bin      fixed-size little-endian records, appended: index:u8 then raw[nch] in the
                     source's native dtype (ESP32: u2 = sum of `os` ADC codes, 0xFFFF = invalid)
    frames.csv       one line per received chunk: run, seq, first_index, n, t_device_us, t_host_ns,
                     flags, gap_before, gap_cause
    events.jsonl     start/stop, device HELLO/status, gaps, errors, trial markers, disconnects

Every file is only ever appended to. A crash leaves at most one partial record at the end of
samples.bin, which the reader ignores and reports. A directory that already exists is never
reused, so a recording cannot be overwritten. Derived layers (validated, processed, features,
analysis) are written elsewhere (`data/derived/`, `data/sessions/`).
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..acquisition.base import Chunk, SourceInfo

FORMAT = "emgmirror-raw/1"


def record_dtype(raw_dtype: str, nch: int) -> np.dtype:
    return np.dtype([("index", "<u8"), ("raw", raw_dtype, (nch,))])


def new_recording_id(label: str = "") -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
    return f"{stamp}_{safe}" if safe else stamp


class RawRecorder:
    def __init__(self, root: str | Path, info: SourceInfo, recording_id: str | None = None, label: str = "",
                 metadata: dict[str, Any] | None = None, config_snapshot: dict[str, Any] | None = None,
                 fsync_every_s: float = 1.0):
        self.root = Path(root)
        self.recording_id = recording_id or new_recording_id(label)
        self.dir = self.root / self.recording_id
        self.dir.mkdir(parents=True, exist_ok=False)  # never reuse (and so never overwrite) a recording
        self.info = info
        self.nch = len(info.channels)
        self.dtype = record_dtype(info.raw_dtype, self.nch)
        self.metadata = metadata or {}
        self.config_snapshot = config_snapshot or {}
        self.fsync_every_s = fsync_every_s
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.samples = 0
        self.gap_samples = 0
        self.invalid = 0
        self.first_index: int | None = None
        self.last_index: int | None = None
        self.runs: set[int] = set()
        self.last_run: int | None = None  # run of the last written chunk; markers carry it
        self.closed = False
        self._last_sync = time.time()
        self.push_every_s = 0.05  # flush (no fsync) at least this often: live viewers tail samples.bin
        self._last_push = self._last_sync
        self._write_manifest(status="recording")
        self._samples = open(self.dir / "samples.bin", "ab")
        self._frames = open(self.dir / "frames.csv", "a", newline="", encoding="utf-8")
        self._frames_csv = csv.writer(self._frames)
        self._frames_csv.writerow(["run", "seq", "first_index", "n", "t_device_us", "t_host_ns", "flags",
                                   "gap_before", "gap_cause"])
        self._events = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self.event("start", source=info.kind, label=info.label)

    # ---------------- writing ----------------
    def write(self, chunk: Chunk) -> None:
        if self.closed:
            raise RuntimeError("recording is closed")
        n = len(chunk)
        if n == 0:
            return
        if chunk.raw.shape[1] != self.nch:
            raise ValueError(f"chunk has {chunk.raw.shape[1]} channels, recording has {self.nch}")
        rec = np.empty(n, dtype=self.dtype)
        rec["index"] = chunk.index
        rec["raw"] = chunk.raw.astype(self.dtype["raw"].base, copy=False)
        self._samples.write(rec.tobytes())
        self._frames_csv.writerow([chunk.run, "" if chunk.seq is None else chunk.seq, int(chunk.index[0]), n,
                                   "" if chunk.t_device_us is None else chunk.t_device_us,
                                   int(chunk.t_host * 1e9), chunk.flags, chunk.gap_before, chunk.gap_cause])
        if chunk.gap_before:
            self.gap_samples += chunk.gap_before
            self.event("gap", index=int(chunk.index[0]), missing=int(chunk.gap_before), cause=chunk.gap_cause,
                       run=chunk.run)
        self.samples += n
        self.invalid += int((~chunk.valid).sum())
        self.runs.add(chunk.run)
        self.last_run = chunk.run
        if self.first_index is None:
            self.first_index = int(chunk.index[0])
        self.last_index = int(chunk.index[-1])
        now = time.time()
        if now - self._last_sync >= self.fsync_every_s:
            self.flush(sync=True)
        elif now - self._last_push >= self.push_every_s:  # hand data to the OS often, so a live viewer can read it
            self._samples.flush()
            self._frames.flush()
            self._last_push = now

    def event(self, kind: str, **fields: Any) -> None:
        if self.closed:
            return
        rec = {"t": time.time(), "event": kind, **fields}
        self._events.write(json.dumps(rec, default=str) + "\n")
        self._events.flush()

    def mark(self, label: str, index: int | None = None, run: int | None = None, **fields: Any) -> None:
        """Trial or user marker at a sample index (default: the last written sample + 1).

        The marker also carries the device run (default: the run of the last written chunk): the
        index restarts at 0 on every run, so an index alone is ambiguous after a device restart.
        """
        idx = index if index is not None else (None if self.last_index is None else self.last_index + 1)
        self.event("marker", label=label, index=idx, run=run if run is not None else self.last_run, **fields)

    def flush(self, sync: bool = False) -> None:
        for f in (self._samples, self._frames, self._events):
            f.flush()
            if sync:
                os.fsync(f.fileno())
        self._last_sync = time.time()

    def close(self, status: str = "complete", reason: str = "") -> Path:
        if self.closed:
            return self.dir
        self.event("stop", status=status, reason=reason, samples=self.samples, gap_samples=self.gap_samples)
        self.flush(sync=True)
        for f in (self._samples, self._frames, self._events):
            f.close()
        self.closed = True
        self._write_manifest(status=status, reason=reason)
        return self.dir

    def summary(self) -> dict[str, Any]:
        return {"recording_id": self.recording_id, "dir": str(self.dir), "samples": self.samples,
                "gap_samples": self.gap_samples, "invalid_samples": self.invalid, "runs": sorted(self.runs),
                "seconds": round(self.samples / self.info.fs_nominal, 2), "closed": self.closed}

    def _write_manifest(self, status: str, reason: str = "") -> None:
        m = {
            "format": FORMAT,
            "recording_id": self.recording_id,
            "status": status,
            "status_reason": reason,
            "started": self.started,
            "stopped": time.strftime("%Y-%m-%dT%H:%M:%S%z") if status != "recording" else None,
            "source": self.info.to_dict(),
            "sample_record": {"dtype": [["index", "<u8"], ["raw", self.info.raw_dtype, [self.nch]]],
                              "itemsize": self.dtype.itemsize, "file": "samples.bin"},
            "metadata": self.metadata,
            "config": self.config_snapshot,
            "totals": {"samples": self.samples, "gap_samples": self.gap_samples, "invalid_samples": self.invalid,
                       "first_index": self.first_index, "last_index": self.last_index, "runs": sorted(self.runs)},
        }
        tmp = self.dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.dir / "manifest.json")  # atomic: a reader never sees half a manifest


# ---------------- reading ----------------
def _frame_row_ok(row: dict) -> bool:
    """A frames.csv row whose run, first index and length parse (a crash can leave a half-written last line)."""
    try:
        int(row["run"]), int(row["first_index"]), int(row["n"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


@dataclass
class RawRecording:
    dir: Path
    manifest: dict[str, Any]
    records: np.ndarray  # structured: index, raw
    frames: list[dict[str, str]]
    events: list[dict[str, Any]]
    truncated_bytes: int  # partial record at the end of samples.bin (crash), ignored

    @classmethod
    def open(cls, folder: str | Path, frames: bool = True) -> "RawRecording":
        """frames=False skips frames.csv (slow to parse for long recordings); integrity() then reports
        no runs or clock rates, everything derived from the sample indices is unchanged."""
        folder = Path(folder)
        m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if m.get("format") != FORMAT:
            raise ValueError(f"unsupported raw format {m.get('format')!r}")
        src = m["source"]
        dt = record_dtype(src["raw_dtype"], len(src["channels"]))
        blob = (folder / "samples.bin").read_bytes()
        whole = len(blob) // dt.itemsize * dt.itemsize
        rec = np.frombuffer(blob[:whole], dtype=dt)
        rows = []
        fpath = folder / "frames.csv"
        if frames and fpath.exists():
            with open(fpath, newline="", encoding="utf-8") as fh:
                rows = [r for r in csv.DictReader(fh) if _frame_row_ok(r)]  # a crash can cut the last line
        events = []
        epath = folder / "events.jsonl"
        if epath.exists():
            for line in epath.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a crash can leave half a line
        return cls(folder, m, rec, rows, events, len(blob) - whole)

    @property
    def interrupted(self) -> bool:
        return self.manifest.get("status") != "complete"

    @property
    def fs(self) -> float:
        return float(self.manifest["source"]["fs_nominal"])

    @property
    def nch(self) -> int:
        return len(self.manifest["source"]["channels"])

    def runs(self) -> list[int]:
        return sorted({int(f["run"]) for f in self.frames}) if self.frames else [0]

    def record_runs(self) -> np.ndarray | None:
        """Device run of every sample record, from frames.csv (one line per written chunk, in order).
        None when frames were not loaded or are missing."""
        if not self.frames:
            return None
        out = np.empty(len(self.records), dtype=np.int64)
        pos = 0
        for f in self.frames:
            if pos >= len(out):
                break
            n = int(f["n"])
            out[pos: pos + n] = int(f["run"])
            pos += n
        if pos < len(out):  # frames.csv shorter than samples.bin (crash between the two writes)
            out[pos:] = out[pos - 1] if pos else 0
        return out

    def integrity(self) -> dict[str, Any]:
        """Re-derive gaps from the sample indices themselves (not from what the recorder logged)."""
        idx = self.records["index"].astype(np.int64)
        out: dict[str, Any] = {"recording_id": self.manifest["recording_id"], "status": self.manifest.get("status"),
                               "interrupted": self.interrupted, "samples": int(len(idx)),
                               "truncated_bytes": self.truncated_bytes, "fs_nominal": self.fs}
        if len(idx) == 0:
            out.update(gaps=[], missing_samples=0, duplicate_or_backwards=0)
            return out
        d = np.diff(idx)
        restarts = np.flatnonzero(d <= 0)  # new device run (index restarted) or duplicates
        gap_at = np.flatnonzero(d > 1)
        gaps = [{"after_index": int(idx[i]), "missing": int(d[i] - 1)} for i in gap_at]
        raw = self.records["raw"]
        invalid = int((raw == 0xFFFF).sum()) if raw.dtype.kind == "u" and raw.dtype.itemsize == 2 else \
            int((~np.isfinite(raw.astype(float))).sum())
        span = int(len(idx) + sum(g["missing"] for g in gaps))
        th = [f for f in self.frames if f.get("t_device_us")]
        dev_rate = None
        if len(th) >= 2:
            a, b = th[0], th[-1]
            if a["run"] == b["run"]:
                dt = (int(b["t_device_us"]) - int(a["t_device_us"])) / 1e6
                if dt > 0:
                    dev_rate = (int(b["first_index"]) + int(b["n"]) - int(a["first_index"]) - int(a["n"])) / dt
        host_rate = None
        if len(self.frames) >= 2:
            a, b = self.frames[0], self.frames[-1]
            dt = (int(b["t_host_ns"]) - int(a["t_host_ns"])) / 1e9
            if dt > 0 and a["run"] == b["run"]:
                host_rate = (int(b["first_index"]) + int(b["n"]) - int(a["first_index"]) - int(a["n"])) / dt
        out.update(gaps=gaps, missing_samples=int(sum(g["missing"] for g in gaps)), runs=self.runs(),
                   index_restarts=int(len(restarts)), invalid_samples=invalid, span_samples=span,
                   completeness=round(len(idx) / span, 6) if span else None,
                   duration_s=round(span / self.fs, 3),
                   observed_rate_device_clock_hz=None if dev_rate is None else round(dev_rate, 3),
                   observed_rate_host_clock_hz=None if host_rate is None else round(host_rate, 3),
                   markers=[e for e in self.events if e.get("event") == "marker"])
        return out

    def to_pin_mv(self) -> np.ndarray | None:
        """Voltage at the ADC pins (mV), NaN for invalid samples. None for sources without an ADC."""
        src = self.manifest["source"]
        if src["kind"] in ("virtual", "replay"):
            return None
        raw = self.records["raw"].astype(np.float64)
        dev = src.get("device", {})
        cal = src.get("calibration", {})
        if src["raw_dtype"] == "<u2":
            os_ = float(dev.get("os", 1))
            valid = self.records["raw"] != 0xFFFF
            pin = float(dev.get("cal_a", 0.8059)) * raw / os_ + float(dev.get("cal_b", 0.0))
            pin[~valid] = np.nan
            return pin
        bits = int(self.manifest.get("config", {}).get("acquisition", {}).get("adc_bits", 12))
        vref = float(self.manifest.get("config", {}).get("acquisition", {}).get("adc_vref", 3.3))
        _ = cal
        return raw / 2**bits * vref * 1000.0

    def to_mv(self) -> np.ndarray:
        """Electrode-referred estimate in mV using the conversion recorded in the manifest."""
        src = self.manifest["source"]
        if src["kind"] in ("virtual", "replay"):
            return self.records["raw"].astype(np.float64)
        pin = self.to_pin_mv()
        cal = src.get("calibration", {})
        acq = self.manifest.get("config", {}).get("acquisition", {})
        if src["raw_dtype"] == "<u2":
            bias = float(cal.get("bias_mv", 1650.0))
            gain = float(cal.get("gain", acq.get("amp_gain", 1.0)))
            return (pin - bias) / gain
        bits, vref = int(acq.get("adc_bits", 12)), float(acq.get("adc_vref", 3.3))
        mid_mv = vref * 1000.0 / 2
        return (pin - mid_mv) / float(acq.get("amp_gain", 1.0))


def list_recordings(root: str | Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(Path(root).glob("*/manifest.json"), reverse=True):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"recording_id": m.get("recording_id"), "status": m.get("status"), "started": m.get("started"),
                    "source": m.get("source", {}).get("kind"), "label": m.get("metadata", {}).get("label", ""),
                    "samples": m.get("totals", {}).get("samples")})
    return out
