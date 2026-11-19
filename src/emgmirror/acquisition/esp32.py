"""ESP32 acquisition source: EMGM/2 over a serial transport (firmware/emg_esp32_v2).

Separation of concerns:
- `emgm2` knows the bytes. This module knows the device: handshake, sample indexing across 32-bit
  wrap, run restarts, loss accounting, unit conversion, reconnection.
- Nothing here filters or analyses. Chunks go to the common pipeline unchanged.

Loss accounting (all counts in samples per channel):
- device_dropped: the device's DMA pool overflowed; the firmware still advances the sample index,
  so the jump is exact. Flagged FLAG_AFTER_DROP on the next frame.
- link_lost: frames lost or corrupted between device and host (frame_seq gaps, CRC failures).
- invalid: samples the device marked 0xFFFF (a row did not get all its conversions).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable

import numpy as np

from ..storage import Channel
from . import emgm2
from .base import Chunk, DataSource, SourceInfo

DEFAULT_BIAS_MV = 1650.0  # mid-rail of a 3.3 V supply; the front-end's real idle level is not measured


def _raise_thread_priority() -> None:
    """Give the serial reader thread CPU first. On a busy 2-core laptop other programs can otherwise
    starve it long enough for the OS receive buffer to overflow. Windows only; no effect elsewhere."""
    import sys

    if sys.platform == "win32":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            k.SetThreadPriority(k.GetCurrentThread(), 2)  # THREAD_PRIORITY_HIGHEST
        except Exception:
            pass


class HandshakeError(ConnectionError):
    """The port opened but no valid EMGM/2 HELLO arrived."""


MIN_CONV_HZ = 20000  # ESP32 continuous-ADC minimum conversion rate (firmware MIN_CONV_HZ)
MAX_OS = 16  # firmware MAX_OS


def min_rate_hz(nch: int) -> int:
    """Lowest output rate per channel at which emg_esp32_v2 can sample `nch` channels:
    fs * os * nch >= 20 kHz with os at most 16 (and the firmware accepts nothing below 500 Hz)."""
    return max(500, -(-MIN_CONV_HZ // (MAX_OS * max(nch, 1))))


def not_sampling_message(nch: int, fs: float, os_: str | None) -> str:
    return (f"device is not sampling: it still reports running=0 after START (fs={fs:g} Hz, {nch} channel(s), "
            f"os={os_}). The ESP32 ADC needs fs x os x channels >= {MIN_CONV_HZ} conversions/s with os <= "
            f"{MAX_OS}, so with {nch} channel(s) the rate must be at least {min_rate_hz(nch)} Hz: send a higher "
            "RATE (e.g. `emgmirror acq probe --rate 2000`) or reset the board.")


def open_serial(port: str, baud: int):
    import serial

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, baud, 0.1
    # Keep DTR/RTS released so opening the port does not pulse EN and reboot the board.
    s.dtr = False
    s.rts = False
    s.open()
    # Windows' default receive buffer is 4 KB (~0.2 s of EMGM/2 at 2 kHz). A busy host can stall
    # longer than that; 1 MB (~55 s) turns such stalls into latency instead of link losses.
    if hasattr(s, "set_buffer_size"):
        try:
            s.set_buffer_size(rx_size=1 << 20, tx_size=4096)
        except Exception:
            pass
    return s


class Esp32Source(DataSource):
    def __init__(self, port: str, channels: list[Channel], acq: dict, hardware: dict | None = None,
                 transport_factory: Callable[[], Any] | None = None, handshake_timeout_s: float = 5.0,
                 auto_reconnect: bool = True, reconnect_timeout_s: float = 30.0, apply_pins: bool = True):
        self.port = port
        self.apply_pins = apply_pins
        self._pins_requested = False
        # A HELLO with running=0 (e.g. after a RATE the ADC cannot run at): send START once, then
        # report it if the device still does not sample.
        self._start_requested = False
        self._start_acked = False
        self.acq = acq
        self.hardware = hardware or {}
        baud = int(acq.get("serial_baud", 500000))
        self._factory = transport_factory or (lambda: open_serial(port, baud))
        self.handshake_timeout_s = handshake_timeout_s
        self.auto_reconnect = auto_reconnect
        self.reconnect_timeout_s = reconnect_timeout_s
        self.gain = float(acq.get("amp_gain", 1.0))
        bias = acq.get("bias_mv")
        self.bias_mv = DEFAULT_BIAS_MV if bias in (None, "auto") else float(bias)
        self.info = SourceInfo(
            kind="esp32", label=f"ESP32 on {port}", fs_nominal=float(acq.get("fs", 2000)), channels=list(channels),
            is_real_hardware=True, raw_dtype="<u2", raw_units="sum of `os` 12-bit ADC codes (0xFFFF = invalid)",
            device={"port": port}, hardware=self.hardware,
        )
        self.decoder = emgm2.StreamDecoder()
        self.ser = None
        self.hello: dict[str, str] | None = None
        self.device_status: dict[str, str] = {}
        self.acks: deque = deque(maxlen=20)
        self.events: deque = deque(maxlen=200)  # (t, level, text) for diagnostics and the raw recorder
        self._queue: deque[Chunk] = deque()
        self._lock = threading.Lock()
        self._run_thread = False
        self._thread: threading.Thread | None = None
        self._hello_evt = threading.Event()
        self.connected = False
        self.error: str | None = None
        # stream state
        self.run = 0  # host-side run counter, increments on every device (re)start
        self._dev_run: str | None = None
        self._idx_base = 0
        self._last_idx32: int | None = None
        self._next_index: int | None = None
        self._last_seq: int | None = None
        self.cal_a = 0.8059
        self.cal_b = 0.0
        self.os_ = 4
        # counters
        self.samples = 0
        self.device_dropped = 0
        self.link_lost = 0
        self.link_lost_frames = 0
        self.invalid = 0
        self.pre_hello_frames = 0
        self.reconnects = 0
        self.max_read_gap_s = 0.0
        self.read_gaps: deque = deque(maxlen=2000)
        self.read_sizes: deque = deque(maxlen=2000)
        self.last_data_host_t: float | None = None
        self.last_data_device_us: int | None = None

    # ---------------- lifecycle ----------------
    def _start(self) -> None:
        self._open()
        self._run_thread = True
        self._thread = threading.Thread(target=self._reader, name="esp32-reader", daemon=True)
        self._thread.start()
        # Ask for the HELLO until it arrives. A single INFO sent right after opening the port was seen
        # to go unanswered on this board (CH340, Windows), while data kept flowing; INFO is idempotent.
        deadline = time.time() + self.handshake_timeout_s
        sent = 0
        while not self._hello_evt.is_set() and time.time() < deadline:
            if not (self._pins_requested or self._start_requested) or sent == 0:
                try:
                    self.send_command("INFO")
                    sent += 1
                except Exception as exc:  # port died during the handshake: report it as a handshake failure
                    self._event("error", f"could not send INFO: {exc}")
            self._hello_evt.wait(0.4)
        if sent > 1:
            self._event("info", f"HELLO needed {sent} INFO requests")
        if not self._hello_evt.is_set():
            seen = self.decoder.bytes_dropped + self.decoder.packets
            if self.hello is not None or self._pins_requested or self.decoder.packets:
                # EMGM/2 is flowing, so this is not "wrong firmware": say what happened instead.
                recent = "; ".join(t for _, _, t in list(self.events)[-6:])
                self._stop()
                raise HandshakeError(f"{self.port}: EMGM/2 packets arrive ({self.decoder.packets}) but no matching "
                                     f"HELLO within {self.handshake_timeout_s:.0f} s. Recent events: {recent}")
            self._stop()
            raise HandshakeError(
                f"{self.port} opened, but no EMGM/2 HELLO arrived within {self.handshake_timeout_s:.0f} s "
                f"({seen} bytes or packets seen, {self.decoder.crc_errors} CRC errors). The board is probably running "
                "different firmware (e.g. the text-protocol or pin-survey sketch), or the baud rate differs. "
                "Flash firmware/emg_esp32_v2 or use the matching source type.")
        if self.error and self.error.startswith(("config mismatch", "device is not sampling")):
            msg = self.error
            self._stop()
            raise HandshakeError(msg)

    def _open(self) -> None:
        self.ser = self._factory()
        self.connected = True
        self.error = None
        self._event("info", f"transport opened ({self.port})")

    def _stop(self) -> None:
        self._run_thread = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.connected = False

    def send_command(self, cmd: str) -> None:
        if self.ser is None:
            raise ConnectionError("not connected")
        self.ser.write((cmd.strip() + "\n").encode("ascii"))

    # ---------------- reader ----------------
    def _reader(self) -> None:
        _raise_thread_priority()
        last = time.perf_counter()
        while self._run_thread:
            try:
                data = self.ser.read(max(1, getattr(self.ser, "in_waiting", 0) or 1))
            except Exception as exc:
                self._on_disconnect(exc)
                if not self._run_thread:
                    return
                continue
            now = time.perf_counter()
            # Longest time between two reads: if this exceeds what the OS receive buffer holds, bytes are
            # lost before Python sees them (shows up as link loss and CRC errors).
            gap = now - last
            last = now
            self.read_gaps.append(gap)
            if gap > self.max_read_gap_s:
                self.max_read_gap_s = gap
            if data:
                self.read_sizes.append(len(data))
                self.process_bytes(data)

    def _on_disconnect(self, exc: Exception) -> None:
        self.connected = False
        self.error = f"transport lost: {exc}"
        self._event("error", self.error)
        try:
            self.ser.close()
        except Exception:
            pass
        if not self.auto_reconnect:
            self._run_thread = False
            return
        deadline = time.time() + self.reconnect_timeout_s
        while self._run_thread and time.time() < deadline:
            time.sleep(0.5)
            try:
                self._open()
            except Exception:
                continue
            self.reconnects += 1
            self.decoder = emgm2.StreamDecoder()  # resynchronise on the next delimiter
            self._event("warning", f"reconnected (attempt succeeded after loss: {exc})")
            try:
                self.send_command("INFO")
            except Exception:
                pass
            return
        self._run_thread = False
        self.error = f"transport lost and not recovered within {self.reconnect_timeout_s:.0f} s: {exc}"
        self._event("error", self.error)

    def process_bytes(self, data: bytes) -> None:
        """Feed raw bytes (the reader thread does this; tests can call it directly)."""
        crc_before = self.decoder.crc_errors + self.decoder.frame_errors
        for pkt in self.decoder.feed(data):
            self._on_packet(pkt)
        bad = self.decoder.crc_errors + self.decoder.frame_errors - crc_before
        if bad:
            self._event("warning", f"{bad} corrupted packet(s) discarded")

    def _on_packet(self, pkt: emgm2.Packet) -> None:
        if pkt.type == emgm2.HELLO:
            self._on_hello(pkt.fields)
        elif pkt.type == emgm2.DATA:
            self._on_data(pkt.frame)
        elif pkt.type == emgm2.STATUS:
            self.device_status = pkt.fields
            self._on_status_run(pkt.fields)
        elif pkt.type == emgm2.LOG:
            level = pkt.fields.get("level", "info")
            self._event(level, f"device: {pkt.text}")
        elif pkt.type == emgm2.ACK:
            self.acks.append(pkt.fields)
            self._event("info", f"ack: {pkt.text}")
            if pkt.fields.get("cmd", "").upper() == "START" and self._start_requested:
                self._start_acked = True  # the HELLO that follows this ACK reports the outcome

    def _on_hello(self, f: dict[str, str]) -> None:
        if f.get("proto") != emgm2.PROTOCOL:
            self.error = f"unsupported protocol {f.get('proto')!r} (expected {emgm2.PROTOCOL})"
            self._event("error", self.error)
            return
        problems = []
        nch = int(f.get("nch", 0))
        if nch != len(self.channels):
            problems.append(f"device sends {nch} channels, config defines {len(self.channels)}")
        pins = [int(p) for p in f.get("pins", "").split(",") if p]
        want = [c.get("gpio") for c in self.hardware.get("channels", [])]
        if want and pins != want:
            problems.append(f"device pins {pins} != configured hardware pins {want}")
        fs = float(f.get("fs", 0))
        if problems and want and len(want) == len(self.channels) and self.apply_pins and not self._pins_requested:
            # The config is the source of truth for the channel map: ask the device to adopt it
            # (firmware 2.1+ stores it in NVS) and wait for the HELLO that follows.
            self._pins_requested = True
            self._event("info", f"device pins {pins} differ from config {want}: sending PINS")
            try:
                self.send_command("PINS " + ",".join(str(g) for g in want))
            except Exception as exc:
                self._event("error", f"could not send PINS: {exc}")
            return
        if f.get("running") == "0" and not problems:
            if not self._start_requested:
                # Sampling stopped (STOP, or a RATE/PINS the ADC could not start with): ask once.
                self._start_requested, self._start_acked = True, False
                self._event("warning", "device reports running=0: sending START")
                try:
                    self.send_command("START")
                except Exception as exc:
                    self._event("error", f"could not send START: {exc}")
                return
            if not self._start_acked:
                return  # a HELLO sent before our START was handled; the one after its ACK decides
            self.error = not_sampling_message(nch, fs, f.get("os"))
            self._event("error", self.error)
        elif f.get("running") != "0":
            self._start_requested = self._start_acked = False
            if (self.error or "").startswith("device is not sampling"):
                self.error = None
        if problems:
            self.error = "config mismatch: " + "; ".join(problems)
            self._event("error", self.error)
        elif (self.error or "").startswith("config mismatch"):
            self.error = None
        first = self.hello is None
        self.hello = f
        self.cal_a = float(f.get("cal_a", self.cal_a))
        self.cal_b = float(f.get("cal_b", self.cal_b))
        self.os_ = int(f.get("os", self.os_))
        if fs and fs != self.info.fs_nominal:
            if first:
                self._event("warning", f"device rate {fs:g} Hz differs from config {self.info.fs_nominal:g} Hz; "
                                       "using the device rate")
            self.info.fs_nominal = fs
        self.info.device = {"port": self.port, **f}
        self.info.calibration = {
            "raw": f"sum of os={self.os_} conversions per sample; code = raw / os",
            "pin_mv": f"cal_a * code + cal_b with cal_a={self.cal_a}, cal_b={self.cal_b} "
                      f"({f.get('cal', 'unknown')} from the device)",
            "mv": f"(pin_mv - {self.bias_mv:g}) / {self.gain:g}: electrode-referred estimate using an ASSUMED bias "
                  "and a NOMINAL front-end gain (neither measured on this unit)",
            "bias_mv": self.bias_mv, "gain": self.gain, "gain_source": "config acquisition.amp_gain (nominal)",
        }
        self._new_run_if_needed(f.get("run"), reason="hello")
        self._hello_evt.set()

    def _on_status_run(self, f: dict[str, str]) -> None:
        if self.hello is not None and f.get("run") and f.get("run") != self._dev_run:
            # Device restarted a run without a HELLO reaching us (e.g. it was lost). Ask again.
            try:
                self.send_command("INFO")
            except Exception:
                pass

    def _new_run_if_needed(self, dev_run: str | None, reason: str) -> None:
        if dev_run != self._dev_run:
            if self._dev_run is not None:
                self._event("warning", f"device run changed {self._dev_run} -> {dev_run} ({reason}); index restarts")
            self._dev_run = dev_run
            self.run += 1
            self._idx_base = 0
            self._last_idx32 = None
            self._next_index = None
            self._last_seq = None

    def _on_data(self, fr: emgm2.DataFrame) -> None:
        if self.hello is None or (self.error or "").startswith("config mismatch"):
            self.pre_hello_frames += 1
            return
        if fr.flags & emgm2.FLAG_RUN_START and self._next_index not in (None, 0) and fr.first_index == 0:
            self._new_run_if_needed(f"{self._dev_run}+", reason="run-start flag")
        # unwrap the 32-bit index
        if self._last_idx32 is not None and fr.first_index < self._last_idx32 and self._last_idx32 - fr.first_index > 2**31:
            self._idx_base += 2**32
        self._last_idx32 = fr.first_index
        index0 = self._idx_base + fr.first_index
        n = fr.raw.shape[0]
        # frame sequence: frames lost on the link (or discarded for a bad CRC)
        lost_frames = 0
        if self._last_seq is not None:
            lost_frames = (fr.seq - self._last_seq - 1) & 0xFFFFFFFF
            if lost_frames >= 2**31:
                lost_frames = 0
        gap, cause = 0, ""
        if self._next_index is not None:
            gap = index0 - self._next_index
            if gap < 0:
                self._event("error", f"sample index went backwards ({self._next_index} -> {index0}); new run assumed")
                self._new_run_if_needed(f"{self._dev_run}?", reason="index decreased")
                return self._on_data(fr)
            if gap:
                # Every frame carries n rows, so the link share of the gap is exact; the rest the
                # device itself discarded (DMA pool overflow), which it also flags.
                link = min(gap, lost_frames * n)
                dev = gap - link
                self.link_lost += link
                self.link_lost_frames += lost_frames
                self.device_dropped += dev
                cause = "+".join(c for c, k in (("link", link), ("device", dev)) if k)
                self._event("warning", f"{gap} samples missing before index {index0} ({cause})")
        self._last_seq = fr.seq
        self._next_index = index0 + n
        raw = fr.raw
        valid = raw != emgm2.INVALID_SAMPLE
        os_ = max(fr.os, 1)
        code = raw.astype(np.float64) / os_
        pin_mv = self.cal_a * code + self.cal_b
        pin_mv[~valid] = np.nan
        mv = (pin_mv - self.bias_mv) / self.gain
        self.invalid += int((~valid).sum())
        self.samples += n
        now = time.time()
        self.last_data_host_t = now
        t_dev = int(fr.t_us) if fr.t_us >= 0 else None  # negative = the device had no timestamp
        if t_dev is not None:
            self.last_data_device_us = t_dev
        chunk = Chunk(index=np.arange(index0, index0 + n, dtype=np.int64), raw=raw, valid=valid, mv=mv,
                      pin_mv=pin_mv, t_host=now, t_device_us=t_dev, run=self.run, seq=fr.seq, flags=fr.flags,
                      gap_before=gap, gap_cause=cause)
        with self._lock:
            self._queue.append(chunk)
            if len(self._queue) > 4000:  # ~20 s at 2 kHz with nobody reading: keep memory bounded
                self._queue.popleft()
                self._event("warning", "host queue overflow: oldest chunk discarded (nobody is reading)")

    def _event(self, level: str, text: str) -> None:
        self.events.append((time.time(), level, text))

    # ---------------- DataSource ----------------
    def read_chunks(self) -> list[Chunk]:
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
        return out

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "handshake": self.hello is not None,
            "error": self.error,
            "port": self.port,
            "device": self.hello or {},
            "device_status": self.device_status,
            "run": self.run,
            "samples": self.samples,
            "device_dropped_samples": self.device_dropped,
            "link_lost_samples": self.link_lost,
            "link_lost_frames": self.link_lost_frames,
            "invalid_samples": self.invalid,
            "pre_hello_frames": self.pre_hello_frames,
            "reconnects": self.reconnects,
            "reader": {"max_gap_ms": round(self.max_read_gap_s * 1000, 1),
                       "p99_gap_ms": None if not self.read_gaps else round(float(np.percentile(self.read_gaps, 99)) * 1000, 1),
                       "max_read_bytes": max(self.read_sizes) if self.read_sizes else 0},
            "decoder": self.decoder.counters(),
            "last_data_age_s": None if self.last_data_host_t is None else round(time.time() - self.last_data_host_t, 3),
            "events": [{"t": t, "level": lv, "text": tx} for t, lv, tx in list(self.events)[-20:]],
        }
