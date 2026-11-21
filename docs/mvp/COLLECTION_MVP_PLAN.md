# Collection MVP: every source recording on one timeline

Research use, not for diagnosis or surgical planning.

Status: plan of 2026-11-20 (draft for review). This is **phase 1**. The analysis plan
([MVP_MULTIMODAL_PLAN.md](MVP_MULTIMODAL_PLAN.md)) is phase 2, and its wireless-EMG part is postponed.
Background designs: [design_esp32_ultrasonic.md](design_esp32_ultrasonic.md) and
[design_phone_streams.md](design_phone_streams.md). Where they disagree with this plan, this plan wins.

## 1. Goal

Show that **every source collects its data, on one timeline**, in a 2-minute session:

| Source | Hardware | Link to the laptop | Nominal rate |
|---|---|---|---|
| EMG, 1 channel | Muscle BioAmp Candy on the ESP32, GPIO35 | ESP32 USB serial (EMGM/2) | 2000 Hz |
| Ultrasonic 1 and 2 | 2 × HC-SR04-type distance sensors on the same ESP32 | same ESP32 USB serial | 16.7 Hz each (alternating 30 ms slots) |
| Normal camera | Android phone (Android 12 or later) | USB, scrcpy camera mirroring | 30 fps, 1280×720 |
| Depth camera | iPhone with LiDAR (12 Pro or later Pro) | USB, Record3D live stream | depth 256×192 + RGB, 30 fps |

No analysis yet: only collection, integrity and timing.

## 2. Decisions

| Topic | Decision | Why |
|---|---|---|
| Links | All wired to the laptop for now. Wireless EMG is postponed. | Simplest path to data; one host clock. |
| Android app | **scrcpy** camera mirroring (Apache-2.0, open source, no ads, no account, no cloud), pinned to one release. MVP uses the scrcpy CLI (`--video-source=camera --camera-size=1280x720 --camera-fps=30 --no-audio --record=android.mkv`), started by our orchestrator; its window is the live preview. | Only option that is open source, has no ads or cloud, and carries a phone-side timestamp per frame. Nothing is installed on the phone. |
| iPhone app | **Record3D** (paid app, about $4 to 5 one-time; USB streaming needs the purchase) with the `record3d` Python library (LGPL-2.1, Windows wheels). Per frame: depth, RGB, confidence, intrinsics and the camera's capture timestamp. | Record3D's Wi-Fi depth is lossy (HSV-packed), so USB is the only usable live depth path. |
| Rejected | IP Webcam, DroidCam (ads, closed source, no frame timestamps); Record3D Wi-Fi (lossy depth). | Privacy and data quality. |
| Fallback | Android: none needed unless the phone is older than Android 12. iPhone: Stray Scanner (MIT, free), which records to files that are copied over USB afterwards and aligned with the same LED. | |
| Ultrasonic timing | MCPWM capture hardware times each echo (Espressif's official HC-SR04 method), so nothing blocks EMG sampling. Every reading carries the trigger time on the device clock **and the EMG sample index**, so it is on the EMG timeline by construction. | Exact alignment with EMG at no cost. |
| Camera timing | Every frame is stored with its phone timestamp and the laptop's monotonic arrival time. A coded **sync LED driven by the ESP32**, seen by both cameras, measures each camera's latency on the device clock. Expected accuracy: about ±10 to 17 ms at 30 fps. | Puts the phones on the ESP32 timeline with a measured error. |
| Host clock | Add a monotonic column (`t_host_mono_ns`, from `perf_counter_ns`) to the EMG `frames.csv`, and write monotonic/wall pairs to `events.jsonl` every 10 s. | The current file stores only wall time, which Windows can step during a session. |
| Storage | `data/multimodal/<session>/` (already git-ignored): the EMG raw recording (with `ultrasonic.csv`), `android.mkv` (H.264 as sent, no re-encode), `iphone/` (RGB JPEG chunks, depth as uint16 arrays, `frames.csv`), and `session.json`. Disk: about 3 to 4 GB per 10 minutes. | |
| Privacy | Both phones in airplane mode with Wi-Fi off during sessions (no backup to Google Photos or iCloud, no app telemetry). Nothing goes to any cloud. | Personal data stays on the laptop. |

## 3. Wiring (ESP32 DevKit, 30-pin)

| Signal | Pin | Note |
|---|---|---|
| EMG CH1 | GPIO35 (D35) | as today. `configs/esp32_candy_1ch.yaml` still says GPIO34 and must change to GPIO35. |
| US1 TRIG / ECHO | GPIO23 / GPIO22 | ECHO through a 1 kΩ / 2 kΩ divider (5 V → 3.3 V); measure ≤ 3.4 V. |
| US2 TRIG / ECHO | GPIO19 / GPIO18 | same divider. |
| Sensor power | VIN (5 V) | short lead turning away from D35 at once; sensor grounds return to the **right-row GND** (beside 3V3), not the Candy's left-row GND. |
| Sync LED | GPIO21 + 330 Ω to 1 kΩ | right row, away from the EMG wire (not GPIO25, which sits beside D35). Bright LED on a short lead, visible to both cameras. |

Keep any added capacitors at 47 µF or less (inrush, given the boot-loop history), insulate pins from
the foil lining, and check 5 clean boots out of 5.

## 4. Safety (must be recorded by the owner before electrodes go on)

The current gate covers "only the ESP32's USB cable connected". This MVP adds 2 ultrasonic sensors to
the electrode circuit's ground, and 2 phones on USB, which share the laptop's ground. A phone on a
mains charger would put a mains-referenced path into the body circuit. **Amendment to
`docs/ELECTRICAL_SAFETY.md`**, recorded by the owner:

- **Configuration covered:** 1 channel on GPIO35, 2 × HC-SR04 on ESP32 VIN, Android and iPhone on USB.
- **Power:** laptop on battery; no phone on a charger; no powered USB hub.
- **Phones:** airplane mode.
- **Sensors:** mounted so they cannot be touched.

Until the amendment is recorded, every combined run is done **with no electrodes on a person**.

## 5. Milestones

"Cloud" milestones run in Claude Code cloud sessions with emulators and fakes. "Laptop" milestones
need the hardware.

| # | Milestone | Where | Done when |
|---|---|---|---|
| H0 | Board works again | laptop | Boot loop fixed; `acq scan` shows GPIO35 driven by the Candy; 5/5 clean boots. Gates every laptop step. |
| A1 | Config + host clock | cloud | `esp32_candy_1ch.yaml` on GPIO35/ADC1_CH7; `t_host_mono_ns` column in `frames.csv` (old recordings still load); mono/wall pairs every 10 s; tests. |
| A2 | Firmware 2.2.0-us (compiled, not flashed) | cloud | Buffers enlarged (body/wire about 600 B, HELLO 512, STATUS 384; an oversized packet logs an error instead of vanishing); fixed pins; MCPWM capture; 30 ms alternating slots; `US ON/OFF`; RANGE packet type 0x06 (us_seq, run, t_trig_us, emg_index, echo_us, sensor, status); STUCK reported only; sync LED on GPIO21 with a `SYNC` command whose edges are logged with their EMG sample index. Compiles with arduino-cli. |
| A3 | Host decode + emulator | cloud | The decoder counts unknown packet types separately (not as corruption); RANGE and SYNC in `Esp32Source` and the emulator; `ultrasonic.csv` is written only when readings arrive (existing recordings unchanged); `emgmirror acq check` prints EMG rate, per-sensor rate and % OK, and the index delta. |
| A4 | Phone sources + orchestrator | cloud | `emgmirror collect record` creates the session folder, launches the pinned scrcpy CLI for Android (logs start/stop monotonic time), runs a `Record3DSource` for the iPhone (capture timestamp + monotonic arrival per frame), and records EMG + ultrasonic. Fakes for scrcpy and Record3D let it run in the cloud. |
| A5 | Collection check | cloud | `emgmirror collect check` (and a page in the viewer) shows every source's rate, drops, latest value or frame, and arrival delay; pass/fail against the targets in section 6. |
| C0 | Phone probe (no electrodes) | laptop | `scrcpy --list-camera-sizes` works; the iPhone is visible to `record3d` through the most private Apple driver option (try Apple Mobile Device Support only, then the Apple Devices app, then iTunes never signed in); scrcpy timestamp unit confirmed; 2-minute CPU and battery check with all sources. |
| B1 | Bench (no electrodes, laptop on battery) | laptop | Flash backed up; 2.2.0-us flashed; ECHO ≤ 3.4 V; hardware tests pass with US OFF (0 frame errors); distances at 200/500/1000 mm within a few mm; EMG noise floor with US ON vs OFF compared (no comb at 16.7/33 Hz harmonics). |
| B2 | One-timeline check | laptop | The 2-minute session of section 6 passes. |

## 6. Pass criteria for B2 (the MVP)

A 2-minute session with all 5 sources (EMG, US1, US2, Android, iPhone):

- every source at ≥ 95 % of its nominal rate, with no gap over 0.5 s;
- `SYNC` fired 3 times at the start and 3 times at the end: the LED onsets are found in `android.mkv`
  and in the Record3D RGB, giving each camera's latency and its spread in ms;
- a hand moved into the US1 beam while clenching: the ultrasonic dip and the EMG burst line up on the
  device timeline, and both cameras show the movement within their measured latency;
- EMG has no new gaps compared with a run without the phones (USB and CPU load do not starve it).

With electrodes on, B2 runs only after the section 4 amendment is recorded.

## 7. Open questions for the owner

- **A1 to A5:** none; the cloud work can start now.
- **C0:** which Android phone (Android 12 or later?) and which iPhone (a Pro model with LiDAR, iOS 16 or
  later)? Is the Record3D purchase OK? Is USB debugging on the Android phone OK?
- **B1:** which ultrasonic modules (classic 5 V HC-SR04, HC-SR04P or RCWL-1601)? What will they point
  at, and over what distance (e.g. hand at 5 to 50 cm, or body at 1 to 2 m)? This sets the range limit
  and whether 16.7 Hz is enough. A laser time-of-flight sensor over I2C would give higher rates if
  needed.
- **B1:** does the laptop have 3 free USB ports (ESP32, Android, iPhone) without a powered hub?
- **H0:** is the boot loop fixed and is GPIO35 driven by the Candy again?
