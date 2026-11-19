# Fable production handoff: real-hardware EMG acquisition foundation

Branch `acquisition-foundation`, based on `master` commit `5258398` ("Baseline"). Updated
2026-11-16, evening, after flashing and testing on the real board.

Read this first, then `ARCHITECTURE.md` (system), `ACQUISITION_PROTOCOL.md` (wire format),
`HARDWARE_INVENTORY.md` (what the rig is and how that is known), `ELECTRICAL_SAFETY.md` (the gate)
and `ACQUISITION_SETUP.md` (commands).

## 1. Summary

| | Status |
|---|---|
| Virtual-data pipeline | Works, behaviour unchanged. All baseline tests pass (§8). |
| Three-channel acquisition architecture | Done: source interface → common pipeline → monitor / raw recorder / live / analysis. |
| ESP32 firmware | `emg_esp32_v2` **v2.1.0 flashed** on the board (hash verified). 1 to 6 ADC1 channels; pin map set from the host and stored in NVS. |
| ESP32 → host acquisition | **Verified on hardware**: 2000 Hz, 3 channels, 5 min with 0 missing, 0 invalid and 0 CRC errors; host-clock rate 2000.004 Hz. |
| Raw recording on hardware | **Verified**: 600,100-sample recording, integrity re-derived from the file (complete, no gaps), converted to a Session, existing filters run on it. |
| Channel map | **Measured**: CH1 = GPIO34 (D34, red wire), CH2 = GPIO39 (VN, green), CH3 = GPIO36 (VP, purple). |
| Front-end | **Only CH1's Candy is producing an output** (1.66 V idle). CH2 and CH3 read as floating: their Candys are not driving. Probably power or ground; not measured. |
| EMG capture | **Not attempted.** Electrical safety gate BLOCKED; no electrodes connected. |

So raw data can be collected now on all three channels, but **only CH1 carries a front-end
signal**. CH2 and CH3 record a floating ADC input until their Candys are fixed.

## 2. Architecture

```
VirtualSource / ReplaySource ─┐
Esp32Source ← EMGM/2 ← ESP32 ─┤──► Acquisition (pipeline.py) ──┬─► StreamMonitor  (5-stage diagnostics)
Esp32Source ← Esp32Emulator  ─┤                                ├─► RawRecorder    data/raw/<id>/  (append-only)
SerialSource ← text firmware ─┘                                ├─► read(): mV with gaps re-inserted as logged fill
                                                               │      └─► LiveMirror / record.py / Session → analysis, report
                                                               └─► server/acq.py → /api/acq/*, /ws/acq, /diagnostics
raw → recording/derive.py → data/derived/<id>/ (validated, integrity) → Session(+provenance) → existing analysis
pinscan.py: driven-vs-floating scan of ADC1 pins (forward and reverse order), CLI `acq scan`
```

Details and the design rules are in `ARCHITECTURE.md` Part 2.

## 3. What was implemented

| Deliverable | Location |
|---|---|
| Existing-system report, updated architecture | `docs/ARCHITECTURE.md` |
| Hardware inventory, measured channel map | `docs/HARDWARE_INVENTORY.md`, `configs/esp32_candy.yaml` (`hardware:`) |
| Firmware | `firmware/emg_esp32_v2/emg_esp32_v2.ino` (v2.1.0) |
| Protocol | `docs/ACQUISITION_PROTOCOL.md`, `src/emgmirror/acquisition/emgm2.py` |
| Host adapter | `src/emgmirror/acquisition/esp32.py` (handshake, pin-map apply, loss accounting, reconnect) |
| Pin identification | `src/emgmirror/acquisition/pinscan.py`, `emgmirror acq scan` |
| Source interface | `src/emgmirror/acquisition/base.py`, `registry.py` |
| Emulator with fault injection | `src/emgmirror/acquisition/emulator.py` |
| Common pipeline, diagnostics | `src/emgmirror/acquisition/pipeline.py`, `monitor.py` |
| Raw recording, derived layers | `src/emgmirror/recording/raw.py`, `derive.py` |
| Diagnostic UI and API | `src/emgmirror/server/acq.py`, `server/static/diagnostics.html` |
| CLI | `emgmirror acq probe|record|scan`, `emgmirror raw list|inspect|to-session`, `emgmirror record --source` |
| Tests | `tests/test_emgm2_protocol.py`, `test_esp32_simulated.py`, `test_raw_recording.py`, `test_acq_monitor_server.py`, `tests/physical/` |
| Tools | `scripts/read_pin_survey.py`, `scripts/bench_emgm2.py` |

## 4. What was preserved (unchanged behaviour)

The simulator, protocol, filters, features, bilateral analysis, motor-unit estimators, calibration
engine, experiments, anatomy, report, the analysis dashboard, `live.py`, the older firmware and the
CT pipeline. The changes to existing files are small: `git diff 5258398 -- src configs pyproject.toml`
(`sources.py` refactored onto the interface with the same `read()` results; `Session` gains
optional `provenance`; `record.py` writes trial markers; `app.py` mounts the acquisition API; `cli.py`
adds `acq` and `raw`; `default.yaml` adds `protocol` (default `text`), `bias_mv`, `raw_dir` and
`diagnostics`).

## 5. Data schemas and recording format

`ARCHITECTURE.md` → "Data schemas". Key points:

- `Chunk.raw` for the ESP32 is the **sum of `os` 12-bit codes** (0xFFFF = invalid), stored
  unchanged in `data/raw/<id>/samples.bin` as records `[index:<u8, raw:<u2[nch]]`.
- `pin_mV = cal_a·raw/os + cal_b`. This board's eFuse values are `cal_a = 0.806`, `cal_b = 152`, so
  **code 0 = 152 mV**, the floor of the ADC.
- `mv = (pin_mV − 1650)/2420` uses an **assumed bias** and a **nominal gain**. It is not a
  calibrated muscle voltage, and every layer says so.

## 6. Firmware, protocol, channel map, sampling (measured on the board)

| | |
|---|---|
| Firmware | v2.1.0, built with Arduino-ESP32 core 3.3.12, 298 KB, flashed 2026-11-16 to COM9 |
| Device | ESP32-D0WD-V3 rev 3.1, MAC 48:9d:31:04:70:70 |
| Protocol | EMGM/2 at 500000 baud |
| Channel map | CH1 = GPIO34/ADC1_CH6 (D34, red), CH2 = GPIO39/ADC1_CH3 (VN, green), CH3 = GPIO36/ADC1_CH0 (VP, purple) |
| Pin states (no electrodes) | GPIO34 **driven** at 1.66 V (Candy output, mostly 50 Hz hum); GPIO39 and GPIO36 **floating** (`pinscan_*.json`) |
| Sampling | 2000 Hz/channel, 4× oversampling (24 kHz conversions), 10 rows per packet |
| Measured rate | host clock: 1999.904 Hz (live monitor, 5 min) and **2000.004 Hz** (re-derived from the raw file, 300 s); 1999.19 Hz over a 20 s window. The device-clock figure is always about 2000.0 because the ADC and the timer share one crystal, so it is not an independent check. |
| `RATE 1000` | 999.94 Hz on the host clock |
| Losses | 0 missing, 0 invalid, 0 CRC/framing errors, 0 DMA overflows in 20 s, 30 s and 5 min runs |
| Link | `tx_free` stayed at 8164 of 8192 bytes: the serial TX buffer never filled |

## 7. Test commands

```
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest -q tests\test_emgm2_protocol.py tests\test_esp32_simulated.py tests\test_raw_recording.py tests\test_acq_monitor_server.py
set "EMGMIRROR_PORT=COM9" && .venv\Scripts\python -m pytest tests\physical -m hardware -v
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq scan  --source esp32:COM9
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq probe --source esp32:COM9 --seconds 30
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq record --source esp32:COM9 --seconds 300 --label bench
.venv\Scripts\emgmirror raw inspect <recording_id>
```

## 8. Actual test results

Simulated and physical results are kept apart (`docs/test_results/simulated/`,
`docs/test_results/physical/`).

**Software and simulated hardware** (no board):

| Run | Result | Record |
|---|---|---|
| Baseline before any change | 370 passed | terminal log |
| Full suite on the branch (before v2.1) | 414 collected: 409 passed, 1 failed (timing flake, fixed), 4 skipped (physical) | `simulated/2026-11-16_pytest_full.xml` |
| After the v2.1 changes (all non-CT software and simulated tests) | **63 passed**. The CT suite was not re-run: no CT code changed after its 349/349 pass. | `simulated/2026-11-16_pytest_v21.xml` |
| Host throughput | Esp32Source 12× to 38× the device's packet rate | `simulated/2026-11-16_bench_emgm2.txt` |

**Physical** (COM9, firmware v2.1.0, no electrodes, 2026-11-16):

| Test | Result | Record |
|---|---|---|
| `tests/physical` (handshake, 20 s integrity, RATE, pin levels) | **4 passed** | `physical/2026-11-16_pytest_physical.xml`, `*_pytest_*.json` |
| 30 s probe | 60,200 samples, 0 missing, 1999.874 Hz host | `physical/2026-11-16_probe_v2.1_30s.json` |
| 5 min raw recording | 600,100 samples, 0 missing, 0 invalid, file integrity complete, 2000.004 Hz | `physical/2026-11-16_record_5min.json`, `*_raw_inspect_5min.json` |
| Raw → Session → filters | works; provenance and `is_real_hardware` kept | `data/sessions/bench-5min` (git-ignored) |
| Guided protocol recorder (`record --arm right`, about 90 s, no person) | raw complete, 178,680 samples, 0 missing, 2000.084 Hz; 14 trial markers in the raw file; session with 7 trials, 0 fills; `analyze` writes a report. Trials rebuilt from the raw markers match the session durations, offset by +2.2 s because the raw file also keeps the 2 s settle. | `data/raw/20261116-193654_bench-guided`, `data/sessions/bench-guided` (git-ignored) |
| Channel labelling | 18 runs, 1 to 6 pins, many orders: the driven signal always lands on its own GPIO | `HARDWARE_INVENTORY.md` |
| Pin scans | plugged in / unplugged / re-plugged | `physical/2026-11-16_pinscan_*.json` |
| Old pin-survey firmware | handshake refused as designed (before flashing) | `physical/2026-11-16_probe_emgm2_on_pin_survey_firmware.json` |

## 9. Known defects and open questions

1. **Hardware: 2 of 3 Candys are not driving their outputs** (GPIO39 and GPIO36 float with the
   connector on). Probably VCC or GND not reaching them. Needs multimeter checks
   (`HARDWARE_INVENTORY.md`, A to D).
2. **Hardware: the live channel moved.** In the first scan the live output was on GPIO36. After
   an unplug and re-plug it is on GPIO34. Either the connector sat one pin over before, or contact
   is intermittent. Labelling has been ruled out (§8).
3. Device-side overflow accounting (index jump plus `AFTER_DROP`) works in emulation. **It has not
   been triggered on hardware**: no overflow happened in any run.
4. `Acquisition.read()` fills at most 2 s per gap. Longer gaps shorten the live time base (the raw
   file and `to_session` are correct).
5. Monitor channel verdicts for a floating pin often say "clipping" (it touches code 0). The root
   cause, floating, is only visible with `acq scan`. `derive.channel_stats` labels such pins
   "varying".
6. The clipping count in `StreamMonitor` is cumulative, not windowed.
7. The legacy text `SerialSource` cannot detect lost samples.
8. `record --arm` relabels channels by mutating `SourceInfo.channels`.

## 10. Technical debt

- The pages load Plotly from a CDN, so they do not work offline.
- The server allows one acquisition at a time and has no authentication; keep it on 127.0.0.1.
- The full test suite takes about 15 to 25 minutes, mostly the CT tests.
- `configs/me.local.yaml` (personal, git-ignored) still describes the old text firmware; use
  `configs/esp32_candy.yaml`.
- `docs/CT_TO_3D.md` (in the baseline) describes the owner's scan. Check before any public push.

## 11. Unverified assumptions and electrical safety

Unverified:

- Candy supply voltages
- why CH2 and CH3 are silent
- Candy gain (2420 nominal) and idle bias (1650 mV assumed; the live one measures 1660 mV)
- input protection
- the output range under a real signal

**Electrical safety: BLOCKED** (`ELECTRICAL_SAFETY.md`). No electrodes have been connected. The
software never instructs anyone to attach them and reports EMG as "not verified" on real hardware.

## 12. Recommended next tasks

1. Owner measures Candy VCC, OUT and GND continuity (inventory A to D). Fix the green and purple
   Candys' power, then `acq scan` must show GPIO39 and GPIO36 as driven at about 1.65 V.
2. Reseat the connector and re-run `acq scan` a few times, to check that GPIO34 (or whichever pin
   is live) stays driven, which rules out an intermittent contact.
3. Bench calibration without a body: a known small sine into a Candy input through a divider
   gives the real gain and bias per channel, so `mv` becomes calibrated.
4. Force a device overflow in a debug build (stall `loop()` for 200 ms) to verify `AFTER_DROP`
   accounting on hardware.
5. Close the safety gate, then the electrode protocol on the cleared arm.
