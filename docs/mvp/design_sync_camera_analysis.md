> **Reference design (draft, 2026-11-19).** Written before review. Where it disagrees with
> [MVP_MULTIMODAL_PLAN.md](MVP_MULTIMODAL_PLAN.md), the plan wins: see its sections 4 and 5 for the
> corrections (one protocol addendum, boot id, buffer sizes, verb-only upper-casing, PING with t_rx/t_tx,
> liveness watchdog, placement off the racket arm). Research use, not for diagnosis.

# Time sync, camera and per-stroke analysis: design

Scope: one timeline for EMG, video and pose; picking a pose estimator; per-stroke analysis; a session folder; a viewer video panel; and synthetic multimodal ground truth. The design does not depend on a particular camera. The reference camera is a phone at 120–240 fps, and a laptop webcam is the alternative. Wireless EMG transport is a separate work package. This design only needs that package to keep `t_device_us` per frame (see "Requirements on the wireless design").

Repo facts this design builds on (read, not changed):
- **Firmware:** `firmware/emg_esp32_v2` sends EMGM/2 DATA packets. Each carries `first_index` (a sample counter on the ADC clock) and `t_us`, the esp_timer time at the last row (µs since boot). Both run off the same 40 MHz crystal.
- **The host decoder (`acquisition/emgm2.py`) raises on unknown packet types.** New device information therefore goes into existing LOG/ACK packets as `key=value` text. LOG packets currently only reach `esp32.py` → `_event`.
- **Raw recording:** `recording/raw.py` writes `frames.csv` with `run, seq, first_index, n, t_device_us, t_host_ns, …`. `t_host_ns` is `Chunk.t_host = time.time()` at arrival, which is wall-clock time and can be stepped by NTP. `events.jsonl` holds markers with `index` and `run`. The format is `emgmirror-raw/1` and is append-only.
- **Safety:** a person may be recorded only with the laptop on battery (`acquisition/safety.py` checks this), and only on the right forearm, with the 2-channel config GPIO35/36.
- **Git:** `.gitignore` excludes `data/raw`, `data/sessions/*` and `data/derived`. A new `data/multimodal/` folder must be added to it.

---

## 1. One shared timeline

### 1.1 Clocks

| clock | symbol | resolution / behaviour |
|---|---|---|
| ESP32 device | `t_dev` (s) | From `first_index/fs` per run, anchored by `t_us`. The sample index is exact; `t_us` is the time base. Crystal error up to ±10–40 ppm against true time. Resets on reboot, so each boot is its own segment. |
| laptop host, monotonic | `t_mono` | `time.perf_counter_ns()` (QueryPerformanceCounter), sub-µs, never steps |
| laptop host, wall | `t_wall` | `time.time_ns()`, which is what `frames.csv` holds today. NTP can slew or step it. |
| laptop camera | `t_mono` at frame grab | carries a fixed, unknown capture latency (exposure to delivery, typically tens of ms) |
| phone video | `pts` (s) | container presentation times from the phone's own clock. Often variable frame rate, so read every frame's pts and never assume a constant fps. |
| phone audio | audio sample index / 48 kHz | same file, but the audio/video offset inside the phone is unknown (some ms to tens of ms) |
| future sensors | own clock each | each one gets its own sync model to `t_dev` |

**Session clock = the EMG device clock**, `t_dev`, in seconds. Within one run, sample *i* sits at `t_dev(i) = t0_run + i/fs_dev`. `t0_run` and `fs_dev` come from a least-squares fit of the frames.csv rows (`first_index + n − 1` against `t_device_us`), which is exact to within µs. Every other stream *s* gets a model

```
t_dev = θ_s + (1 + δ_s) · t_s        (δ in ppm; piecewise if a stream has a discontinuity)
```

stored in `sync/model.json`. The device is the master clock because it is the only clock that also exists when the ESP32 logs on board with no host at all. All analysis times are in `t_dev`. Host time is only an intermediate step for laptop-attached cameras.

### 1.2 Case (a): camera on the laptop (webcam or depth camera)

The chain is frame → `t_mono` → `t_dev`.

1. **Frame to host.** The capture process stamps each frame with `perf_counter_ns()` when it is grabbed, and also records the driver/container timestamp if the driver gives one. Frames arrive after a camera latency `L_cam`, which is roughly constant per camera and mode and is unknown. `L_cam` is measured once per camera and mode with the ESP32 LED (see 1.4) and stored in `configs/cameras/<name>.yaml`. It is checked again in every session.
2. **Host to device.** `frames.csv` gives one pair (`t_host`, `t_device_us`) per chunk, 200 per second. Each pair is `t_host = t_dev_mapped + latency`, where latency is always positive. The method is the classic one-way-delay estimate: fit the line through the **lower envelope** (the minimum `t_host − t_dev` in 2 s windows, then a robust linear fit). That gives the drift exactly and the offset up to the *minimum* transport delay `d_min`. `d_min` is about 1–3 ms over USB-CH340 and a few ms over Wi-Fi; it is a bias and cannot be seen without an external reference.
3. **PING to bound `d_min`.** A new firmware command `PING <token>` returns an ACK `cmd=PING ok=1 token=… t_us=…`. The host logs `host_send_mono`, `host_recv_mono` and `t_us`. The NTP-style offset is `t_us − (send+recv)/2`, with an error bound of ±RTT/2. The host sends a PING burst every 10 s and keeps the lowest-RTT samples. This gives the bias bound in ms that goes into the report.
4. **Wall-clock fix.** The recorder writes a `clock` event (`time_ns`, `perf_counter_ns`) every 10 s to `events.jsonl`. That is a new event kind only; FORMAT stays `raw/1`. `frames.csv` `t_host_ns` (wall time) can then be converted to `t_mono` and any NTP steps can be detected. This adds no column, so old readers keep working.

The expected result: device↔host sync error ≤ 2 ms bias (the PING bound) and well under 1 ms of drift residual. The camera adds `L_cam` uncertainty (calibrated ± about one frame) plus frame quantisation of 1/fps. A 30 fps webcam therefore gives about ±17 ms. That is good enough for "which phase" but not for onset-vs-contact.

### 1.3 Case (b): phone recording its own file (no shared clock)

Two rules: every sync event must be **visible or audible to the phone**, and it must be **timestamped on the device clock**. The ESP32 can do both with parts costing a few cents.

**Sync sources, in order of preference:**

| # | source | parts / cost | phone sees | device time from | precision |
|---|---|---|---|---|---|
| A | **ESP32-driven LED, coded blinks** | onboard LED (GPIO2 on most DevKits, free) for close range; a bright 5 mm LED + 220–470 Ω on a free GPIO (e.g. 25/26/27) for court distance, about €0.20 | brightness in a small region of the frame | firmware logs the toggle at a sample index | ±½ frame (±2.1 ms at 240 fps, σ ≈ 1.2 ms). Sub-frame accuracy is possible from partial-exposure brightness. |
| B | **piezo buzzer chirp**, same instant as A | passive piezo disc about €0.50 on a GPIO (LEDC PWM, 4 kHz, 20 ms) | audio track at 48 kHz | same | about 0.1 ms in audio, plus sound travel time (2.9 ms/m) and the phone's A/V offset, which this also *measures* (A and B are simultaneous) |
| C | **3 sharp taps** (racket frame or palm on the electrode-side forearm or the wall) | none | wrist deceleration in the video, impact sound in the audio | the EMG motion artefact (broadband spike) | ±1 frame in video. Artefact onset ±1–2 ms. The physiological delay doesn't matter because the artefact is mechanical. |
| D | clap | none | audio + video | none on the device | only measures the phone's A/V offset |

**Coding.** Each sync burst is 3 short pulses (30 ms on) whose gaps encode a burst number *k*, for example gaps of 100 + 40·b ms for 4 bits. Every burst can then be identified on its own, a missed burst cannot shift the matching, and a detector can search the whole frame for the region whose brightness over time correlates with the known code. The user does not have to mark a region by hand.

**Firmware 2.2.0 additions** (protocol stays EMGM/2):
- `SYNC` fires one burst now.
- `SYNCAUTO <period_s>` fires a burst every N s while running (default 0 = off; recommended 60).
- `SYNCPIN <gpio>` sets the LED pin; the buzzer pin is optional. Both are stored in NVS, like PINS, and appear in HELLO as `sync_pin`, `buzz_pin`.
- Every pulse edge is reported as a LOG packet: `level=info kind=sync burst=k edge=1 run=r index=<sample index at edge> t_us=<esp_timer>`. The edge is scheduled *at a sample index* (from the ADC frame callback), so it lands exactly on the EMG sample grid.
- `PING <token>`, see 1.2.
- **Host side:** `esp32.py` turns `kind=sync` LOG packets into `RawRecorder.event("device_sync", run=, index=, t_us=, burst=, edge=)` and PING replies into `event("clock_ping", …)`. The emulator implements the same, including a configurable crystal error.
- **Electrical caution:** switching an LED or buzzer on the shared 3V3 rail can couple into the Candy supply and the ADC. That artefact must be measured on the bench first, with no body connected. Mitigations: a 1 kΩ series resistor (dimmer LED), short pulses, and a small transistor switching the LED from the ESP32's battery side. Any artefact is also flagged, ±50 ms, in the EMG validity mask.

**Matching and fitting** (`sync/match.py`, `sync/clock.py`):
1. A coarse prior from the MP4 `creation_time` (wall time with about 1 s resolution, phone wall clock) against the recording start bounds the search to about ±5 s. Without it, the search covers the whole session.
2. The detectors produce candidate events per stream (LED code hits in video, chirps in audio, tap triplets in EMG and video).
3. The code IDs pair the events. Taps are paired by triplet pattern, with RANSAC over the pairs.
4. A robust fit (Huber or Theil–Sen) of `t_dev = θ + (1+δ)t_s`. With one event only, δ is fixed at 0 and the report gives a growing bound `|t − t_event| × 50 ppm`.
5. **Sanity gates:** |δ| > 500 ppm means the file was re-timed (for example a slow-motion export conformed to 30 fps, which stretches time ×8) and is rejected with a clear message. A per-event residual above 3× the quantisation floor is an outlier and is dropped and listed.
6. The A/V offset inside the phone file comes from the simultaneous LED and chirp (or clap) pairs: `av_offset = t_audio − t_video − d/343`. It is stored per phone and mode and checked in every session.

**What to record on court:** before play, 3 taps plus a SYNC burst with the arm in view of the camera. During play, SYNCAUTO every 60 s with the LED facing the camera most of the time; missed bursts don't matter. At the end, taps plus a burst again. Two well-separated events fix the drift. With crystals up to about ±50 ppm apart, the drift is about 3 ms per minute, or 60 ms over 20 minutes. Drift correction is therefore required, not optional.

### 1.4 Measuring and reporting the residual sync error

`sync/report.py` writes, per stream, in `sync/model.json` and the viewer header:

| field | meaning |
|---|---|
| `theta_s`, `drift_ppm` | fitted model |
| `n_events`, `span_s` | how many events, and over how long |
| `loo_rms_ms`, `loo_max_ms` | **leave-one-out**: refit without event *j*, predict event *j*, repeat for all events. This is the honest residual. |
| `independent_check_ms` | the model fitted on LED events, evaluated on the tap events (or the other way round). Taps are a physically different marker, so this catches LED-detector bias. |
| `quant_floor_ms` | 1/(fps·√12), the lower limit set by the frame rate |
| `bias_bound_ms` | for laptop cameras: ±RTT/2 from PING plus the `L_cam` calibration spread |
| `extrapolation_ms(t)` | for times outside [first, last] event: prediction interval of the fit |
| `grade` | A ≤ 3 ms, B ≤ 8 ms, C ≤ 20 ms, else F. Analysis refuses onset-vs-contact timing below grade B. |

Every timing metric downstream carries `± sync_ms`, combined in quadrature with its detector uncertainty.

**One-off calibration on hardware (bench, no body connected):**
- Phone film of the LED and buzzer for 10 minutes with SYNCAUTO 10 s. This gives drift and LOO for this phone, plus its A/V offset.
- Webcam pointed at the LED. This gives `L_cam` and its jitter.
- EMG noise floor with the LED pulsing against the LED off. This gives the LED artefact.

### 1.5 Requirements on the wireless design (interface to that work package)
- Each frame must still carry `t_us` and `first_index`. Wi-Fi arrival jitter is larger than USB, but the lower-envelope fit uses 200 packets per second and copes with it.
- PING must work over the wireless link.
- On-board logging (SD card or flash) has no host time. `frames.csv t_host_ns` must then be allowed to be empty (today the recorder writes `int(chunk.t_host*1e9)` unconditionally). Camera sync then relies only on the device-driven LED and chirp, which this design already makes the primary method.
- With the ESP32 on its own battery and no cable to the laptop, the body circuit changes. The safety gate needs an explicit amendment for that configuration; it is not covered today.

---

## 2. Pose estimation for fast racket swings

Hardware: a 2-core, 7.6 GB Windows laptop without a known GPU. Pose runs **offline**, and only locally on personal video. The cloud runs pose only on synthetic or public clips (for CI and benchmarks).

| estimator | licence | keypoints | how it handles speed | CPU on 2 cores (estimate, to benchmark) | verdict |
|---|---|---|---|---|---|
| **RTMPose (via `rtmlib` + onnxruntime)** | Apache-2.0 (code); weights trained on COCO/Halpe | 17 COCO / 26 Halpe (feet) / 133 whole-body (RTMW) | top-down and **per frame, no temporal smoothing**, so no timing lag. A SimCC head keeps the wrist precise at small sizes. | RTMPose-s/m 256×192: about 15–30 crops/s. The YOLOX/RTMDet detector costs extra, but a single subject allows box tracking from the previous frame's keypoints with re-detection every N frames. | **Primary.** No torch/mmcv needed; light enough. |
| MediaPipe Pose Landmarker (BlazePose lite/full/heavy) | Apache-2.0 | 33 (incl. hand tips, z) | VIDEO mode tracks a region from the previous frame and **filters landmarks over time**. That adds lag, and fast wrists can leave the region. IMAGE mode avoids both but is slower. | lite about 20–30 fps, heavy a few fps | **Fallback only, after measuring its lag** on synthetic and bench clips |
| YOLO11/YOLOv8-pose (Ultralytics) | **AGPL-3.0** | 17 | single stage, robust | about 5–15 fps (n/s models) | Avoid as a dependency of an Apache-2.0 public repo; at most an optional local plugin |
| ViTPose / HRNet (MMPose) | Apache-2.0 | 17+ | most accurate | too slow on CPU; overnight runs only | reference for spot checks |
| Depth camera skeletons (Orbbec Femto + K4A body tracking, Azure Kinect is discontinued; RealSense has no first-party skeleton) | proprietary SDKs | 32 (3D) | **30 fps body tracking**, which is too slow for a 100–150 ms swing; short range; needs a GPU for the tracker | — | **Not for the MVP** (cost €300+, not frugal); keep for later 3D work |

**Frame rate and blur.** The acceleration phase of a squash swing lasts about 100–150 ms, and wrist speed peaks just before contact.
- **240 fps** samples it every 4.2 ms, enough to place the peak within about 5 ms. **120 fps** is the minimum. **30 fps** only says "a stroke happened".
- At 240 fps the phone exposes for ≤ 4 ms. At a wrist speed of about 10 m/s that is about 4 cm of blur, which human-pose models tolerate. The racket head (40–60 m/s) blurs heavily and has no keypoint in these models, so racket speed is out of scope for the MVP.
- Indoor court light at 240 fps means high ISO and noise. Record 720p240 or 1080p240 in **H.264 ("Most compatible")**, because software HEVC decoding on 2 cores is slow.
- Transfer the **original file** (not a re-timed slow-motion export). The drift gate in 1.3 catches mistakes.

**Two-pass offline plan (2-core budget).** A 20-minute session at 240 fps is 288k frames, or 3–5 hours if every frame goes through the model.
- Pass 1 runs every 8th frame (30 fps), about 36k frames (about 30–45 minutes). It does person tracking and coarse stroke candidates.
- Pass 2 runs full rate only from −400 to +200 ms around each candidate: about 100 strokes × 0.6 s × 240 ≈ 15k frames (about 15 minutes).
- The run can be resumed and is cached per frame range, so it can run overnight. Decoding uses PyAV, with d3d11va hardware decode if the iGPU has it.

**Post-processing** (`pose/filter.py`):
- Gaps where confidence is below 0.3 are interpolated only up to 3 frames; longer gaps are flagged.
- Zero-phase Butterworth at 12–20 Hz on positions, before differentiation.
- Speed is in body lengths per second (pixels divided by median torso length), so it does not depend on camera distance.
- The owner's track is selected by the subject the sync gesture is made on. Other tracks are dropped and never written out. **Only the owner is filmed**: solo drills, or the camera framed so that no other person is in view.

---

## 3. Analysis

### 3.1 Stroke detection and contact time
1. **Candidates:** peaks of racket-side wrist speed above k× the session median, at least 0.8 s apart and at least 150 ms in duration.
2. **Contact time, in order of trust:**
   - (i) **Audio impact**, when there is audio. A ball-on-strings transient within ±150 ms of the wrist peak, minus the phone's A/V offset and the sound travel time (2.9 ms/m; distance estimated from camera geometry or entered).
   - (ii) **Manual frame labels** made in the viewer (frame-step, key C). These are also the ground truth for measuring the automatic estimator on a subset.
   - (iii) **Wrist-speed peak plus a per-person offset** learned from (i)/(ii). Without either, it is reported as "peak wrist speed", not "contact".
3. **Side** (forehand/backhand) is a heuristic from wrist x relative to the torso midline and swing direction; it is flagged as a heuristic.
4. **EMG-only fallback** (no video): the impact artefact plus the burst peak, flagged with low confidence.

### 3.2 Per-stroke EMG windows
Window: contact −600 to +400 ms. Phases are preparation [−600, −150), acceleration [−150, −20), **impact [−20, +30] excluded** (vibration artefact; the EMG validity mask also drops LED artefacts and ADC-invalid rows), and follow-through (+30, +400].

Processing reuses `processing/filters.py` and the offline cleaning path: band-pass 20–450 Hz (20 Hz instead of 75 Hz, so motion artefact is rejected without throwing away the spectrum the MDF needs), a mains notch, and an RMS envelope with a 25–50 ms window.

### 3.3 Metrics

| metric | definition | when it is valid |
|---|---|---|
| **Onset vs contact** (ms, per channel) | The first point where the TKEO-conditioned envelope stays above baseline + 3 SD for ≥ 25 ms (Solnik-style). Baseline = the ready-position stretch before the stroke (lowest-RMS 200 ms in [−1.5, −0.6] s). Reported as `onset − contact ± sqrt(sync² + detector²)`. | sync grade A/B; a quiet enough baseline (a flag is set when the baseline is above 30% of the peak, as in fast rallies) |
| **Flexor/extensor balance** | the ratio of **normalised** phase RMS (`%ref_flex / %ref_ext`) and the co-contraction index (Falconer–Winter: 2·∫min(f,e) / ∫(f+e) on the normalised envelopes) per phase | only after normalisation. A raw µV ratio is meaningless (skin, electrodes and gain differ per channel). |
| **Normalised RMS** | phase RMS divided by the channel's reference contraction RMS (1 s plateau). The reference reuses the existing cues `grip_mvc`, `wrist_flex_mvc`, `wrist_ext_mvc` (`acquisition/livecheck.py`), done **at session start and end**. | The two references must agree within 15%; otherwise it is flagged (sweat lowers skin impedance, and electrodes shift). Without an MVC the fallback is "% of the session's median stroke peak", **labelled as such, not %MVC**. |
| **Fatigue trend** | median frequency (Welch, 20–450 Hz) per stroke over a fixed acceleration-phase window, plus RMS. Robust slope with a 95% CI against time, and against stroke number. | **Exploratory** in dynamic strokes (non-stationary signal, force and speed vary, the muscle warms up and its MDF rises). The **valid** fatigue measure is a standard **isometric test hold** (for example 5 s grip at 50% of the reference, every 10 minutes): its MDF slope and RMS rise are reported as the primary fatigue result. |
| **Peak timing order** | flexor peak minus extensor peak, per stroke | grade A sync not needed (both are EMG on the same clock) |
| **Kinematics** | peak wrist speed (body lengths/s), swing duration, stroke rate | 2D only; it depends on the camera angle, so keep a fixed camera position across sessions |

### 3.4 What two surface channels can and cannot tell
- **Can:** the timing of the two **regions** (palm-side flexor mass and dorsal extensor mass), their relative normalised activity, co-contraction, change over a session, and stroke-to-stroke consistency.
- **Cannot:**
  - Individual muscles. FCR, FCU and FDS cannot be told apart, and neither can ECRL/B and EDC (crosstalk; the viewer already says "two regions, not single muscles").
  - Deep muscles (pronator teres, supinator).
  - Grip force against wrist torque, and force in newtons. There is no validated EMG–force model during dynamic swings.
  - Absolute comparisons across days without re-normalising.
  - Motor-unit measures.
  - Anything "good technique" or injury-risk without a coach or literature reference.
  - The left arm, which is blocked by the safety gate.
- **Contaminations to flag, not hide:** the impact transient, cable or ESP32 sway (wireless helps), sweat-driven amplitude drift, and electrode lift during lunges.

---

## 4. Session folder (extends the existing formats, replaces nothing)

`data/multimodal/<session_id>/`. This path must be added to `.gitignore`. Raw recordings stay where they are and stay append-only.

```
session.json              format "emgmirror-mm/1"; id; created; notes; protocol (sport=squash, drill)
                          streams: [{id:"emg0", kind:"emg", raw_recording:"<rid>", raw_root:"data/raw",
                                     sha256_samples:"…", device_mac, boot_segment},
                                    {id:"cam0", kind:"video", file:"video/cam0.mp4", sha256, camera:"phone-xyz-240",
                                     clock:"pts" | "host_mono"}, {id:"aud0", kind:"audio", of:"cam0"}]
                          session_clock: "emg0.device"
video/cam0.mp4            the original file, copied, never re-encoded (hash checked)
video/cam0.frames.csv     frame, pts_s, [host_mono_ns], keyframe, decoded_ok
audio/cam0.wav            optional (extracted for detection); derived, can be deleted
sync/events.csv           stream, kind(led|chirp|tap|clap|manual), burst, t_stream_s, t_dev_s(matched or empty),
                          detector, confidence, used(1/0), residual_ms
sync/model.json           per stream: theta_s, drift_ppm, method, valid_from/to, the report fields (1.4), version
pose/cam0.rtmpose-m.npz   pts_s[F], frame[F], kp[F,K,2] px, score[F,K], bbox[F,4], track_id, pass(1|2)
pose/cam0.rtmpose-m.json  model, weights hash, licence, input size, keypoint names, filter settings, runtime
analysis/strokes.csv      stroke, t_contact_dev_s, contact_source, contact_unc_ms, side, peak_wrist_bl_s,
                          per channel × phase: rms_norm, onset_ms, onset_unc_ms, mdf_hz; cci; flags
analysis/reference.json   which reference contractions (raw rid + marker), RMS per channel, start/end agreement
analysis/summary.json     fatigue slopes ± CI (test holds and strokes), counts, validity flags, sync grades
```
- **Raw recordings need no format change.** Only new event kinds go into `events.jsonl`: `device_sync`, `clock_ping`, `clock`. Readers that don't know them ignore them.
- **Multiple EMG recordings, or a device reboot**, give separate boot segments. Each gets its own fit to the master segment through shared LED events, so a mid-session reboot can be recovered.
- **Future sensors** (IMU, HR, …) are more `streams` entries, each with its own clock and `sync/model.json` entry.
- **Code:** `storage/multimodal.py` (dataclasses plus load/save, with hash checks), separate from `storage/session.py`, which stays for the dataset sessions.

---

## 5. Viewer: video panel and skeleton overlay

Changes to `src/emgmirror/viewer`:
- **`server.py`:**
  - New routes `GET /api/mm` (list sessions), `/api/mm/<sid>/meta` (session.json, sync models, stroke table), `/api/mm/<sid>/pose/<cam>` (binary: header plus float32 pts, kp, score), and `/api/mm/<sid>/strokes`.
  - `/media/<sid>/<cam>` with **HTTP Range (206) support**. The current `_file()` sends the whole file, and browsers need Range requests to seek in video.
  - The same 127.0.0.1-only `_local_request` guard and ID_RE name checks apply.
- **`static/index.html` + new `static/video.js`:** a resizable video pane above the EMG lanes. A `<canvas>` over the `<video>` draws the skeleton (bones, the racket-side wrist highlighted, confidence as opacity).
- **Video drives the clock during playback.** `requestVideoFrameCallback` gives the `mediaTime` of the frame actually shown. That frame time goes through the sync model to `t_dev`, which moves the EMG cursor. When scrubbing the EMG timeline, the inverse maps `t_dev` to pts to `video.currentTime`, snapped to the nearest frame.
- **Other controls:** playback 0.1× to 1× for slow motion, and frame step with `,` and `.`.
- **New lanes and table:** a **wrist-speed lane**, contact lines and phase bands on all lanes, and a **stroke table** like the trials table (click → zoom EMG to the window, seek the video).
- **Header:** sync grade and `±loo_rms ms` per stream; grades below B shown in red.
- **Manual labels:** key C stores a contact label, T a tap label. Both are written to `sync/events.csv` / `analysis/manual_labels.csv` through a POST that only answers on localhost.
- **`static/emgcore.js`:** `mapClock(model, t)` and `invMapClock`, tested in Node against the Python implementation (same pattern as `tests/test_viewer_js.py`).

The existing raw-recording view is unchanged when no session is opened (`emgmirror view --session <sid>`, or picked in the list).

---

## 6. Synthetic multimodal generator (cloud-testable ground truth)

`src/emgmirror/simulate/squash.py` + `simulate/clocks.py`, reusing `movements.py` / `motor_units.py` / `frontend.py`:
- **Schedule:**
  - MVC reference cues at the start and end, with taps plus SYNC bursts.
  - N strokes (forehand/backhand) at intervals of 2–5 s, with SYNCAUTO bursts every 60 s.
  - Isometric test holds every 10 minutes.
  - Optional rally segments with no quiet baseline.
- **Kinematics:** a 2D Halpe-26/COCO-17 stick figure in px. The racket-arm swing has a bell-shaped wrist speed peaking at `contact − Δ_peak` (truth). The generator also injects keypoint jitter, confidence drops and missing frames at peak speed (blur), and a camera at 120/240/30 fps with optional VFR pts jitter.
- **EMG:** extensor and flexor drives with known per-stroke onsets (for example ext −140 ± 20 ms, flex −90 ± 20 ms relative to contact) through the existing motor-unit model. On top of that:
  - an impact artefact at contact
  - tap artefacts
  - optional LED-switching artefacts
  - fatigue, as a progressive MUAP widening (lower conduction velocity, so lower MDF, with a known slope) and amplitude rise
  - a sweat-driven gain drift
- **Clocks (the point of the exercise):** true time `t`. Device: `t_dev = t·(1+ε_d)`. Phone: `pts = (t − θ_c)·(1+ε_c)`, with ε drawn from ±50 ppm and θ_c up to ±10 s, plus a stated A/V offset. Host: `t_host = t·(1+ε_h) + θ_h + latency`, where latency is drawn from a USB or Wi-Fi distribution with stalls. PING replies get RTT noise.
- **Outputs, through the real writers:**
  - A raw recording via `RawRecorder` with `Chunk`s, so frames.csv and events.jsonl (including `device_sync`, `clock_ping`, `clock`) are exercised.
  - `video/cam0.mp4`: a small rendered stick figure plus an LED blob, written with opencv-headless or PyAV, 320×240 to stay small. Plus `frames.csv`.
  - `audio/cam0.wav` with chirps, taps and impacts, including sound-travel delay.
  - `pose/cam0.synthetic.npz`, the skeleton itself, so the analysis can be tested without a model.
  - `truth.json` with all injected offsets, drifts, onsets, contacts, MDF slopes and artefact times.
- **CLI:** `emgmirror sim squash --minutes 5 --fps 240 --camera phone|webcam --seed 1 --out data/multimodal/`.
- Pose models can't be checked on stick figures. The CI pose test runs RTMPose through rtmlib on a small **public** clip, or only checks the adapter on a few frames: output schema, keypoint order, and no lag against a frame-shifted copy.

---

## Concrete changes per file or module

| where | change |
|---|---|
| `firmware/emg_esp32_v2/emg_esp32_v2.ino` | v2.2.0: SYNC, SYNCAUTO, SYNCPIN, PING; edge scheduling at a sample index; LOG `kind=sync`; HELLO `sync_pin`, `buzz_pin`; buzzer through LEDC |
| `docs/ACQUISITION_PROTOCOL.md` | new commands, the LOG kind=sync fields, PING semantics |
| `acquisition/esp32.py`, `emulator.py` | route sync/PING to recorder events; periodic PING thread; the emulator emits sync with a configurable ppm |
| `recording/raw.py` | `clock` event every 10 s; allow an empty `t_host_ns` for on-board logs; helper `device_timebase(rec)` (fit per run and boot) |
| new `sync/` | `clock.py` (LinearClock, robust fit, LOO, piecewise), `detect.py` (LED code in video, chirp/tap in audio, tap triplets in EMG, wrist taps in pose), `match.py`, `hostclock.py` (lower envelope plus PING), `report.py` |
| new `video/` | `probe.py` (PyAV per-frame pts → frames.csv, VFR and re-timing checks), `capture.py` (laptop camera: OpenCV or an ffmpeg-dshow MJPEG copy, perf_counter stamps), `render.py` (synthetic) |
| new `pose/` | `base.py` (Estimator protocol plus npz schema), `rtm.py` (rtmlib), `mediapipe_.py` (optional), `synthetic.py`, `run.py` (two passes, resumable), `filter.py` |
| new `athlete/` (or `analysis/strokes.py`, `analysis/athlete.py`) | stroke detection, contact estimation, windows, metrics, fatigue, validity flags |
| new `storage/multimodal.py` | session folder I/O |
| `simulate/squash.py`, `simulate/clocks.py` | generator, see section 6 |
| `viewer/server.py`, `static/index.html`, new `static/video.js`, `emgcore.js`, `viewer.css` | section 5 |
| `cli.py` | `sim squash`; `mm new`, `mm add-video`, `mm sync`, `mm pose`, `mm analyze`, `mm report`; `view --session` |
| `pyproject.toml` | extras: `video = [av, opencv-python-headless]`, `pose = [rtmlib, onnxruntime]`, `mediapipe = [mediapipe]`; core deps unchanged |
| `.gitignore` | `data/multimodal/` |
| docs | `docs/SYNC.md`, `docs/MULTIMODAL_SESSION.md`, `docs/ATHLETE_ANALYSIS.md` (including the validity table in 3.4), an update to `SIGNAL_VIEWER.md` |

## Test plan

**Cloud** (pytest, synthetic only):
- `test_sync_clock.py`:
  - With 2 events per 20 minutes at 240 fps: recovered θ within ±1 ms and δ within ±2 ppm of the truth.
  - With single-event mode, the reported extrapolation bound covers the true error.
  - The LOO RMS lies between 0.8× and 1.5× the true per-event error.
  - Re-timed (×8) and outlier events are rejected.
- `test_hostclock.py`: lower-envelope offset bias ≤ d_min plus 0.5 ms under USB and Wi-Fi latency models with stalls; PING bound covers the truth.
- `test_sync_detect.py`: LED code found in the rendered MP4 with no region given; chirps found in the WAV; tap triplets found in the EMG; A/V offset recovered to within 1 ms.
- `test_video_probe.py`: per-frame pts from VFR synthetic files.
- `test_strokes.py`:
  - Stroke F1 ≥ 0.95.
  - Contact error from audio ≤ 2 ms; from wrist speed after learning the offset ≤ 10 ms.
  - Onset error within ±15 ms at grade A sync.
  - The sign and rough size of the MDF slope recovered on test holds.
  - CCI within ±0.05.
  - The flags fire (baseline too high, references disagree).
- `test_multimodal_storage.py`: round-trip and hash mismatch detection.
- `test_viewer_mm.py`: Range requests (206, partial bytes), the pose binary, localhost guard, unsafe names. Plus Node tests for `mapClock` against Python.
- `test_emgm2_protocol.py` additions: LOG kind=sync parsing, PING ACK; the emulator streams sync events.
- Optional, marked `pose`: an RTMPose smoke test on a public clip.

**Laptop and hardware** (owner, after git pull; nothing leaves the laptop):
1. **Bench, no body:** flash 2.2.0. Run `pytest -m hardware` extended for SYNC, PING and LOG kind=sync. Measure the LED/buzzer EMG artefact with the electrodes disconnected.
2. **Phone calibration:** film the LED for 10 minutes with SYNCAUTO 10 s. Expected: drift ppm, LOO ≤ 3 ms at 240 fps, and the A/V offset stored in `configs/cameras/`.
3. **Webcam calibration (if used):** `L_cam` and its jitter.
4. **Pose benchmark:** time pass 1 and pass 2 with rtmlib on a 1-minute clip of the owner (stays local) on the 2-core laptop; choose model size.
5. **Indoor dry run** with shadow swings at home, and the safety conditions as currently written: compare tap-based and LED-based offsets (the independent check), and label contacts by hand for 20 swings to measure the automatic contact error.
6. **Court session** only after the wireless work and a safety-gate amendment for the new body circuit.

## Risks
- The LED or buzzer current couples into the EMG. Mitigations: measure it, use a resistor or transistor, and mask ±50 ms.
- The LED is out of view (the player turns). Coded periodic bursts help, taps are the fallback, and drift needs only 2 good events.
- The phone re-times or exports slow-motion files. Transfer originals; the drift gate rejects re-timed files.
- Pose fails on blur or occlusion, or swaps the wrist. Use confidence gating, only the owner in frame, and the manual-label subset to measure the error.
- The 2-core CPU is too slow. Use the two-pass schedule, overnight runs and H.264 720p240.
- MediaPipe smoothing lag biases timing; it is not the default.
- Dynamic MDF fatigue is confounded. The primary result is the isometric test holds; stroke MDF is labelled exploratory.
- Sweat changes the normalisation. References at start and end, with an agreement check.
- The ESP32 boot-loop or a reboot splits device time. Each boot segment gets its own fit through shared events.
- Other people appear in the video (privacy). Solo drills; keep only the owner's track; video is served on localhost only and never goes into git.

## Cost
- About €0.20 for an LED and resistor (the onboard LED costs nothing), and about €0.50 for a piezo disc.
- A phone stand from household items (books or a clamp).
- Optional later: an MPU-6050 IMU, about €2–3, on the ESP32. It gives wrist acceleration on the device clock, which supports continuous sync by cross-correlation with pose wrist acceleration, and strokes and contact without a camera. It is the best next sensor.
- All software is free and open source and runs locally. No cloud compute is needed beyond CI.