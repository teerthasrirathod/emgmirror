"""Does a recording contain muscle activity? Rest vs task, from the raw recording itself.

For each labelled trial (from the trial markers in the raw file) this reports the RMS of the channel
after the project's own analysis filter (band-pass from the config + mains removal), its ratio to
the rest trial, and where the spectral power sits. Surface EMG shows up as broadband power, mostly
between about 20 and 250 Hz, that rises clearly above rest during contraction. Mains interference
shows up as narrow peaks at 50 Hz and its harmonics and does not follow the task.

    python scripts/check_emg_response.py <raw_recording_id> --config configs/esp32_candy_1ch.yaml
"""

from __future__ import annotations

import argparse
import json

import numpy as np
from scipy.signal import welch

from emgmirror.config import REPO_ROOT, load_config
from emgmirror.processing.filters import clean_for_analysis
from emgmirror.recording import RawRecording
from emgmirror.recording.derive import to_session


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("recording")
    p.add_argument("--config", default=str(REPO_ROOT / "configs" / "esp32_candy_1ch.yaml"))
    p.add_argument("--out", default=None)
    a = p.parse_args()
    cfg = load_config(a.config)
    rec = RawRecording.open(REPO_ROOT / "data" / "raw" / a.recording)
    integ = rec.integrity()
    s = to_session(rec, "check", allow_gaps=True)
    b = s.blocks[0]
    fs = s.fs
    y = clean_for_analysis(b.data, fs, cfg)  # electrode-referred estimate, mV
    pin = rec.to_pin_mv()
    rows = []
    rest = next((t for t in b.trials if t.task == "rest"), None)
    for ch in range(b.data.shape[1]):
        def seg(t, arr=y):
            i0, i1 = int(t.start_s * fs) + int(0.25 * fs), int(t.end_s * fs) - int(0.25 * fs)
            return arr[i0:i1, ch]

        r0 = float(np.sqrt(np.mean(seg(rest) ** 2))) if rest else None
        for t in b.trials:
            x = seg(t)
            rms = float(np.sqrt(np.mean(x ** 2)))
            f, pxx = welch(x, fs=fs, nperseg=int(fs // 4))
            band = (f >= 20) & (f <= 450)
            mains = np.zeros_like(f, dtype=bool)
            for h in np.arange(50, fs / 2, 50):
                mains |= np.abs(f - h) <= 3
            total = pxx[band].sum()
            # Median over 0.5 s windows: a sustained contraction raises every window, while a movement
            # artifact (a short spike with a baseline jump) raises one or two and barely moves the median.
            w = int(fs / 2)
            win = [float(np.sqrt(np.mean(x[i:i + w] ** 2))) for i in range(0, len(x) - w + 1, w)] or [rms]
            rest_win = [float(np.sqrt(np.mean(seg(rest)[i:i + w] ** 2))) for i in range(0, len(seg(rest)) - w + 1, w)]
            r_med = float(np.median(rest_win)) if rest_win else None
            sustained = None if not r_med else round(float(np.median(win)) / r_med, 2)
            # Room noise floor = the rest trial after the same cleaning (band-pass + mains regression).
            # Net RMS removes it in power (sqrt(rms^2 - floor^2)); SNR in dB is relative to that floor.
            floor = r0 or 0.0
            net = float(np.sqrt(max(rms ** 2 - floor ** 2, 0.0)))
            snr_db = None if not floor else round(20 * np.log10(max(rms, 1e-12) / floor), 1)
            praw = seg(t, pin) if pin is not None else None
            clip = None if praw is None else round(float(np.mean((praw <= 152.5) | (praw >= 3200))), 3)
            rows.append({"ch": ch + 1, "task": t.task, "target": t.level, "rms_mv_est": round(rms, 5),
                         "net_rms_mv_est": round(net, 5), "snr_db_vs_rest": snr_db, "clipped_fraction": clip,
                         "x_rest": None if not r0 else round(rms / r0, 2), "x_rest_sustained": sustained,
                         "windows_over_3x_rest": None if not r_med else int(sum(v > 3 * r_med for v in win)),
                         "windows": len(win),
                         "median_freq_hz": float(f[band][np.searchsorted(np.cumsum(pxx[band]), total / 2)]),
                         "mains_share": round(float(pxx[band & mains].sum() / total), 3) if total > 0 else None,
                         "pin_p2p_mv": round(float(np.ptp(seg(t, pin))), 1)})
    res = {"recording": a.recording, "integrity": {k: integ[k] for k in ("status", "samples", "missing_samples",
                                                                        "observed_rate_host_clock_hz")},
           "filter": {"band_hz": cfg["processing"]["band_hz"], "mains_removal": cfg["processing"]["mains_removal"]},
           "mv_note": "electrode-referred ESTIMATE: nominal gain 2420, assumed bias; not calibrated",
           "trials": rows}
    print(json.dumps(res["integrity"]))
    print(f"{'ch':<3}{'task':<16}{'SNR dB':>7}{'net RMS(est)':>13}{'x rest':>8}{'sustained':>10}{'>3x win':>9}"
          f"{'median Hz':>11}{'mains':>7}{'pin p2p':>9}{'clip':>7}")
    for r in rows:
        print(f"{r['ch']:<3}{r['task']:<16}{str(r['snr_db_vs_rest']):>7}{r['net_rms_mv_est']:>13.5f}{str(r['x_rest']):>8}"
              f"{str(r['x_rest_sustained']):>10}{str(r['windows_over_3x_rest']) + '/' + str(r['windows']):>9}"
              f"{r['median_freq_hz']:>11.0f}{r['mains_share']:>7.2f}{r['pin_p2p_mv']:>9.1f}{str(r['clipped_fraction']):>7}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)


if __name__ == "__main__":
    main()
