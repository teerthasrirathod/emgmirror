# MVP: EMG + camera on one timeline (squash practice)

Research use, not for diagnosis or surgical planning.

Status: **phase 2** (analysis). On 2026-11-20 the owner put a data-collection MVP first:
[COLLECTION_MVP_PLAN.md](COLLECTION_MVP_PLAN.md) (1 EMG channel, 2 ultrasonic sensors, an Android
phone and a LiDAR iPhone, all wired to the laptop). The wireless-EMG milestones (W1 to W4, H1 to H2)
below are postponed until after it. Background designs:
[design_wireless_emg.md](design_wireless_emg.md) and
[design_sync_camera_analysis.md](design_sync_camera_analysis.md). Where they disagree with this plan,
this plan wins.

## 1. Goal

Record forearm sEMG and video of the same athlete on **one timeline**, then analyse every stroke:
muscle activity before, at and after ball contact, flexor/extensor balance, muscle onset relative to
contact, and the trend over a session. Example user: a squash player training for competition.
The design keeps room for more biosignals later (an IMU first).

## 2. Decisions

| Topic | Decision |
|---|---|
| Where code is built | Claude Code cloud sessions that clone this public repo. Everything gets tested there on **synthetic** data with known truth. |
| Where data is captured | The owner's Windows laptop, after `git pull`. Hardware, camera files and recordings never go to the cloud or into git. |
| Subjects | Only the owner. Solo drills; frame the camera so no other person is in the video. |
| EMG link | **Wireless first**: ESP32 as its own Wi-Fi access point (soft-AP), TCP, the unchanged EMGM/2 byte stream, a RAM transmit queue (start at 64 kB, about 4 to 5 s). BLE and SD logging are not in the MVP; SD logging returns only if court tests show outages longer than the queue. |
| Session clock | The ESP32 device clock (sample index + `t_device_us`). Every other stream maps onto it with a fitted line `t_dev = θ + (1+δ)·t_stream`; the leave-one-out residual is reported in ms. |
| Camera | Not chosen yet, so the design is camera-agnostic. Reference case: a phone filming at 120 to 240 fps into a file. A mandatory capability probe runs before any camera is trusted (section 6, step C0). |
| Sync between EMG and video | ESP32-driven **external LED** (coded blinks, visible to the camera) plus **3 taps at the start and the end** as an independent check. No audio chirp, no webcam calibration in the MVP. |
| Pose | RTMPose-m through `rtmlib` (onnxruntime, Apache-2.0), run offline on the laptop. Not YOLO-pose (AGPL), and not a depth camera in the MVP. |
| Privacy | Video, pose and analysis output go to `data/multimodal/` (git-ignored). Camera calibration goes in `*.local.yaml`. `mm add-video` warns about GPS metadata and keeps it out of session files. |

## 3. What the MVP does (and does not)

In the MVP:

- **Wireless EMG:** AP + TCP + queue, queue drops counted as their own cause, a host liveness watchdog
  (no packet for 2 s means reconnect), and PING for the host clock.
- **Phone video:** one phone video file per session; LED + taps sync; linear fit with leave-one-out error;
  a sync grade.
- **Pose:** RTMPose-m skeletons from the video.
- **Strokes:**
  - a stroke is a peak of wrist speed (scaled by torso length);
  - contact comes from manual labels on a few strokes, with the automatic offset measured against them;
  - per stroke: phase RMS normalised to the session's reference contractions, onset vs contact with an
    uncertainty, and a flexor/extensor ratio;
  - a fatigue trend, marked valid only on standard isometric test holds.
- **Viewer:** a video panel with the skeleton overlay synced to the EMG timeline, plus a stroke table.
  Video is streamed with HTTP Range, never read into memory whole.

Later, not in the MVP:

- **Wireless extras:** RESUME/ACKSEQ lossless reconnect, station mode, and TX power or channel tuning commands.
- **Other cameras:** webcam latency calibration and depth cameras.
- **More sync sources:** audio chirp sync and audio-based contact.
- **Other pose options:** a MediaPipe adapter and whole-body keypoints.
- **SD logging and an IMU:** both come after the MVP.

What 2 surface channels cannot tell: individual muscles, deep muscles, force, or motor-unit counts
during dynamic strokes. The analysis states this next to the numbers.

## 4. Fixes to the background designs (from the review)

These are binding for whoever builds the milestones.

1. **One protocol addendum first** (`docs/mvp/PROTOCOL_2_2.md`, milestone P0). It fixes:
   - **Commands:** `PING <token>` for the clock round trip; `SYNC` / `SYNCAUTO <s>` / `SYNCPIN <gpio>` for LED
     bursts; no `BLINK`; one LED pin stored in NVS, default an external LED on GPIO25.
   - **LED events:** sent as `LOG kind=sync ...`. No new packet type (today's decoder counts unknown types
     as corruption).
   - **Event names:** one vocabulary, `device_sync`, `clock_ping`, `clock`, `link_status`, and one host clock
     module, `sync/hostclock.py`.
2. **Boot id:** HELLO and STATUS carry `boot=<random hex>` and the reset reason. Runs and the duplicate guard
   are keyed on (boot, run), because `run` and `frame_seq` restart after every reboot or brownout.
3. **Buffers:** the packet body and wire buffers grow to about 600 B, HELLO text to 512, STATUS to 384.
   `send_packet` logs `packet_too_long` instead of failing silently. A host test sends a maximal HELLO.
4. **Commands:** only the verb is upper-cased; the command buffer grows to 160; over-long lines get `ok=0`.
   Wi-Fi and link settings are accepted from Serial only, and the passphrase is never echoed.
5. **Concurrency:** only `loop()` encodes packets, and the sender task only drains bytes. Commands from TCP
   go through a queue that `loop()` executes.
6. **PING replies in NTP style:** `t_rx_us` and `t_tx_us`, sent on a priority path ahead of the data queue.
   The camera sync does not depend on PING (LED + taps define camera to device).
7. **Queue with no client:** drop old data instead of serving seconds of stale samples on connect.
   Non-DATA packets keep reserved room. Writes never block (`MSG_DONTWAIT`).
8. **Power:** 470 µF on 5 V and 100 µF on 3V3, a short thick cable, and the reset reason in HELLO.
   The pass criterion is 0 brownouts in 30 min on the power bank.
9. **Radio noise test:** compare radio on vs off with the same power and ground (a firmware test mode
   sends dummy Wi-Fi traffic while data stays on serial). Test the supply separately. Notch the TX cadence
   and beacon harmonics in the analysis, or randomise the TX interval, depending on the result.
10. **Placement:** keep mass off the racket arm. The power bank and ESP32 go on a waist belt, with a
    twisted 4-wire lead and strain relief. Only the Candys and electrodes sit on the forearm. The LED goes
    on a short lead outside any pouch, facing the camera.
11. **Tests:** tolerances are derived from the quantisation floor (k = 4 σ), with fixed seeds and about
    20 sync events. The leave-one-out calibration is checked over 50 seeds. Network-dependent tests
    (model weights, arduino-cli) skip when offline. TCP tests use virtual time.
12. **Integrity over Wi-Fi:** the host rate comes from the lower envelope of (host − device) time, or is
    labelled as arrival-time based.

## 5. Safety

The current gate (ELECTRICAL_SAFETY.md) covers a USB-tethered board with the laptop on battery.
Wireless needs its **own** configuration, "6c: wireless, battery-only", passed separately by the
owner before any body-worn wireless recording:

- **Power:** the unit runs from a power bank (or AA pack) through a marked bank-only cable, with the
  bank's input port taped over and no wall adapter in the kit. No pass-through charging while worn.
- **Software interlock:** recording is blocked while the board's USB-serial bridge (CH340) shows up on
  the laptop, i.e. while it is cabled.
- **Before each session:** a per-session attestation; a wall charger cannot be seen by software, so
  the owner confirms the bank is not charging.
- **Open items:** checklist items 1, 2, 5 and 7 stay open exactly as today; 6c does not close them.
- **Movement:** full shadow swings only after 6c and the wireless bench tests pass. While tethered:
  taps and slow movements only.

## 6. Milestones, in order

Each milestone is one cloud session (or one laptop session) and ends with tests passing and a short
result note. "Cloud" milestones need no hardware.

| # | Milestone | Where | Done when |
|---|---|---|---|
| H0 | **Tethered EMG works again** | laptop | Boot loop fixed (foil lining checked for shorts); `acq scan` shows GPIO35/36 driven; a tethered rest + MVC recording shows EMG above the room floor. Gates every hardware step below. |
| P0 | Protocol 2.2 addendum | cloud | `docs/mvp/PROTOCOL_2_2.md` covers commands, LOG kinds, HELLO/STATUS keys, buffer sizes and the boot id; both background designs point to it. |
| W1 | TCP transport + Wi-Fi emulator | cloud | `wifi:` and `emulator-wifi:` sources; a 60 s emulated Wi-Fi recording through a real localhost socket has 0 lost samples; watchdog and keepalive tested. |
| W2 | Wi-Fi fault model + loss accounting | cloud | Emulator outages, resets mid-packet, jitter, queue overflow (flag bit 3, cause `device_txq`) and reboots with a new boot id, all counted correctly. |
| W3 | PING clock mapping | cloud | NTP-style PING in emulator and host; offset and drift recovered within 4 σ of the floor under asymmetric batching delay. |
| W4 | Firmware 2.2.0 code (not flashed) | cloud | Shared C++ core (COBS, CRC, queue) passes g++ unit tests with the host's byte vectors; the sketch has AP + TCP, sender task, queue, PING, LED sync, boot id, and the buffer and command fixes. |
| S1 | Clock fit + sync library | cloud | Linear fit with leave-one-out error, outlier rejection, sync grade; statistical tests over 50 seeds. |
| S2 | Synthetic squash session | cloud | `emgmirror sim squash` writes a raw EMG recording, a video file with explicit timestamps (PyAV), LED blinks, taps, a synthetic skeleton and `truth.json` (known offset and drift). |
| S3 | Session folder + sync detectors | cloud | `data/multimodal/<id>/` format (git-ignored) referencing the raw recording; LED code and tap detectors recover the injected offset within 4 σ. |
| S4 | Pose adapter | cloud + laptop | `rtmlib` RTMPose-m adapter with pinned weight hash; smoke test on a public clip in the cloud; throughput measured on the laptop. |
| S5 | Strokes + metrics | cloud | On synthetic data: stroke F1 ≥ 0.95, onset error within the sync floor, validity flags; `strokes.csv` and `summary.json`. |
| S6 | Viewer video panel | cloud | Range-streamed video, skeleton overlay, stroke table, sync grade; the EMG cursor follows the video; HEVC→H.264 proxy for playback only. |
| C0 | Camera capability probe | laptop | 2 min and 20 min test clips checked: real fps, timestamp regularity, audio presence, codec, size, dropped frames, heating. Picks the camera and mode. |
| H1 | Flash 2.2.0 + USB regression | laptop | Flash backed up; hardware tests pass; 5 min serial recording with 0 missing; Wi-Fi passphrase set locally, nothing secret in git. |
| H2 | Bench Wi-Fi integrity + noise A/B | laptop | 10 min on a power bank at 3 m and 10 m: 0 lost, 0 brownouts; radio-on vs radio-off floor within 1 dB. |
| H3 | Gate 6c + LED bench test | laptop | Owner passes 6c; LED switching artefact on EMG measured; LED vs taps offsets agree within the reported error. |
| H4 | Shadow swings, then court | laptop | Shadow swings with 20 manual contact labels, then a 20 to 30 min solo court session: losses ≤ 0.1 %, sync grade B or better, per-stroke report produced locally. |

**MVP accepted when:**

- **Synthetic:** a generated session goes through the real writers, and onset vs contact is recovered
  within 4 σ of the sync floor.
- **Hardware:** one court session with at least 50 strokes, EMG loss ≤ 0.1 %, sync grade B or better
  (≤ 5 ms), and all of it produced on the laptop.

## 7. Open questions for the owner

Each is needed by the milestone shown; none blocks the cloud milestones P0 to S6.

- **H0:** when can the board be fixed (boot loop, floating GPIO35/36)?
- **H2:** is there a power bank? Does it stay on at about 130 mA, and does it have pass-through charging?
- **C0:** which phone, and does its 240 fps mode record audio and run for 20 minutes?
- **H4:** which hand holds the racket? The electrode site has to be cleared on that forearm.
- **H4:** where can the laptop sit at the court, and how far is it from the player?
- **S5:** are reference contractions (grip, wrist flexion, wrist extension) at the start and end of a
  session acceptable, plus short isometric holds every 10 minutes for the fatigue trend?
- **Later:** is a cheap IMU (MPU-6050) the next sensor? It gives continuous sync and camera-free stroke
  detection.
