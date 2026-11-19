"""Live check: second-by-second cues, live signal, and measured latency.

LiveFeed turns chunks into display items as they arrive. Each item covers about 20 ms and holds,
per channel, the band-passed signal range and the envelope, plus the timing needed for latency:

  t_host     host clock when the chunk arrived (time.time())
  t_dev_us   device clock at the last sample of the chunk (EMGM/2), or None

Latency, and what can honestly be known about it:

- device -> host: the two clocks are unrelated, so only the delay ABOVE the fastest packet seen so
  far is measurable (t_host - t_dev, minus the running minimum of the same). That captures queueing
  and USB/serial stalls. The fixed part (serial time of about 2 ms per packet plus the USB-serial
  bridge's latency timer) is not measurable without an external reference and is NOT included.
- host -> screen: the browser and the server run on the same computer and clock, so this is
  measured exactly (render time minus t_host) and reported back to the server.
- sample age on screen: host -> screen + device -> host (above the best) + up to one packet
  (10 samples = 5 ms at 2 kHz) of the oldest sample in it. A lower bound, for the reason above.

CueRunner walks a cue protocol on the host clock. It writes trial_start/trial_end markers into the
raw recording at the device sample index current at that moment, so the analysis sees the same
boundaries the person saw (offset by the display latency, which is logged).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..processing.filters import StreamingFilter
from .base import Chunk


@dataclass
class Cue:
    task: str  # analysis task name (rest, grip_mvc, ...)
    say: str  # what the person reads
    seconds: float
    level: float = 0.0  # target effort 0..1


DEFAULT_CUES = [
    Cue("rest", "RELAX. Forearm resting, hand still", 8),
    Cue("grip_mvc", "SQUEEZE your fist HARD", 3, 1.0),
    Cue("rest", "RELAX", 5),
    Cue("grip_mvc", "SQUEEZE HARD", 3, 1.0),
    Cue("rest", "RELAX", 5),
    Cue("grip_mvc", "SQUEEZE HARD", 3, 1.0),
    Cue("rest", "RELAX", 5),
    Cue("wrist_flex_mvc", "BEND the wrist DOWN toward the palm, hard", 3, 1.0),
    Cue("rest", "RELAX", 5),
    Cue("wrist_ext_mvc", "BEND the wrist UP (back), hard", 3, 1.0),
    Cue("rest", "RELAX", 5),
]


def _pctl(v: list[float], q: float) -> float | None:
    return None if not v else float(np.percentile(v, q))


class LiveFeed:
    def __init__(self, fs: float, nch: int, cfg: dict, bucket_s: float = 0.02, keep_s: float = 15.0):
        p = cfg.get("processing", {})
        band = tuple(p.get("band_hz", (20.0, 450.0)))
        mains = cfg.get("acquisition", {}).get("mains_hz", 50.0)
        self.fs, self.nch = fs, nch
        self.filter = StreamingFilter(fs, nch, band, mains, window_s=0.1)
        self.band, self.mains = band, mains
        self.bucket = max(1, int(round(bucket_s * fs)))
        self._acc: list[np.ndarray] = []
        self._acc_n = 0
        self._seq = 0
        self.items: deque = deque(maxlen=int(keep_s / bucket_s))
        self._lock = threading.Lock()
        self._min_offset: float | None = None
        self.dev_excess_ms: deque = deque(maxlen=250)  # ~5 s of chunks
        self.screen_ms: deque = deque(maxlen=200)
        self.rest_env: list[float] | None = None
        self._rest_samples: list[np.ndarray] = []

    def update(self, chunk: Chunk) -> None:
        if len(chunk) == 0:
            return
        x = np.nan_to_num(chunk.mv, nan=0.0)
        y, env = self.filter.process(x)
        dev_excess = None
        if chunk.t_device_us is not None and chunk.t_device_us >= 0:
            off = chunk.t_host - chunk.t_device_us / 1e6
            if self._min_offset is None or off < self._min_offset:
                self._min_offset = off
            dev_excess = (off - self._min_offset) * 1000.0
            self.dev_excess_ms.append(dev_excess)
        self._acc.append(y)
        self._acc_n += len(y)
        if self._acc_n < self.bucket:
            return
        seg = np.vstack(self._acc)
        self._acc, self._acc_n = [], 0
        rms = np.sqrt(np.mean(seg ** 2, axis=0))
        item = {"seq": self._seq, "t_host": chunk.t_host, "t_dev_us": chunk.t_device_us,
                "index": int(chunk.index[-1]), "dev_excess_ms": None if dev_excess is None else round(dev_excess, 2),
                "lo": np.round(seg.min(axis=0), 5).tolist(), "hi": np.round(seg.max(axis=0), 5).tolist(),
                "rms": np.round(rms, 6).tolist()}
        self._seq += 1
        with self._lock:
            self.items.append(item)
            if self._rest_samples is not None and getattr(self, "_collect_rest", False):
                self._rest_samples.append(rms)

    def since(self, seq: int) -> list[dict]:
        with self._lock:
            return [it for it in self.items if it["seq"] > seq]

    # rest baseline, measured during the first rest cue
    def begin_rest(self) -> None:
        with self._lock:
            self._rest_samples, self._collect_rest = [], True

    def end_rest(self) -> None:
        with self._lock:
            self._collect_rest = False
            if self._rest_samples:
                arr = np.vstack(self._rest_samples)
                self.rest_env = np.median(arr, axis=0).round(6).tolist()

    def note_screen(self, ms: float) -> None:
        if 0 <= ms < 60000:
            self.screen_ms.append(ms)

    def latency(self) -> dict[str, Any]:
        dev = list(self.dev_excess_ms)
        scr = list(self.screen_ms)
        p50 = (_pctl(dev, 50) or 0) + (_pctl(scr, 50) or 0) + 1000 * 10 / self.fs / 2
        p95 = (_pctl(dev, 95) or 0) + (_pctl(scr, 95) or 0) + 1000 * 10 / self.fs
        return {
            "device_to_host_excess_ms": {"p50": _pctl(dev, 50), "p95": _pctl(dev, 95), "max": max(dev) if dev else None},
            "host_to_screen_ms": {"p50": _pctl(scr, 50), "p95": _pctl(scr, 95), "max": max(scr) if scr else None},
            "sample_age_on_screen_ms_lower_bound": {"p50": round(p50, 1), "p95": round(p95, 1)} if scr else None,
            "note": "device->host is the delay above the fastest packet seen; its fixed part is not measurable "
                    "without an external reference, so ages are lower bounds",
        }


class CueRunner:
    """Walks the cues on the host clock and marks the raw recording at each boundary."""

    def __init__(self, acq, feed: LiveFeed, cues: list[Cue] | None = None, on_done=None, guard=None):
        self.acq, self.feed = acq, feed
        self.guard = guard  # callable -> reason to abort (e.g. mains power appeared) or None
        self.cues = list(cues or DEFAULT_CUES)
        self.on_done = on_done
        self.t0: float | None = None
        self.step = -1
        self.done = False
        self.aborted: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def total_s(self) -> float:
        return sum(c.seconds for c in self.cues)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="cue-runner", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "stopped") -> None:
        self.aborted = reason
        self._stop.set()

    def _mark(self, label: str, **fields) -> None:
        idx = self.acq.monitor.last_index
        self.acq.mark(label, index=None if idx is None else idx + 1, t_host=time.time(), **fields)

    def _run(self) -> None:
        self.t0 = time.time()
        first_rest_done = False
        t_edge = self.t0
        for i, cue in enumerate(self.cues):
            self.step = i
            self._mark("trial_start", task=cue.task, level=cue.level, cue=cue.say, step=i)
            if cue.task == "rest" and not first_rest_done:
                self.feed.begin_rest()
            t_edge += cue.seconds
            last_tick = -1
            while time.time() < t_edge:
                if self._stop.wait(0.01):
                    self._mark("trial_end", task=cue.task, step=i, aborted=self.aborted)
                    self._finish()
                    return
                sec = int(time.time() - self.t0)
                if sec != last_tick:  # once a second: safety guard, and latency into the raw recording
                    last_tick = sec
                    reason = self.guard() if self.guard else None
                    if reason:
                        self.stop(reason)
                        continue
                    self.acq.mark("latency", **{k: v for k, v in self.feed.latency().items() if k != "note"})
            if cue.task == "rest" and not first_rest_done:
                self.feed.end_rest()
                first_rest_done = True
            self._mark("trial_end", task=cue.task, step=i)
        self.step = len(self.cues)
        self._finish()

    def _finish(self) -> None:
        self.done = True
        if self.on_done:
            self.on_done(self)

    def state(self) -> dict[str, Any]:
        if self.t0 is None:
            return {"running": False}
        now = time.time()
        el = now - self.t0
        if self.done or self.step >= len(self.cues):
            return {"running": False, "done": True, "aborted": self.aborted, "elapsed_s": round(el, 2),
                    "total_s": self.total_s}
        start = sum(c.seconds for c in self.cues[: self.step])
        cue = self.cues[self.step]
        left = start + cue.seconds - el
        nxt = self.cues[self.step + 1] if self.step + 1 < len(self.cues) else None
        return {"running": True, "step": self.step, "steps": len(self.cues), "task": cue.task, "say": cue.say,
                "active": cue.level > 0, "seconds_left": max(0.0, round(left, 2)), "elapsed_s": round(el, 2),
                "total_s": self.total_s, "next": None if nxt is None else nxt.say,
                "timeline": [{"task": c.task, "active": c.level > 0, "seconds": c.seconds} for c in self.cues],
                "t_server": now}
