"""Summarise the ADC pin voltages reported by firmware/esp32_pin_survey (read-only).

The pin-survey sketch prints a CSV header and one 67-column row per ~0.8 s at 115200 baud:
elapsed_ms, then gpioN_adc_raw / gpioN_mV for every ADC-capable pin, then digital and touch columns.
This script opens the port WITHOUT resetting the board, collects rows for a while, and reports the
mean / min / max millivolts per ADC1 pin. Nothing is written to the board.

    python scripts/read_pin_survey.py COM9 --seconds 30 --out docs/test_results/physical/pin_survey.json

Notes on reading the result:
- Only ADC1 pins (32-39) are meaningful for EMG; ADC2 pins share hardware with the radio.
- analogRead switches the pin to analog mode, so the digital columns of the same row are unreliable.
- A pin wired to a powered Candy OUT should sit well inside 0.1-3.0 V; ~0 V means no output reaching it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time

import serial

ADC1 = [32, 33, 34, 35, 36, 39]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("port")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--out", default=None)
    a = p.parse_args()

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = a.port, a.baud, 0.2
    s.dtr = False  # do not pulse EN: leave the running firmware alone
    s.rts = False
    s.open()
    buf, t0 = b"", time.time()
    while time.time() - t0 < a.seconds:
        buf += s.read(4096)
    s.close()

    lines = buf.decode("ascii", errors="replace").splitlines()
    header = next((ln.split(",") for ln in lines if ln.startswith("elapsed_ms,")), None)
    if header is None:
        # The sketch prints its header once at boot; rebuild it from the known pin list instead.
        adc = [0, 2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33, 34, 35, 36, 39]
        header = ["elapsed_ms"] + [f"gpio{g}_{k}" for g in adc for k in ("adc_raw", "mV")]
    rows = []
    for ln in lines:
        parts = ln.split(",")
        if len(parts) >= len(header) and parts[0].isdigit():
            try:
                rows.append({h: int(v) for h, v in zip(header, parts)})
            except ValueError:
                continue
    if not rows:
        raise SystemExit(f"no complete pin-survey rows in {a.seconds:.0f} s: is the board running esp32_pin_survey?")
    res = {"port": a.port, "rows": len(rows), "seconds": round(time.time() - t0, 1),
           "row_interval_ms": round(statistics.mean(b["elapsed_ms"] - a_["elapsed_ms"]
                                                    for a_, b in zip(rows, rows[1:])), 1) if len(rows) > 1 else None,
           "board_uptime_s": rows[-1]["elapsed_ms"] / 1000, "pins": {}}
    for g in ADC1:
        mv = [r[f"gpio{g}_mV"] for r in rows if f"gpio{g}_mV" in r]
        raw = [r[f"gpio{g}_adc_raw"] for r in rows if f"gpio{g}_adc_raw" in r]
        res["pins"][f"GPIO{g}"] = {"mean_mV": round(statistics.mean(mv), 1), "min_mV": min(mv), "max_mV": max(mv),
                                   "stdev_mV": round(statistics.pstdev(mv), 1), "mean_raw": round(statistics.mean(raw))}
    print(json.dumps(res, indent=2))
    if a.out:
        res["date"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        res["firmware"] = "esp32_pin_survey (structured CSV), already on the board; not flashed by this script"
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)


if __name__ == "__main__":
    main()
