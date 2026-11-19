"""Host-side throughput of the EMGM/2 path (decoder and Esp32Source), no hardware needed.

The device sends 200 DATA packets/s at 2 kHz (10 rows per packet). The host must process them with
a wide margin, because the dashboard, recorder and analysis share the same Python process.

    python scripts/bench_emgm2.py
"""

from __future__ import annotations

import time

import numpy as np

from emgmirror.acquisition import emgm2
from emgmirror.acquisition.esp32 import Esp32Source
from emgmirror.storage import Channel


def main(n: int = 4000) -> None:
    raw = np.random.default_rng(0).integers(0, 16380, (10, 3)).astype(np.uint16)
    t = time.perf_counter()
    stream = b"".join(emgm2.encode_data(i, i * 10, i * 5000, 0, raw, 4) for i in range(n))
    enc = time.perf_counter() - t
    t = time.perf_counter()
    got = len(emgm2.StreamDecoder().feed(b"\x00" + stream))
    dec = time.perf_counter() - t
    src = Esp32Source("bench", [Channel(f"c{k}", "right", "flexors") for k in range(3)], {"fs": 2000})
    src.process_bytes(b"\x00" + emgm2.encode_text(
        emgm2.HELLO, "proto=EMGM/2 nch=3 fs=2000 os=4 pins=35,32,33 run=1 cal_a=0.8 cal_b=0"))
    t = time.perf_counter()
    src.process_bytes(stream)
    full = time.perf_counter() - t
    assert got == n and src.samples == n * 10
    for name, d in (("encode (emulator side)", enc), ("decode only", dec), ("Esp32Source end-to-end", full)):
        print(f"{name:<24} {n / d:8.0f} packets/s  = {n / d / 200:5.1f}x the device's 200 packets/s")


if __name__ == "__main__":
    main()
