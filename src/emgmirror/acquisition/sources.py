"""Virtual and legacy sources. Everything downstream only sees `DataSource` (see base.py).

- ReplaySource: plays a saved session (synthetic or real) back in real time. A synthetic session is
  reported as kind "virtual", a real one as "replay".
- VirtualSource: simulates a fresh session in memory and plays it (no file needed).
- SerialSource: the text protocol of `firmware/emg_stream` and `firmware/emg_candy_esp32`
  (one "a,b,c" line per sample). It has no sample index, so it cannot detect lost samples; prefer
  the EMGM/2 firmware (`acquisition.esp32.Esp32Source`).

All return chunks whose `mv` is in millivolts.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from ..storage import Channel, Session
from .base import Chunk, DataSource, SourceInfo, mv_chunk

Source = DataSource  # older name, kept for callers that type against it


class ReplaySource(DataSource):
    """Real-time playback, looping.

    A sequential session (one block per arm, same protocol) is played as if it
    were mirror mode: the blocks are aligned on their first trial and played
    side by side, so the recorded right arm acts as the moving reference.
    """

    def __init__(self, session: Session, block: int | None = None, speed: float = 1.0):
        fs = session.fs
        blocks = session.blocks if block is None else [session.blocks[block]]
        if len(blocks) > 1 and len({a for b in blocks for a in b.arms}) > 1:
            starts = [int(b.trials[0].start_s * fs) if b.trials else 0 for b in blocks]
            lead = min(starts)
            cut = [s - lead for s in starts]
            n = min(len(b.data) - c for b, c in zip(blocks, cut))
            self.data = np.hstack([b.data[c: c + n] for b, c in zip(blocks, cut)])
            channels = [ch for b in blocks for ch in b.channels]
            shift = cut[0] / fs
            self.trials = [type(t)(t.task, t.start_s - shift, t.end_s - shift, t.level) for t in blocks[0].trials]
        else:
            self.data = blocks[0].data
            channels = blocks[0].channels
            self.trials = blocks[0].trials
        synthetic = session.source == "synthetic"
        self.info = SourceInfo(
            kind="virtual" if synthetic else "replay",
            label=f"{'synthetic' if synthetic else 'recorded'} session {session.session_id}",
            fs_nominal=float(fs), channels=list(channels), is_real_hardware=False,
            raw_dtype="<f4", raw_units="mV (synthetic)" if synthetic else "mV (from saved session)",
            device={"session_id": session.session_id, "session_source": session.source},
            calibration={"mv": "values are the session's stored millivolts; no conversion"},
        )
        self.speed = speed
        self._t0 = None
        self._pos = 0

    def _start(self) -> None:
        self._t0 = time.perf_counter()
        self._pos = 0

    @property
    def position_s(self) -> float:
        return (self._pos % len(self.data)) / self.fs

    def read_chunks(self) -> list[Chunk]:
        due = int((time.perf_counter() - self._t0) * self.fs * self.speed)
        n = due - self._pos
        if n <= 0:
            return []
        idx = np.arange(self._pos, self._pos + n) % len(self.data)
        chunk = mv_chunk(self.data[idx], self._pos)
        self._pos = due
        return [chunk]


class VirtualSource(ReplaySource):
    """Synthetic data generated on the spot by the motor-unit simulator (no file written)."""

    def __init__(self, cfg: dict, mode: str = "mirror", seed: int | None = None, speed: float = 1.0):
        from ..simulate.synthetic import synthetic_session

        c = cfg if seed is None else {**cfg, "simulation": {**cfg["simulation"], "seed": seed}}
        super().__init__(synthetic_session(c, mode=mode, session_id=f"virtual-{mode}"), speed=speed)
        self.info.label = f"virtual {mode} (simulator, seed {c['simulation']['seed']})"


def counts_to_mv(counts: np.ndarray, bits: int, vref: float, gain: float) -> np.ndarray:
    """ADC counts (centred at mid-scale) to millivolts at the electrodes."""
    mid = (2**bits) / 2
    return (counts - mid) / (2**bits) * vref / gain * 1000.0


def parse_line(line: str, n_channels: int) -> list[float] | None:
    """One sample per line: comma-separated integers. Lines starting with # are headers."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(",")
    if len(parts) != n_channels:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


class SerialSource(DataSource):
    """Legacy text firmware: "# emgmirror fs=..." headers and one CSV line per sample."""

    def __init__(self, port: str, channels: list[Channel], acq: dict, transport=None):
        if transport is None:
            import serial  # pyserial, imported lazily so tests need no hardware

            transport = serial.Serial(port, int(acq["serial_baud"]), timeout=0.1)
        self.ser = transport
        self.acq = acq
        bits, vref = int(acq["adc_bits"]), float(acq["adc_vref"])
        self.info = SourceInfo(
            kind="serial-text", label=f"text-protocol device on {port}", fs_nominal=float(acq["fs"]),
            channels=list(channels), is_real_hardware=True, raw_dtype="<f4",
            raw_units=f"ADC counts ({bits}-bit) as sent by the device",
            device={"port": port, "protocol": "emgmirror text v1 (no sample index)"},
            calibration={"pin_mv": f"counts / 2^{bits} * {vref} V (nominal)",
                         "mv": f"(counts - mid-scale) / 2^{bits} * {vref} V / gain {acq['amp_gain']} (nominal)"},
        )
        self.buf: deque = deque(maxlen=int(self.info.fs_nominal * 30))
        self.header: dict[str, str] = {}
        self._run = False
        self._lock = threading.Lock()
        self.dropped = 0
        self._count = 0
        self.error: str | None = None

    def _reader(self) -> None:
        # Chunked reads: pyserial's readline() goes byte by byte and cannot keep up
        # with 2000 lines/s on Windows (measured ~1200/s), silently losing samples.
        n = len(self.channels)
        pending = b""
        while self._run:
            try:
                chunk = self.ser.read(max(1, self.ser.in_waiting))
            except Exception as exc:  # unplugged
                self.error = f"serial read failed: {exc}"
                self._run = False
                break
            if not chunk:
                continue
            pending += chunk
            *lines, pending = pending.split(b"\n")
            rows = []
            for raw in lines:
                line = raw.decode("ascii", errors="ignore")
                if line.startswith("#"):
                    for kv in line[1:].split():
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            self.header[k] = v
                    ch = self.header.get("ch", "").strip()
                    if ch.isdigit() and int(ch) != n and self.error is None:
                        # Every data line would be dropped as malformed: say why instead.
                        self.error = (f"channel count mismatch: the device sends {ch} channel(s), the config "
                                      f"defines {n}. Use a config whose `channels` list matches the firmware "
                                      "(NUM_CHANNELS).")
                    continue
                vals = parse_line(line, n)
                if vals is None:
                    if line.strip():
                        self.dropped += 1
                    continue
                rows.append(vals)
            if rows:
                with self._lock:
                    self.buf.extend(rows)

    def _start(self) -> None:
        self.ser.reset_input_buffer()
        self._run = True
        threading.Thread(target=self._reader, daemon=True).start()

    def read_chunks(self) -> list[Chunk]:
        with self._lock:
            rows = list(self.buf)
            self.buf.clear()
        if not rows:
            return []
        a = self.acq
        counts = np.asarray(rows)
        bits, vref = int(a["adc_bits"]), float(a["adc_vref"])
        mv = counts_to_mv(counts, bits, vref, float(a["amp_gain"]))
        idx = np.arange(self._count, self._count + len(rows), dtype=np.int64)
        self._count += len(rows)
        return [Chunk(index=idx, raw=counts.astype("<f4"), valid=np.ones(counts.shape, bool), mv=mv,
                      pin_mv=counts / 2**bits * vref * 1000.0)]

    def status(self) -> dict:
        return {"connected": self._run and self.error is None, "error": self.error, "header": dict(self.header),
                "malformed_lines": self.dropped, "index_source": "host count (protocol has no sample index)"}

    def _stop(self) -> None:
        self._run = False
        time.sleep(0.2)
        self.ser.close()


def list_serial_ports() -> list[str]:
    from serial.tools import list_ports

    return [f"{p.device}  {p.description}" for p in list_ports.comports()]
