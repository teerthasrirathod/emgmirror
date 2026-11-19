"""A software ESP32 that speaks EMGM/2, with fault injection.

`Esp32Emulator` behaves like a pyserial port (read / write / in_waiting / close) connected to
firmware/emg_esp32_v2. It lets the whole real-hardware path (decoder, Esp32Source, pipeline,
recorder, dashboard) run and be tested without a board. It is never reported as real hardware:
sources built on it have kind "esp32-emulator".

Per-channel signal modes (pin level, before the device's calibration):
  "emg"        mid-rail bias + noise + periodic bursts (a stand-in, not a physiological model)
  "flat_low"   0 V (unpowered front-end: what this rig showed on 2026-11-08)
  "flat_mid"   constant mid-rail, no noise
  "saturated"  pinned at full scale
  "floating"   nothing connected: echoes the previous channel (sample-and-hold charge) + pickup
  "dc<mV>"     a driven pin at a fixed voltage, e.g. "dc240"
  "invalid"    the device marks every sample invalid (0xFFFF)

Faults:
  drop_device_frames   frame numbers the device discards (DMA overflow): index jumps, flag set
  drop_link_frames     frame numbers lost on the wire (bytes never arrive): seq and index jump
  corrupt_frames       frame numbers with one byte flipped on the wire (CRC failure)
  t_jitter_us          random jitter added to device timestamps
  clock_ppm            device clock error relative to the host
  disconnect_after_s   read() raises like an unplugged USB cable after this many seconds
  text_firmware        speak the old text protocol instead (handshake must fail)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from . import emgm2

ADC1_PINS = {32, 33, 34, 35, 36, 39}


@dataclass
class EmulatorConfig:
    fs: int = 2000
    os: int = 4
    rows_per_frame: int = 10
    pins: tuple[int, ...] = (35, 32, 33)
    modes: tuple[str, ...] = ("emg", "emg", "emg")
    pin_modes: dict[int, str] | None = None  # extra GPIO -> mode, for pins selected later with PINS
    cal_a: float = 0.8059
    cal_b: float = 0.0
    seed: int = 1
    drop_device_frames: set[int] = field(default_factory=set)
    drop_link_frames: set[int] = field(default_factory=set)
    corrupt_frames: set[int] = field(default_factory=set)
    t_jitter_us: float = 0.0
    clock_ppm: float = 0.0
    disconnect_after_s: float | None = None
    text_firmware: bool = False
    status_period_s: float = 1.0
    run_id: int = 1
    running: bool = True  # False: booted with sampling stopped (HELLO running=0)
    min_conv_hz: int | None = None  # like the firmware: START/RATE fail when fs*16*nch < this (20000)


class EmulatorDisconnected(OSError):
    pass


class Esp32Emulator:
    """Byte-level device. Time comes from `clock` so tests can drive it deterministically."""

    def __init__(self, cfg: EmulatorConfig | None = None, clock=time.perf_counter, realtime: bool = True):
        self.cfg = cfg or EmulatorConfig()
        # What is "wired" to each GPIO; used when the host changes the pin map with PINS.
        self.pin_modes = {**dict(zip(self.cfg.pins, self.cfg.modes)), **(self.cfg.pin_modes or {})}
        self.clock = clock
        self.realtime = realtime
        self.rng = np.random.default_rng(self.cfg.seed)
        self.t0 = clock()
        self.out = bytearray()
        self.frames_made = 0  # conversion frames produced (incl. dropped)
        self.seq = 0
        self.overflow = 0
        self.running = self.cfg.running
        self.run_id = self.cfg.run_id
        self.closed = False
        self.last_status = 0.0
        self.commands: list[str] = []
        self._cmd = b""
        self._pending_drop = False
        self._first = True
        self.timeout = 0.1
        # Like the real board: it has been streaming before the host opened the port, so the first
        # bytes the host sees are the tail of a packet.
        self.out += b"\x17\x42\x99\x00"
        if not self.cfg.text_firmware:
            self._hello()

    # ---------- pyserial-like API ----------
    @property
    def in_waiting(self) -> int:
        self._advance()
        return len(self.out)

    def read(self, n: int = 1) -> bytes:
        if self.closed:
            raise EmulatorDisconnected("port closed")
        self._advance()
        if not self.out and self.realtime:
            time.sleep(min(self.timeout, 0.005))
            self._advance()
        data = bytes(self.out[:n])
        del self.out[:n]
        return data

    def write(self, data: bytes) -> int:
        if self.closed:
            raise EmulatorDisconnected("port closed")
        self._cmd += data
        while b"\n" in self._cmd:
            line, self._cmd = self._cmd.split(b"\n", 1)
            self._command(line.decode("ascii", "replace").strip())
        return len(data)

    def reset_input_buffer(self) -> None:
        self._advance()
        self.out.clear()

    def close(self) -> None:
        self.closed = True

    # ---------- device behaviour ----------
    def _elapsed(self) -> float:
        el = self.clock() - self.t0
        dc = self.cfg.disconnect_after_s
        if dc is not None and el >= dc and not self.closed:
            self.closed = True
            raise EmulatorDisconnected("device disconnected (emulated USB unplug)")
        return el

    def _advance(self) -> None:
        if self.closed:
            raise EmulatorDisconnected("port closed")
        el = self._elapsed()
        c = self.cfg
        if c.text_firmware:
            due = int(el * c.fs + 1e-9)
            while self.frames_made < due:
                vals = self._pin_codes(self.frames_made, 1)[0] // c.os
                self.out += (",".join(str(int(v)) for v in vals) + "\n").encode()
                self.frames_made += 1
            return
        if self.running:
            frame_s = c.rows_per_frame / c.fs
            due = int(el / frame_s + 1e-9)
            while self.frames_made < due:
                self._frame(self.frames_made)
                self.frames_made += 1
        if el - self.last_status >= c.status_period_s:
            self.last_status = el
            self.out += emgm2.encode_text(emgm2.STATUS, f"run={self.run_id} running={int(self.running)} "
                                                        f"rate_dev={c.fs:.2f} index={self.frames_made * c.rows_per_frame} "
                                                        f"frames={self.seq} dma_overflow={self.overflow} "
                                                        "incomplete_rows=0 ts_lost=0 tx_free=8000 "
                                                        f"uptime_ms={int(el * 1000)}")

    def _pin_codes(self, first_row: int, n: int) -> np.ndarray:
        """Sum-of-os ADC codes, (n, nch)."""
        c = self.cfg
        t = (first_row + np.arange(n)) / c.fs
        cols = []
        prev = np.full(n, 1650.0)  # what the sample-and-hold capacitor held before the first channel
        for k, mode in enumerate(c.modes):
            if mode == "emg":
                burst = (np.sin(2 * math.pi * 0.25 * t + k) > 0.6).astype(float)
                v = 1650 + self.rng.normal(0, 4, n) + burst * self.rng.normal(0, 120, n) \
                    + 15 * np.sin(2 * math.pi * 50 * t)
            elif mode == "flat_low":
                v = np.zeros(n)
            elif mode == "flat_mid":
                v = np.full(n, 1650.0)
            elif mode == "saturated":
                v = np.full(n, 3400.0)
            elif mode == "floating":
                # An unconnected ESP32 ADC input mostly reads the charge the sample-and-hold capacitor
                # kept from the previous channel in the scan, plus pickup. So its value depends on the
                # scan order, which is how the host's pin scan tells floating from driven pins.
                v = np.nan_to_num(prev, nan=1650.0) + 40 * np.sin(2 * math.pi * 50 * t + k) \
                    + self.rng.normal(0, 30, n)
            elif mode.startswith("dc"):  # "dc1234" = a driven pin at 1234 mV with a little noise
                v = float(mode[2:]) + self.rng.normal(0, 3, n)
            elif mode == "invalid":
                v = np.full(n, np.nan)
            else:
                raise ValueError(f"unknown emulator mode {mode}")
            prev = v
            code = np.clip((v - c.cal_b) / c.cal_a, 0, 4095) * c.os
            code = np.where(np.isnan(v), emgm2.INVALID_SAMPLE, np.round(code))
            cols.append(code)
        return np.column_stack(cols).astype(np.uint32)

    def _frame(self, k: int) -> None:
        c = self.cfg
        if k in c.drop_device_frames:
            self.overflow += 1
            self._pending_drop = True
            return
        first = k * c.rows_per_frame
        raw = self._pin_codes(first, c.rows_per_frame)
        t_us = (first + c.rows_per_frame) / c.fs * 1e6 * (1 + c.clock_ppm * 1e-6)
        if c.t_jitter_us:
            t_us += self.rng.normal(0, c.t_jitter_us)
        flags = (emgm2.FLAG_RUN_START if self._first else 0) | (emgm2.FLAG_AFTER_DROP if self._pending_drop else 0)
        if (raw == emgm2.INVALID_SAMPLE).any():
            flags |= emgm2.FLAG_INCOMPLETE_ROW
        pkt = emgm2.encode_data(self.seq, first, int(max(t_us, 0)), self.overflow, raw.astype(np.uint16), c.os, flags)
        self.seq += 1
        self._first = False
        self._pending_drop = False
        if k in c.drop_link_frames:
            return
        if k in c.corrupt_frames:
            b = bytearray(pkt)
            i = len(b) // 2
            b[i] = b[i] ^ 0x5A or 0x01  # never introduce a 0x00 delimiter
            pkt = bytes(b)
        self.out += pkt

    def _hello(self) -> None:
        c = self.cfg
        pins = ",".join(str(p) for p in c.pins)
        self.out += emgm2.encode_text(
            emgm2.HELLO,
            f"proto={emgm2.PROTOCOL} fw=emg_esp32_v2/2.1.0-emulator built=emulator chip=EMULATOR rev=0 mac=000000000000 "
            f"run={self.run_id} running={int(self.running)} fs={c.fs} os={c.os} nch={len(c.modes)} pins={pins} "
            f"adc_unit=1 atten_db=12 bits=12 rows_per_frame={c.rows_per_frame} cal=emulated cal_a={c.cal_a} "
            f"cal_b={c.cal_b} baud=500000")

    def _command(self, cmd: str) -> None:
        self.commands.append(cmd)
        u = cmd.upper()
        if self.cfg.text_firmware:
            return
        if u == "INFO":
            self._hello()
            self.out += emgm2.encode_text(emgm2.ACK, "cmd=INFO ok=1")
        elif u == "STOP":
            self.running = False
            self.out += emgm2.encode_text(emgm2.ACK, "cmd=STOP ok=1")
        elif u == "START" or u.startswith("RATE "):
            if u.startswith("RATE "):
                self.cfg.fs = int(u[5:])
            if c_min := self.cfg.min_conv_hz:
                if self.cfg.fs * 16 * len(self.cfg.modes) < c_min:  # adc_continuous_config fails
                    self.running = False
                    self.out += emgm2.encode_text(emgm2.LOG, "level=error msg=adc_start_failed")
                    self.out += emgm2.encode_text(emgm2.ACK, f"cmd={u.split()[0]} ok=0 run={self.run_id}")
                    self._hello()
                    return
            self.running = True
            self.run_id += 1
            self.t0 = self.clock()
            self.frames_made = 0  # sample index restarts; frame_seq keeps counting, as on the device
            self.last_status = 0.0
            self._first = True
            self.out += emgm2.encode_text(emgm2.ACK, f"cmd={u.split()[0]} ok=1 run={self.run_id}")
            self._hello()
        elif u.startswith("PINS "):
            try:
                pins = tuple(int(p) for p in u[5:].replace(" ", "").split(",") if p)
            except ValueError:
                pins = ()
            if not pins or len(pins) > 6 or len(set(pins)) != len(pins) or not set(pins) <= ADC1_PINS:
                self.out += emgm2.encode_text(emgm2.ACK, "cmd=PINS ok=0 error=invalid allowed=32,33,34,35,36,39 max=6")
                return
            self.cfg.pins = pins
            self.cfg.modes = tuple(self.pin_modes.get(p, "floating") for p in pins)
            self._command("START")
        else:
            self.out += emgm2.encode_text(emgm2.ACK, f"cmd={u[:20]} ok=0 error=unknown_command")
