"""Live mirror pipeline: filter, envelope and left-vs-right ratio per muscle.

The right arm is the moving reference. Whenever the right muscle is active,
the left envelope is expressed as a percentage of it; at rest the ratio is
undefined (None) rather than a meaningless division of two noise floors.
"""

from __future__ import annotations

import numpy as np

from .acquisition.sources import Source
from .processing.filters import StreamingFilter


class LiveMirror:
    def __init__(self, source: Source, cfg: dict, calib_s: float = 2.0):
        self.src = source
        self.fs = source.fs
        p = cfg["processing"]
        self.filter = StreamingFilter(self.fs, len(source.channels), tuple(p["band_hz"]),
                                      cfg["acquisition"]["mains_hz"] if p.get("notch", True) else None,
                                      p["envelope_window_s"])
        self.pairs = {}
        for i, ch in enumerate(source.channels):
            self.pairs.setdefault(ch.muscle, {})[ch.arm] = i
        self.noise = np.full(len(source.channels), np.nan)
        self._calib: list[np.ndarray] = []
        self._calib_n = int(calib_s * self.fs)
        self.ratio_smooth: dict[str, float | None] = {m: None for m in self.pairs}

    def step(self) -> dict | None:
        chunk = self.src.read()
        if len(chunk) == 0:
            return None
        y, env = self.filter.process(chunk)
        if np.isnan(self.noise).any():
            # Noise floor from the first seconds; subject should be relaxed.
            self._calib.append(y)
            got = np.vstack(self._calib)
            if len(got) >= self._calib_n:
                self.noise = np.sqrt(np.mean(got[len(got) // 4:] ** 2, axis=0))
        noise = np.nan_to_num(self.noise, nan=0.0)
        net = np.sqrt(np.maximum(env**2 - noise**2, 0.0))

        ratios = {}
        for m, idx in self.pairs.items():
            if "right" not in idx or "left" not in idx:
                ratios[m] = None
                continue
            r, l = net[idx["right"]], net[idx["left"]]
            active = r > 3 * max(noise[idx["right"]], 1e-4)
            val = 100.0 * l / r if active else None
            prev = self.ratio_smooth[m]
            if val is None:
                self.ratio_smooth[m] = None
            else:
                self.ratio_smooth[m] = val if prev is None else 0.8 * prev + 0.2 * val
            ratios[m] = self.ratio_smooth[m]

        step = max(1, len(y) // 20)
        return {
            "n": int(len(y)),
            "fs": self.fs,
            "channels": [c.name for c in self.src.channels],
            "filtered": y[::step].T.round(4).tolist(),
            "envelope": net.round(4).tolist(),
            "ratio_pct": ratios,
            "calibrated": bool(not np.isnan(self.noise).any()),
        }
