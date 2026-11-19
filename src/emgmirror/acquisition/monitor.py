"""Stream integrity and signal sanity, stage by stage.

The diagnostic model separates five questions, because "the board is plugged in" and "we are
recording muscle activity" are very different claims:

  1. connection   is the transport open?
  2. device       did the device identify itself with a protocol and configuration we accept?
  3. acquisition  are samples arriving at the expected rate, without gaps or invalid samples?
  4. channels     is each pin's voltage electrically plausible (not stuck at a rail, not clipping,
                  not perfectly flat)?
  5. emg          is the signal muscle activity? Never decided automatically for real hardware: it
                  needs electrodes on a person, which is gated by the electrical-safety check.

Each stage reports state "ok" | "warn" | "fail" | "waiting" | "n/a" and a plain-language detail.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

import numpy as np

from .base import Chunk, DataSource

DEFAULT_THRESHOLDS = {
    "rail_low_mv": 100.0,  # ESP32 ADC reads ~0 below ~0.1 V at 12 dB attenuation
    "rail_high_mv": 3000.0,  # above this the ESP32 ADC is near full scale at 12 dB
    "flat_p2p_mv": 2.0,  # peak-to-peak over the window below this = no variation at all
    "clip_fraction": 0.01,  # share of samples at a code limit that counts as clipping
    "rate_tolerance": 0.02,  # observed vs nominal rate
    "stall_s": 1.0,
    "window_s": 1.0,
    "mains_fraction": 0.6,  # share of AC power at mains harmonics that counts as "mostly hum"
}


class StreamMonitor:
    def __init__(self, source: DataSource, thresholds: dict | None = None, mains_hz: float = 50.0,
                 display_s: float = 5.0):
        self.src = source
        self.th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.mains_hz = mains_hz
        self.nch = len(source.channels)
        self.display_s = display_s
        self._alloc()
        self.t_start = time.time()
        self.samples = 0
        self.missing = 0
        self.missing_by_cause: dict[str, int] = {}
        self.invalid = 0
        self.clipped = np.zeros(self.nch, dtype=np.int64)
        self.first_index: int | None = None
        self.last_index: int | None = None
        self.last_run: int | None = None
        self.last_host_t: float | None = None
        self.runs: set[int] = set()
        self._rate_pts: deque = deque(maxlen=400)  # (t_host, index_end, t_device_us)
        self.gaps: deque = deque(maxlen=200)  # (index, n, cause, run)

    def _alloc(self) -> None:
        self.cap = max(int(self.src.fs * self.display_s), 1)
        self.ring = np.full((self.cap, self.nch), np.nan)
        self.ring_mv = np.full((self.cap, self.nch), np.nan)
        self.wpos = 0
        self.filled = 0
        self._fs_alloc = self.src.fs

    def update(self, chunk: Chunk) -> None:
        if self.src.fs != self._fs_alloc:
            self._alloc()
        n = len(chunk)
        if n == 0:
            return
        self.runs.add(chunk.run)
        if chunk.gap_before:
            self.missing += chunk.gap_before
            for cause in (chunk.gap_cause or "unknown").split("+"):
                self.missing_by_cause[cause] = self.missing_by_cause.get(cause, 0) + chunk.gap_before
            self.gaps.append((int(chunk.index[0]), int(chunk.gap_before), chunk.gap_cause, chunk.run))
        self.samples += n
        self.invalid += int((~chunk.valid).sum())
        if self.first_index is None:
            self.first_index = int(chunk.index[0])
        self.last_index = int(chunk.index[-1])
        self.last_run = chunk.run
        self.last_host_t = chunk.t_host
        self._rate_pts.append((chunk.t_host, int(chunk.index[-1]) + 1, chunk.t_device_us, chunk.run))
        pin = chunk.pin_mv if chunk.pin_mv is not None else chunk.mv
        if chunk.raw.dtype == np.uint16 and self.src.info.kind.startswith("esp32"):
            os_ = int(self.src.info.device.get("os", 1) or 1)
            r = chunk.raw.astype(np.int64)
            self.clipped += ((r == 0) | ((r >= 4095 * os_) & chunk.valid)).sum(axis=0)
        # ring buffer for display and windowed stats
        for arr, buf in ((pin, self.ring), (chunk.mv, self.ring_mv)):
            if n >= self.cap:
                buf[:] = arr[-self.cap:]
            else:
                end = self.wpos + n
                if end <= self.cap:
                    buf[self.wpos:end] = arr
                else:
                    k = self.cap - self.wpos
                    buf[self.wpos:] = arr[:k]
                    buf[:n - k] = arr[k:]
        self.wpos = (self.wpos + n) % self.cap if n < self.cap else 0
        self.filled = min(self.filled + n, self.cap)

    def _last(self, buf: np.ndarray, n: int) -> np.ndarray:
        n = min(n, self.filled)
        if n == 0:
            return np.empty((0, self.nch))
        idx = (self.wpos - n + np.arange(n)) % self.cap
        return buf[idx]

    # ---------------- rates ----------------
    def rates(self) -> dict[str, Any]:
        fs = self.src.fs
        out: dict[str, Any] = {"nominal_hz": fs, "host_window_hz": None, "device_clock_hz": None,
                               "host_since_start_hz": None}
        pts = [p for p in self._rate_pts if p[3] == (self._rate_pts[-1][3] if self._rate_pts else None)]
        now = time.time()
        recent = [p for p in pts if now - p[0] <= 5.0]
        if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
            out["host_window_hz"] = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
        dev = [p for p in pts if p[2] is not None and p[2] >= 0]
        if len(dev) >= 2 and dev[-1][2] > dev[0][2]:
            out["device_clock_hz"] = (dev[-1][1] - dev[0][1]) / ((dev[-1][2] - dev[0][2]) / 1e6)
        if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
            out["host_since_start_hz"] = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
        return out

    # ---------------- channels ----------------
    def channel_report(self) -> list[dict[str, Any]]:
        th = self.th
        w = int(self.src.fs * th["window_s"])
        pin = self._last(self.ring, w)
        is_adc = self.src.info.kind not in ("virtual", "replay")
        hw = {c.get("ch"): c for c in self.src.info.hardware.get("channels", [])}
        out = []
        for k, ch in enumerate(self.src.channels):
            x = pin[:, k] if len(pin) else np.empty(0)
            good = x[np.isfinite(x)]
            meta = hw.get(k + 1, {})
            r: dict[str, Any] = {"ch": k + 1, "name": ch.name, "muscle": ch.muscle, "arm": ch.arm,
                                 "gpio": meta.get("gpio"), "sensor": meta.get("sensor"),
                                 "units": "mV at ADC pin" if is_adc else "mV (electrode, synthetic or stored)"}
            if len(x) == 0:
                r.update(state="waiting", detail="no samples yet")
                out.append(r)
                continue
            if len(good) == 0:
                r.update(state="fail", detail="every sample in the window is marked invalid by the device")
                out.append(r)
                continue
            mean, lo, hi = float(good.mean()), float(good.min()), float(good.max())
            p2p, std = hi - lo, float(good.std())
            r.update(mean=round(mean, 2), min=round(lo, 2), max=round(hi, 2), p2p=round(p2p, 2), std=round(std, 3),
                     invalid_fraction=round(1 - len(good) / len(x), 4))
            hum = self._mains_fraction(good)
            r["mains_fraction"] = None if hum is None else round(hum, 3)
            if not is_adc:
                r.update(state="ok", detail="virtual/replayed data: electrical checks do not apply")
            elif mean < th["rail_low_mv"] and p2p < 50:
                r.update(state="fail", detail=f"stuck near 0 V (mean {mean:.0f} mV): front-end output not reaching "
                                              "the pin, front-end unpowered, or output shorted low")
            elif mean > th["rail_high_mv"] and p2p < 50:
                r.update(state="fail", detail=f"stuck near full scale (mean {mean:.0f} mV): output pinned high "
                                              "or wired to a supply rail")
            elif self._clip_frac(k, len(x)) > th["clip_fraction"]:
                r.update(state="warn", detail="clipping: samples at the ADC limits")
            elif p2p < th["flat_p2p_mv"]:
                r.update(state="warn", detail=f"flat: {p2p:.2f} mV peak-to-peak at {mean:.0f} mV; constant voltage, "
                                              "no signal variation")
            elif hum is not None and hum > th["mains_fraction"]:
                r.update(state="warn", detail=f"varying, but {hum:.0%} of the AC power is mains hum: typical of "
                                              "open inputs or poor electrode contact")
            else:
                r.update(state="ok", detail="electrically plausible: mid-range and varying (not proof of EMG)")
            out.append(r)
        return out

    def _clip_frac(self, k: int, n: int) -> float:
        return float(self.clipped[k]) / max(self.samples, 1)

    def _mains_fraction(self, x: np.ndarray) -> float | None:
        fs = self.src.fs
        if len(x) < fs * 0.5 or not self.mains_hz:
            return None
        x = x - x.mean()
        p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        f = np.fft.rfftfreq(len(x), 1 / fs)
        ac = p[f >= 10].sum()
        if ac <= 0:
            return None
        mask = np.zeros_like(f, dtype=bool)
        h = self.mains_hz
        while h < fs / 2:
            mask |= np.abs(f - h) <= 2.0
            h += self.mains_hz
        return float(p[mask].sum() / ac)

    # ---------------- stages ----------------
    def report(self, recording: dict | None = None) -> dict[str, Any]:
        info = self.src.info
        st = self.src.status()
        rates = self.rates()
        chans = self.channel_report()
        stages: dict[str, dict[str, str]] = {}

        if st.get("connected"):
            stages["connection"] = {"state": "ok", "detail": st.get("port") and f"open on {st['port']}" or "open"}
        else:
            stages["connection"] = {"state": "fail", "detail": st.get("error") or "not connected"}

        if info.kind in ("virtual", "replay"):
            stages["device"] = {"state": "n/a", "detail": f"{info.kind} source, no device"}
        elif info.kind == "serial-text":
            stages["device"] = {"state": "warn", "detail": "legacy text protocol: no handshake, no sample index, "
                                                           "lost samples cannot be detected"}
        elif st.get("handshake") and not (st.get("error") or "").startswith("config mismatch"):
            d = st.get("device", {})
            stages["device"] = {"state": "ok", "detail": f"{d.get('fw')} on {d.get('chip')} rev {d.get('rev')} "
                                                         f"(MAC {d.get('mac')}), pins {d.get('pins')}"}
        elif st.get("error"):
            stages["device"] = {"state": "fail", "detail": st["error"]}
        else:
            stages["device"] = {"state": "waiting", "detail": "waiting for the device HELLO"}

        age = None if self.last_host_t is None else time.time() - self.last_host_t
        nominal = rates["nominal_hz"]
        observed = rates["device_clock_hz"] or rates["host_window_hz"]
        if self.samples == 0:
            stages["acquisition"] = {"state": "waiting", "detail": "no samples received yet"}
        elif age is not None and age > self.th["stall_s"]:
            stages["acquisition"] = {"state": "fail", "detail": f"stalled: no samples for {age:.1f} s"}
        elif observed and abs(observed / nominal - 1) > self.th["rate_tolerance"]:
            stages["acquisition"] = {"state": "fail", "detail": f"rate {observed:.1f} Hz vs nominal {nominal:g} Hz"}
        elif self.missing or self.invalid:
            stages["acquisition"] = {"state": "warn", "detail": f"{self.missing} samples missing "
                                                                f"({self.missing_by_cause}), {self.invalid} invalid, "
                                                                f"of {self.samples + self.missing}"}
        else:
            stages["acquisition"] = {"state": "ok", "detail": f"{self.samples} samples, none missing"
                                                              + (f", {observed:.1f} Hz" if observed else "")}

        states = [c["state"] for c in chans]
        if all(s == "waiting" for s in states):
            stages["channels"] = {"state": "waiting", "detail": "no samples yet"}
        else:
            worst = "fail" if "fail" in states else "warn" if "warn" in states else "ok"
            stages["channels"] = {"state": worst, "detail": "; ".join(f"CH{c['ch']} {c['state']}" for c in chans)}

        if info.kind == "virtual":
            stages["emg"] = {"state": "n/a", "detail": "synthetic signals from the simulator, not a person"}
        elif info.kind == "replay":
            stages["emg"] = {"state": "n/a", "detail": "replay of a saved session"}
        elif info.kind == "esp32-emulator":
            stages["emg"] = {"state": "n/a", "detail": "emulated device, not real hardware"}
        else:
            stages["emg"] = {"state": "not_verified",
                             "detail": "EMG capture is not verified. Electrode tests are blocked until the electrical "
                                       "safety gate passes (docs/ELECTRICAL_SAFETY.md)."}

        return {
            "type": "status",
            "t": time.time(),
            "source": {"kind": info.kind, "label": info.label, "is_real_hardware": info.is_real_hardware},
            "stages": stages,
            "rates": {k: (None if v is None else round(v, 3)) for k, v in rates.items()},
            "counts": {"samples": self.samples, "missing": self.missing, "missing_by_cause": self.missing_by_cause,
                       "invalid": self.invalid, "runs": len(self.runs),
                       "first_index": self.first_index, "last_index": self.last_index},
            "channels": chans,
            "transport": {k: v for k, v in st.items() if k not in ("device",)},
            "recording": recording,
        }

    def waveform(self, seconds: float = 2.0, points: int = 600) -> dict[str, Any]:
        """Last `seconds` per channel, min/max decimated so spikes survive."""
        n = int(self.src.fs * seconds)
        x = self._last(self.ring, n)
        if len(x) == 0:
            return {"seconds": seconds, "series": [[] for _ in range(self.nch)]}
        step = max(1, len(x) // (points // 2))
        series = []
        for k in range(self.nch):
            col = x[:, k]
            m = len(col) // step * step
            if m == 0:
                series.append([])
                continue
            blk = col[:m].reshape(-1, step)
            with np.errstate(all="ignore"):
                lo, hi = np.nanmin(blk, axis=1), np.nanmax(blk, axis=1)
            inter = np.empty(2 * len(lo))
            inter[0::2], inter[1::2] = lo, hi
            series.append([None if not np.isfinite(v) else round(float(v), 2) for v in inter])
        return {"seconds": round(len(x) / self.src.fs, 3), "series": series}
