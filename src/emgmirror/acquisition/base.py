"""Hardware-independent data-source interface.

Every input (virtual simulator, replayed session, ESP32 over EMGM/2, the older text firmware, or a
future device) is a `DataSource`. Downstream code sees only `Chunk`s, or, through the older `read()`,
millivolt arrays. No module outside `acquisition/` touches a serial port.

Units and provenance are explicit:

- `Chunk.raw` is the device's native value, unchanged (for the ESP32: the sum of `os` 12-bit ADC
  codes per sample). It is what the raw recorder stores.
- `Chunk.pin_mv` is the voltage at the ADC pin implied by the device's own calibration, if the device
  has an ADC (None for virtual data).
- `Chunk.mv` is an *electrode-referred estimate*: (pin voltage - bias) / nominal front-end gain. The
  gain is a datasheet value until it is measured, so `mv` is not a calibrated muscle voltage.
  `SourceInfo.calibration` records exactly how it was computed.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..storage import Channel


@dataclass
class SourceInfo:
    kind: str  # "virtual" | "replay" | "esp32" | "serial-text" | "esp32-emulator" | ...
    label: str  # human-readable, e.g. "ESP32 on COM9"
    fs_nominal: float
    channels: list[Channel]
    is_real_hardware: bool
    raw_dtype: str  # numpy dtype of Chunk.raw, e.g. "<u2" or "<f4"
    raw_units: str  # e.g. "sum of 4 ADC codes (12-bit)" or "mV (synthetic)"
    device: dict[str, Any] = field(default_factory=dict)  # identity reported by the device
    calibration: dict[str, Any] = field(default_factory=dict)  # how raw -> pin_mv -> mv was done
    hardware: dict[str, Any] = field(default_factory=dict)  # channel map and front-end, from config

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["channels"] = [asdict(c) for c in self.channels]
        return d


@dataclass
class Chunk:
    """Consecutive samples as received. Missing samples are never invented here.

    index:     (n,) int64 device sample index (unwrapped). Consecutive within a chunk.
    raw:       (n, ch) device-native values (see SourceInfo.raw_dtype / raw_units)
    valid:     (n, ch) bool, False where the device marked the sample invalid
    mv:        (n, ch) float64 electrode-referred estimate, NaN where invalid
    pin_mv:    (n, ch) float64 voltage at the ADC pin, or None for sources without an ADC
    gap_before: samples missing between the previous chunk and this one (same run)
    """

    index: np.ndarray
    raw: np.ndarray
    valid: np.ndarray
    mv: np.ndarray
    pin_mv: np.ndarray | None = None
    t_host: float = field(default_factory=time.time)
    t_device_us: int | None = None  # device clock at the last sample of the chunk
    run: int = 0
    seq: int | None = None  # transport frame sequence, if any
    flags: int = 0
    gap_before: int = 0
    gap_cause: str = ""  # "device" | "link" | "" ; why gap_before is non-zero

    def __len__(self) -> int:
        return len(self.index)


class DataSource:
    """Base class. Subclasses implement `_start`, `_stop` and `read_chunks`."""

    info: SourceInfo

    @property
    def fs(self) -> float:
        return self.info.fs_nominal

    @property
    def channels(self) -> list[Channel]:
        return self.info.channels

    def start(self) -> None:
        self._start()

    def stop(self) -> None:
        self._stop()

    def _start(self) -> None: ...

    def _stop(self) -> None: ...

    def read_chunks(self) -> list[Chunk]:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        """Transport and device state for diagnostics. Keys are source-specific; `connected` is always set."""
        return {"connected": True}

    def read(self) -> np.ndarray:
        """Back-compatible view: all new samples as (n, ch) mV, invalid samples set to 0.

        Used by the live mirror and guided recorder. Missing samples are not filled here; the common
        pipeline (`acquisition.pipeline.Acquisition`) does that, so timing stays correct.
        """
        chunks = self.read_chunks()
        if not chunks:
            return np.empty((0, len(self.channels)))
        return np.nan_to_num(np.vstack([c.mv for c in chunks]), nan=0.0)


def mv_chunk(mv: np.ndarray, index0: int, run: int = 0, gap_before: int = 0, dtype: str = "<f4") -> Chunk:
    """Chunk for sources whose native unit is already millivolts (virtual and replay)."""
    mv = np.asarray(mv, dtype=float)
    n = len(mv)
    return Chunk(index=np.arange(index0, index0 + n, dtype=np.int64), raw=mv.astype(dtype), valid=np.isfinite(mv),
                 mv=mv, run=run, gap_before=gap_before)
