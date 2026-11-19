"""Our raw recordings through the vendor's own EMG processing, for an apples-to-apples check.

Upside Down Labs' Muscle-BioAmp-Arduino-Firmware (03_EMG_Envelope / 06_LED_BarGraph) samples at
500 Hz, band-passes 74.5-149.5 Hz with a 4-section IIR, and takes envelope = 2 * mean(|x|) over 64
samples. On a 10-bit 5 V Uno (1 count = 4.887 mV) their bar graph lights no LED below an envelope
of 16 counts (~78 mV) and all six at 36 counts (~176 mV). The Candy's gain does not depend on its
supply, so millivolts at the ADC pin compare directly.

    python scripts/vendor_envelope.py <raw_recording_id>
"""

from __future__ import annotations

import sys

import numpy as np
from scipy.signal import resample_poly, sosfilt

from emgmirror.config import REPO_ROOT
from emgmirror.recording import RawRecording
from emgmirror.recording.derive import to_session

# The vendor's filter, copied section by section: x = in - a1*z1 - a2*z2; out = b0*x + b1*z1 + b2*z2
VENDOR_SOS = np.array([
    [0.01856301, 0.03712602, 0.01856301, 1.0, 0.05159732, 0.36347401],
    [1.0, -2.0, 1.0, 1.0, -0.53945795, 0.39764934],
    [1.0, 2.0, 1.0, 1.0, 0.47319594, 0.70744137],
    [1.0, -2.0, 1.0, 1.0, -1.00211112, 0.74520226],
])
COUNT_MV = 5000.0 / 1023.0


def vendor_envelope(pin_mv: np.ndarray, fs: float) -> tuple[np.ndarray, float]:
    x = np.nan_to_num(pin_mv, nan=float(np.nanmedian(pin_mv)))
    x500 = resample_poly(x, 500, int(fs)) if int(fs) != 500 else x
    y = sosfilt(VENDOR_SOS, x500 - x500[:500].mean())
    env = 2 * np.convolve(np.abs(y), np.ones(64) / 64, mode="same")
    return env, 500.0


def main(rid: str) -> None:
    rec = RawRecording.open(REPO_ROOT / "data" / "raw" / rid)
    s = to_session(rec, "v", allow_gaps=True)
    pins = rec.to_pin_mv()
    fs = rec.fs
    gpios = [c.get("gpio") for c in rec.manifest["source"].get("hardware", {}).get("channels", [])]
    for ch in range(pins.shape[1]):
        env, f5 = vendor_envelope(pins[:, ch], fs)
        g = gpios[ch] if ch < len(gpios) else "?"
        print(f"{rid} CH{ch + 1} (GPIO{g}): vendor envelope at the ADC pin, mV (their LEDs: none < 78 mV, all >= 176 mV)")
        print(f"{'task':<16}{'median':>8}{'p90':>8}{'max':>8}   {'counts(5V/10b) median':>22}")
        for t in s.blocks[0].trials:
            a, b = int((t.start_s + 0.3) * f5), int((t.end_s - 0.3) * f5)
            seg = env[a:b]
            if len(seg) < 10:
                continue
            med, p90, mx = np.median(seg), np.percentile(seg, 90), seg.max()
            print(f"{t.task:<16}{med:8.1f}{p90:8.1f}{mx:8.1f}   {med / COUNT_MV:22.1f}")


if __name__ == "__main__":
    for rid in sys.argv[1:]:
        main(rid)
