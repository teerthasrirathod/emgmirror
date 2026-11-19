"""The common pipeline every source feeds.

    DataSource -> Acquisition.poll() -> StreamMonitor (integrity, diagnostics)
                                     -> RawRecorder   (raw layer, optional)
                                     -> read() in mV  (live mirror, guided recorder, analysis)

`Acquisition` is itself source-compatible (fs, channels, start, read, stop), so the existing live
mirror and guided recorder use it unchanged. Its `read()` keeps the time base honest: samples the
device or link lost are re-inserted as explicit fill (0 mV, the post-bias baseline) and logged in
`self.fills`, instead of silently shortening the signal.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from .base import Chunk, DataSource
from .monitor import StreamMonitor

MAX_FILL_S = 2.0  # never invent more than this per gap; longer gaps end the contiguous segment


class Acquisition:
    def __init__(self, source: DataSource, cfg: dict | None = None):
        self.src = source
        cfg = cfg or {}
        self.cfg = cfg
        self.monitor = StreamMonitor(source, thresholds=cfg.get("diagnostics"),
                                     mains_hz=cfg.get("acquisition", {}).get("mains_hz", 50.0))
        self.recorder = None
        self._pending: list[Chunk] = []
        self._lock = threading.RLock()
        self.fills: list[dict[str, Any]] = []  # gaps filled in read(): index, samples, cause
        self.delivered = 0  # samples handed out by read(), including fill
        self.started = False

    # source-compatible surface
    @property
    def fs(self) -> float:
        return self.src.fs

    @property
    def channels(self):
        return self.src.channels

    @property
    def info(self):
        return self.src.info

    def start(self) -> None:
        if not self.started:
            self.src.start()
            self.started = True
            # The source may have learned its real rate during the handshake.
            self.monitor._alloc()

    def stop(self) -> None:
        with self._lock:
            if self.recorder is not None:
                abort = getattr(self, "abort_reason", None)  # a safety abort never closes as "complete"
                self.stop_recording(abort or "stopped with the source", status="interrupted" if abort else "complete")
        if self.started:
            self.src.stop()
            self.started = False

    # pump
    def poll(self) -> list[Chunk]:
        """Pull new chunks from the source, account for them, record them. Returns them."""
        chunks = self.src.read_chunks()
        with self._lock:
            for c in chunks:
                self.monitor.update(c)
                if self.recorder is not None:
                    self.recorder.write(c)
            self._pending.extend(chunks)
            if len(self._pending) > 20000:  # nobody consumes read(): keep memory bounded
                del self._pending[:-20000]
        return chunks

    def read(self) -> np.ndarray:
        self.poll()
        with self._lock:
            chunks, self._pending = self._pending, []
        if not chunks:
            return np.empty((0, len(self.channels)))
        parts = []
        for c in chunks:
            if c.gap_before:
                n = min(c.gap_before, int(MAX_FILL_S * self.fs))
                at = self.delivered + sum(len(p) for p in parts)
                parts.append(np.zeros((n, len(self.channels))))
                self.fills.append({"at_delivered_sample": at, "device_index": int(c.index[0]),
                                   "missing": int(c.gap_before), "filled": n, "cause": c.gap_cause})
            parts.append(np.nan_to_num(c.mv, nan=0.0))
        out = np.vstack(parts)
        self.delivered += len(out)
        return out

    # recording
    def start_recording(self, recorder) -> None:
        with self._lock:
            if self.recorder is not None:
                raise RuntimeError("already recording")
            self.recorder = recorder
            recorder.event("source_status", **_jsonable(self.src.status()))

    def stop_recording(self, reason: str = "", status: str = "complete"):
        with self._lock:
            rec, self.recorder = self.recorder, None
        if rec is None:
            return None
        if not self.src.status().get("connected", True) and status == "complete":
            status, reason = "interrupted", reason or "source disconnected"
        rec.event("source_status", **_jsonable(self.src.status()))
        rec.close(status=status, reason=reason)
        return rec

    def mark(self, label: str, **fields: Any) -> None:
        with self._lock:
            if self.recorder is not None:
                # The device run the marker belongs to (the index restarts with every run).
                fields.setdefault("run", self.monitor.last_run)
                self.recorder.mark(label, **fields)

    def report(self) -> dict[str, Any]:
        rec = self.recorder.summary() if self.recorder is not None else None
        r = self.monitor.report(recording=rec)
        r["fills"] = self.fills[-20:]
        return r


def _jsonable(d: dict) -> dict:
    import json

    return json.loads(json.dumps(d, default=str))
