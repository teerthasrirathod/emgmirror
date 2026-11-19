"""Which ADC pins are actually driven by something? An electrical test, no body involved.

The ESP32 ADC samples every channel onto one sample-and-hold capacitor, in the order of the scan
pattern. An input with nothing connected (high impedance) cannot recharge that capacitor in the
sampling window, so it mostly reports what the PREVIOUS channel left there. A pin driven by a real
source (an amplifier output, a divider, even an unpowered chip's protection diodes) reads its own
voltage whatever came before it.

So each pin is scanned in two opposite orders:
  - a driven pin reads the same mean in both orders, and its fluctuations are not copies of its
    predecessor's;
  - a floating pin's mean moves with its predecessor, and it correlates strongly with it.

Pins that only ever follow other floating pins can look alike in both orders. Those come out as
"uncertain" rather than being guessed. The decisive check is physical: unplug the connector and
scan again (see docs/HARDWARE_INVENTORY.md).
"""

from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

from ..storage import Channel
from .esp32 import Esp32Source

DRIVEN_MAX_SHIFT_MV = 30.0
FLOATING_MIN_SHIFT_MV = 100.0
FLOATING_MIN_CORR = 0.8
DRIVEN_MAX_CORR = 0.5


def _collect(port: str, pins: list[int], acq: dict, seconds: float, factory: Callable | None,
             settle_s: float = 0.5) -> tuple[np.ndarray, dict]:
    chans = [Channel(f"gpio{g}", "none", "scan") for g in pins]
    hw = {"channels": [{"ch": k + 1, "gpio": g} for k, g in enumerate(pins)]}
    src = Esp32Source(port, chans, acq, hardware=hw, transport_factory=factory)
    src.start()
    rate = int(acq.get("fs", 0))
    if rate and int(src.info.fs_nominal) != rate:  # scan at the requested rate, not whatever the device ran
        src.send_command(f"RATE {rate}")
        t_end = time.time() + 5
        while int(src.info.fs_nominal) != rate and time.time() < t_end:
            src.read_chunks()
            time.sleep(0.02)
        if int(src.info.fs_nominal) != rate:
            src.stop()
            raise RuntimeError(f"device did not switch to {rate} Hz")
    # Counted in samples, not wall-clock time, so a busy host still gets the full amount.
    fs = src.info.fs_nominal
    skip, want = int(settle_s * fs), int(seconds * fs)
    deadline = time.time() + max(15.0, 5 * (seconds + settle_s))
    try:
        parts, seen = [], 0
        while sum(len(p) for p in parts) < want and time.time() < deadline:
            for c in src.read_chunks():
                if c.run != src.run:
                    continue
                n = len(c)
                if seen + n > skip:  # discard the first samples after the pin change
                    parts.append(c.pin_mv[max(0, skip - seen):])
                seen += n
            time.sleep(0.02)
        status = src.status()
    finally:
        src.stop()
    if not parts:
        raise RuntimeError(f"no samples received while scanning pins {pins}")
    if sum(len(p) for p in parts) < want:
        raise RuntimeError(f"only {sum(len(p) for p in parts)} of {want} samples arrived while scanning pins {pins}")
    return np.vstack(parts), {"fs": src.info.fs_nominal, "device": status["device"], "missing": status["device_dropped_samples"]
                              + status["link_lost_samples"], "invalid": status["invalid_samples"]}


def _mains_fraction(x: np.ndarray, fs: float, mains: float) -> float | None:
    x = x[np.isfinite(x)]
    if len(x) < fs * 0.5:
        return None
    x = x - x.mean()
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / fs)
    ac = p[f >= 10].sum()
    if ac <= 0:
        return None
    m = np.zeros_like(f, dtype=bool)
    h = mains
    while h < fs / 2:
        m |= np.abs(f - h) <= 2.0
        h += mains
    return float(p[m].sum() / ac)


def _order_stats(x: np.ndarray, pins: list[int], fs: float, mains: float) -> dict[int, dict[str, Any]]:
    out = {}
    for k, g in enumerate(pins):
        col = x[:, k]
        # Predecessor in the conversion sequence: the previous column; for the first column, the last
        # column of the previous row.
        prev = x[:, k - 1] if k > 0 else np.roll(x[:, -1], 1)
        good = np.isfinite(col) & np.isfinite(prev)
        a, b = col[good][1:], prev[good][1:]
        corr = None
        if len(a) > 10 and a.std() > 0 and b.std() > 0:
            corr = float(np.corrcoef(a - a.mean(), b - b.mean())[0, 1])
        out[g] = {"mean_mv": round(float(np.nanmean(col)), 1), "std_mv": round(float(np.nanstd(col)), 2),
                  "min_mv": round(float(np.nanmin(col)), 1), "max_mv": round(float(np.nanmax(col)), 1),
                  "predecessor": pins[k - 1] if k > 0 else pins[-1],
                  "corr_with_predecessor": None if corr is None else round(corr, 3),
                  "mains_fraction": None if (m := _mains_fraction(col, fs, mains)) is None else round(m, 3)}
    return out


def classify(a: dict, b: dict) -> tuple[str, str]:
    shift = abs(a["mean_mv"] - b["mean_mv"])
    corrs = [c for c in (a["corr_with_predecessor"], b["corr_with_predecessor"]) if c is not None]
    max_corr = max(corrs) if corrs else 0.0
    if shift >= FLOATING_MIN_SHIFT_MV or max_corr >= FLOATING_MIN_CORR:
        return "floating", (f"mean moves {shift:.0f} mV when the scan order changes"
                            if shift >= FLOATING_MIN_SHIFT_MV else
                            f"copies its predecessor's fluctuations (r = {max_corr:.2f})")
    if shift <= DRIVEN_MAX_SHIFT_MV and max_corr <= DRIVEN_MAX_CORR:
        return "driven", f"same level in both orders (shift {shift:.0f} mV), not copying its predecessor"
    return "uncertain", f"shift {shift:.0f} mV, predecessor correlation up to {max_corr:.2f}"


def scan(port: str, acq: dict, pins: list[int], seconds: float = 3.0, rate: int = 1000,
         factory: Callable | None = None) -> dict[str, Any]:
    """Scan `pins` in forward and reverse order, classify each as driven / floating / uncertain."""
    acq = {**acq, "fs": rate}
    fs = float(rate)
    mains = float(acq.get("mains_hz", 50))
    results, meta = {}, {}
    for name, order in (("forward", list(pins)), ("reverse", list(reversed(pins)))):
        x, m = _collect(port, order, acq, seconds, factory)
        results[name] = _order_stats(x, order, m["fs"], mains)
        meta[name] = {"order": order, "samples": len(x), **m}
    rows = []
    for g in pins:
        verdict, why = classify(results["forward"][g], results["reverse"][g])
        f, r = results["forward"][g], results["reverse"][g]
        rows.append({"gpio": g, "verdict": verdict, "why": why, "forward": f, "reverse": r,
                     "mean_mv": round((f["mean_mv"] + r["mean_mv"]) / 2, 1)})
    dev = meta["forward"]["device"]
    return {"pins": rows, "runs": meta, "rate_hz": rate, "seconds_per_order": seconds,
            "device": {k: dev.get(k) for k in ("fw", "chip", "rev", "mac", "fs", "os")},
            "method": __doc__.strip().splitlines()[0]}


def format_table(res: dict) -> str:
    lines = [f"{'GPIO':>5} {'verdict':<10} {'mean mV':>8} {'fwd':>8} {'rev':>8} {'std':>6} {'hum':>5}  why"]
    for r in res["pins"]:
        f, b = r["forward"], r["reverse"]
        hum = f["mains_fraction"]
        lines.append(f"{r['gpio']:>5} {r['verdict']:<10} {r['mean_mv']:>8.1f} {f['mean_mv']:>8.1f} {b['mean_mv']:>8.1f} "
                     f"{f['std_mv']:>6.1f} {'' if hum is None else f'{hum:.0%}':>5}  {r['why']}")
    return "\n".join(lines)
