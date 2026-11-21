> **Reference design (draft, 2026-11-20)** for the data-collection MVP. Written before review; where it
> disagrees with [COLLECTION_MVP_PLAN.md](COLLECTION_MVP_PLAN.md), the plan wins (it is cut down, adds the
> sync LED on GPIO21, a monotonic host clock column, and the safety-gate amendment). Research use, not for
> diagnosis.

# Live phone streams to the laptop: data-collection MVP (design, 2026-11-20)

Scope of this part: the Android phone as a normal camera and the iPhone (LiDAR) as a depth camera, both streaming **live and locally** to the Windows 11 laptop (2 cores, 7.6 GB RAM, on battery). It covers app choice, how each stream gets onto one timeline with the ESP32, the host software in this repo, and fakes for cloud development. Nothing may go to a cloud service. I only read the repo and changed nothing.

Repo facts used: `acquisition/base.py` (`DataSource`, `Chunk`, `SourceInfo`), `recording/raw.py` (append-only `frames.csv` with `t_device_us`, `t_host_ns` = `time.time()` at arrival), `docs/mvp/design_sync_camera_analysis.md` §1.2–1.4 (ESP32 coded LED, `SYNC`/`SYNCAUTO`, lower-envelope fit, `data/multimodal/` must be added to `.gitignore`), and the safety gate (laptop on battery).

---

## 0. Electrical safety note (read first)

A phone on a USB cable to the laptop is galvanically tied to laptop ground, which is the same ground as the ESP32 and the electrode front end. If that phone is also on a **mains charger**, a mains-referenced path reaches the body circuit, and the "laptop on battery" rule no longer protects. Rules for every session with electrodes on:
- Every phone runs on its own battery, with **no charger plugged in** while it is cabled to the laptop. Charge before the session.
- No USB hub with its own power supply.
- This should go into `docs/ELECTRICAL_SAFETY.md` as an amendment (owner decision). Wi-Fi links have no galvanic path, which is one argument for Wi-Fi if the cable rule is hard to keep.
- Phones charging from the laptop drain the laptop battery. Check runtime in the C0 probe.

---

## 1. Android normal camera: options compared

| Option | Transport | Protocol | Live res/fps (practice) | Phone-side frame timestamp | Cost / ads | Licence / source | Network / cloud behaviour | Python read path |
|---|---|---|---|---|---|---|---|---|
| **scrcpy camera mirroring** | USB (adb), or adb over TCP | H.264/H.265/AV1 packets, 12-byte header per packet | `--camera-size` / `--camera-fps` (default 30). With `--camera-high-speed`, 120/240 fps at the sizes `--list-camera-sizes` reports. Plan on 1280x720 or 1920x1080 at 30/60. | **Yes**: per-packet PTS (u61) in the frame header. The docs say timestamps are captured on the device, so network jitter does not affect them. | Free, no ads | Apache-2.0, open source (Genymobile) | No account, no cloud. Needs USB debugging (Developer options). | Own client: push the server jar, `adb forward`, parse headers, mux with PyAV (no decode needed to store). Or the scrcpy CLI `--record=x.mkv`. |
| IP Webcam (Pavel Khlebovich) | Wi-Fi (HTTP on :8080), or USB via `adb forward tcp:8080 tcp:8080` | MJPEG `/video`, JPEG `/shot.jpg`, RTSP `.../h264_pcm.sdp` | MJPEG 640x480–1280x720 at 15–30 fps over Wi-Fi in practice. MJPEG costs a lot of bandwidth (estimate; measure in C0). | **No** (MJPEG has none; RTSP RTP timestamps exist but follow the app's clock and are hard to reach through OpenCV) | Free version is **ad-supported**; a paid Pro version exists | Closed source | Ads mean network traffic to ad SDKs. Optional Ivideon cloud integration. Mitigation: phone in airplane mode and USB `adb forward`, or Pro on a Wi-Fi network with no internet. | `cv2.VideoCapture("http://127.0.0.1:8080/video")`; RTSP through PyAV/OpenCV-FFmpeg |
| DroidCam (Dev47Apps) | Wi-Fi or USB | Proprietary client, exposed as a Windows virtual webcam; also MJPEG in a browser | Free version low resolution (480p). Pro unlocks 720p/1080p, and higher through the OBS plugin. | No: the virtual webcam hides it | Free with ads. Pro is a paid one-time or yearly upgrade. | Closed source | Ads in the free version | `cv2.VideoCapture(index, cv2.CAP_DSHOW)`. Extra latency from the virtual camera driver. |
| Larix Broadcaster + MediaMTX | Wi-Fi | Larix **pushes** RTSP/RTMP/SRT to a server; MediaMTX (MIT, single Windows exe) runs on the laptop | 1080p30/60 H.264 | RTP/SRT timestamps exist, but arrive through MediaMTX | Larix is free with paid Premium/NDI; MediaMTX is free | Larix closed source; MediaMTX MIT | Larix App Privacy for Broadcaster not verified. MediaMTX telemetry not mentioned. | PyAV `av.open("rtsp://127.0.0.1:8554/phone")` |
| "RTSP Camera Server" type apps | Wi-Fi | RTSP | varies | varies | mostly ads | mostly closed | unknown | PyAV/OpenCV |

**Recommendation: scrcpy over USB.** It is the only option that is open source, has no ads, no account and no cloud, *and* carries a device-side timestamp per frame. It also stores the phone's own H.264 stream unchanged, so the laptop does no re-encoding and uses almost no CPU to store it. Requirement: Android 12 or later (open question). The Windows release ships its own `adb`.
- Step 1 (no code): `scrcpy --video-source=camera --camera-size=1280x720 --camera-fps=30 --no-audio --no-playback --no-control --record=android.mkv`. This proves the camera mode works and gives device-timestamped frames in the file. It gives no per-frame host arrival time.
- Step 2 (repo integration): a Python client for the scrcpy server protocol (`doc/develop.md`). Push `scrcpy-server` (Apache-2.0; pin the exact version, because the server checks that the version argument matches), `adb forward tcp:PORT localabstract:scrcpy_<scid>`, then start the server with `video_source=camera camera_size=1280x720 camera_fps=30 audio=false control=false tunnel_forward=true send_frame_meta=true`. Do **not** use `raw_stream=true`, which drops the PTS. Per packet, read the 12-byte header (config flag, key-frame flag, 61-bit PTS, size), then the payload. Write `frames.csv` with `seq, pts_us, t_host_mono_ns, t_host_wall_ns, keyframe, size`. The PTS time base is not stated in the docs. Most likely it is microseconds relative to the first frame (from MediaCodec); confirm it with the LED test and the C0 probe. Request a short I-frame interval (`video_codec_options=i-frame-interval:int=1`) so the preview and the file recover quickly after a drop.
- **Fallback A:** IP Webcam MJPEG over USB `adb forward` with the phone in airplane mode. This works without Android 12, needs only OpenCV, and stops the ads from reaching the network. It gives host arrival time only.
- **Fallback B:** scrcpy `--record` to a file only, copied after the session and aligned with the LED (same as the "phone records its own file" case in `design_sync_camera_analysis.md` §1.3).

## 2. iPhone LiDAR RGB-D: options compared

| Option | Live? | Transport | Depth | Colour | Per-frame timestamp | Intrinsics / pose | Cost | Licence / source | Data / cloud |
|---|---|---|---|---|---|---|---|---|---|
| **Record3D, USB streaming** | Yes | USB (usbmuxd via Apple Mobile Device Support / iTunes on Windows) | LiDAR depth (ARKit sceneDepth 256x192, up to 60 Hz), float metres, plus confidence map | RGB downscaled for streaming (exact size not documented; "2x higher LiDAR RGB resolution" since app 1.8.1; read it at runtime) | **Yes**: `get_misc_data()` returns JSON `{"metadata":{"relativeTimestamp":…, "unixTimestampOnReceivedFrame":…}}` (app ≥ 1.10.3). `relativeTimestamp` is the camera-hardware capture time, seconds since an unspecified start (device uptime). | `get_intrinsic_mat()` (fx, fy, tx, ty); per-frame intrinsics in metadata since 1.10; `get_camera_pose()` (quaternion + position) | App: Basic $3.99 or Full $4.99, one-time (US prices). USB streaming is in the core purchase. | App closed source. Python/C++ library `record3d` **LGPL-2.1**, open source, PyPI 1.4.1 (2025-04), Windows wheels for CPython 3.8–3.13 | App Store label: "does not collect any data"; the description says no analytics and no ads. The library talks only USB to the phone. |
| Record3D, Wi-Fi streaming | Yes | Local Wi-Fi, WebRTC (phone serves `/getOffer`, `/sendAnswer`, `/metadata`) | HSV-packed into the left half of a lossy video frame, 0–3 m only | right half | not in the video frames | `/metadata` K | +$2.99 extension pack | Demo MIT | Local only. The developer says depth is lossy and "mainly for entertainment". **Not acceptable as measurement data.** |
| Stray Scanner | No, record only | Copy over cable (Finder / Files / share sheet) | 16-bit PNG, mm, 256x192, one per RGB frame, plus confidence 0/1/2 | HEVC `rgb.mp4` (constant frame rate, fps selectable) | **Yes**: `odometry.csv` `timestamp` (s) per frame; `imu.csv` with timestamps | per-frame fx, fy, cx, cy and pose in `odometry.csv` | Free | MIT, open source | App Store: "does not collect any data". No cloud upload. |
| NeRFCapture, Polycam, other ARKit apps | NeRFCapture streams posed images on demand over DDS for NeRF training; not a continuous RGB-D stream (not researched in depth). Polycam is account and cloud based. | | | | | | | | **Rejected**: not continuous, or uses the cloud |

**Recommendation: Record3D USB streaming**, read in Python with `record3d.Record3DStream`. Use the `on_new_frame` callback, then `get_depth_frame()`, `get_rgb_frame()`, `get_confidence_frame()`, `get_misc_data()`, `get_intrinsic_mat()`, `get_camera_pose()`. Stamp `perf_counter_ns()` in the callback.
- Windows driver: the library README says to install iTunes. iTunes itself phones home to Apple (store, update checks), but only its **Apple Mobile Device Service / usbmuxd** part is needed. Options, most private first: (a) install only the "Apple Mobile Device Support" MSI extracted from the iTunes installer (a common practice; verify that record3d sees the phone that way); (b) install iTunes, never sign in, turn off update checks; (c) Microsoft Store "Apple Devices" app, whose compatibility with record3d is unverified. Owner decision.
- Ask the app for 30 fps if it offers that, or decimate on the host (logged as `decimated`, not as drops) to fit the 2-core budget. Use 60 fps only for short tests.
- **Fallback:** Stray Scanner recording to files, copied after the session with `scripts`/CLI `emgmirror import stray <folder>`. Its `timestamp` column is aligned with the same coded LED, visible in `rgb.mp4`. This is fully open source, free and collects no data, but it is not live.

## 3. Timing: one timeline with the ESP32

Clocks: the device clock `t_dev` (master, per `design_sync_camera_analysis.md` §1.1), host `t_mono = time.perf_counter_ns()`, and each phone's own clock (`pts_us` for scrcpy, `relativeTimestamp` for Record3D). Every frame row stores the phone time, `t_host_mono_ns` and `t_host_wall_ns`. A `clock` event every 10 s pairs mono and wall time.

Getting a phone frame onto the device timeline:
1. **Phone → host (removes jitter).** A robust lower-envelope fit of `t_host_mono − t_phone` against `t_phone` (minimum per 2 s window, then Theil–Sen) gives the phone↔host drift and the offset up to the minimum transport delay. Frames then get `t_host_est = a + (1+δ)·t_phone`, which is smooth and keeps the variable frame rate. For streams with no phone time (MJPEG), use arrival time with no smoothing. Never assume a constant fps.
2. **Host → device.** Use the existing EMG `frames.csv` envelope fit plus the PING bound (§1.2 of the sync design).
3. **Capture-to-arrival latency `L_s`.** It cannot be seen from the stream alone, so it is measured with the ESP32 LED. Firmware `SYNC`/`SYNCAUTO` fires coded bursts and logs each edge at a sample index (`device_sync` events). Each camera films the LED. The detector finds the LED onset frame k, and `L_s = t_host_est(k) − host_time(device edge)`, averaged over 20 or more edges. The LED onset falls uniformly inside a frame interval, so each edge has ±½ frame quantisation. The mean of N edges has σ ≈ (1/fps)/√(12N): about 2 ms at 30 fps and 1 ms at 60 fps with N = 20. Store `L_s` per phone and mode in `configs/cameras/<name>.yaml` and re-check it every session with 3 bursts at the start and 3 at the end.
4. **Depth:** Record3D depth and RGB share one ARFrame timestamp, so the LED seen in Record3D RGB also times the depth. Cross-check: a hand moving toward both the LiDAR and an HC-SR04 should give the same onset within one depth frame.
5. **Rolling shutter and exposure:** keep the LED near the image centre row (readout skew across the frame is about 10–30 ms), use a bright LED, and use short exposure / good light.

Expected accuracy per frame time, after calibration:
| stream | 30 fps | 60 fps |
|---|---|---|
| scrcpy (device PTS + envelope + LED) | ±10–17 ms (bounded by exposure / frame quantisation) | ±5–9 ms |
| Record3D USB (relativeTimestamp) | ±10–17 ms | ±5–9 ms |
| MJPEG over Wi-Fi (arrival only) | ±1–2 frames plus Wi-Fi jitter (tens of ms; measure) | rarely sustains 60 |
These are estimates; the C0 probe and the LED test give the real numbers. The report states `L_s`, its spread, jitter p95 and `loo_rms_ms`, as in §1.4 of the sync design.

## 4. Host software in this repo (proposal; nothing implemented)

```
src/emgmirror/streams/
  base.py        FrameSource protocol, Frame, StreamInfo
  scrcpy_cam.py  ScrcpyCameraSource  (adb + server jar + header parser; stores packets, no decode)
  mjpeg.py       MjpegHttpSource     (IP Webcam / DroidCam; requests multipart or OpenCV)
  rtsp.py        RtspSource          (PyAV; for Larix -> MediaMTX or IP Webcam RTSP)
  record3d_src.py Record3DSource     (record3d lib; lazy import so cloud tests run without it)
  stray_import.py import of Stray Scanner folders (fallback)
  writer.py      per-stream session writers (append-only, like raw.py)
  fakes/
    fake_scrcpy_server.py  TCP server speaking the scrcpy framing; H.264 from synthetic frames (PyAV), PTS with configurable drift/jitter/drops
    fake_mjpeg_server.py   http.server thread serving multipart JPEG at a set fps, with jitter and drops
    fake_record3d.py       drop-in object with the Record3DStream API (on_new_frame, get_depth_frame, get_misc_data JSON, ...)
  collect.py     orchestrator + `emgmirror collect check|record`
```
- `Frame`: `seq, t_host_mono_ns, t_host_wall_ns, t_phone_s | None, kind ("h264_packet" | "jpeg" | "rgbd"), payload, meta`. `StreamInfo`: kind, label, nominal fps/size, `has_phone_clock`, `is_real_hardware`, device identity (app and version, phone model), licence note.
- **The fakes share one simulated clock with `acquisition/emulator.py`.** The emulator's `SYNC` edges switch a white square on in every fake camera frame after a configured latency `L_s_true`, with drift and jitter. A test can then check end to end that the pipeline recovers `L_s_true` within ½ frame, finds dropped frames, and that the session folder validates. The RTSP path is tested by having PyAV open a local file; no MediaMTX binary is needed in the cloud. The real `record3d`/`adb` are imported lazily and skipped when absent.
- **Process model for 2 cores:** each phone source runs in its own `multiprocessing` process (avoids the GIL), with bounded queues (≤ 2 s of frames) feeding a writer thread. The scrcpy path stores packets with PyAV `mux` (no decode) and decodes only for preview at ≤ 2 fps (keyframes). Record3D RGB goes to JPEG with OpenCV (libjpeg-turbo, a few ms per frame). Depth is stored as uint16 mm, compressed with zlib per 1 s chunk.

### Session folder `data/multimodal/<YYYYmmdd-HHMMSS_label>/` (git-ignored)
```
session.json            sources, versions, configs, apps + licences, owner-only flag, status
events.jsonl            start/stop, clock pairs (mono<->wall every 10 s), device_sync copies, errors, disconnects
emg/                    the existing emgmirror-raw/1 recording (incl. ultrasonic if the firmware part adds it)
android_cam/  video.mkv (H.264 as sent)   frames.csv: seq,pts_us,t_host_mono_ns,t_host_wall_ns,keyframe,size,gap_before
iphone_rgbd/  rgb/000000.jpg.. or rgb_chunks/*.npz   depth/chunk_000123.npz (uint16 mm, conf u1, zlib)
              frames.csv: seq,relative_ts,unix_rx,t_host_mono_ns,t_host_wall_ns,fx,fy,tx,ty,qx,qy,qz,qw,px,py,pz,decimated
              intrinsics.json
sync/model.json         per-stream fits, L_s, jitter, grade (filled offline)
```
Every file is append-only, and an existing folder is never reused (same rule as `raw.py`).

### Collection check (`emgmirror collect check`, plus a panel on the existing :8030 viewer)
Refreshes once per second. Per source: state, nominal and measured fps, drops (seq/PTS gaps > 1.5 frame intervals), longest gap, arrival jitter p50/p95, current `t_arrival − t_host_est` (latency drift), age of the last frame, MB/min written, CPU % of its process, plus a thumbnail of the last frame (colour-mapped for depth). It also shows free disk space and the minutes left at the current rate. PASS criteria for a 2-minute check: each stream ≥ 95 % of nominal fps, no gap > 0.5 s, LED bursts detected on both cameras, EMG gate as already defined.

### Bandwidth, disk and RAM (estimates; C0 measures them)
| stream | rate | disk |
|---|---|---|
| EMG 1 ch + 2 ultrasonic | kB/s | < 1 MB/min |
| Android H.264 720p30 at ~4 Mbit/s | 0.5 MB/s | ~30 MB/min (1080p30 at 8 Mbit/s: ~60 MB/min) |
| Record3D RGB JPEG (~720x960, q85, ~80 kB) at 30 fps | ~2.4 MB/s | ~145 MB/min |
| Record3D depth uint16 + conf, zlib, 30 fps | ~1.5–2 MB/s | ~100 MB/min |
| **Total at 30 fps** | ~5 MB/s | **~280 MB/min, ~3 GB per 10 min** (double that for Record3D at 60 fps) |
RAM: bounded queues (≤ 2 s per stream, < 200 MB in total) plus the libraries, well under 7.6 GB. CPU: decoding is limited to the preview; JPEG plus zlib is about 0.3–0.5 of a core at 30 fps.

## 5. Order of work
1. C0 probe on hardware: scrcpy camera modes (`--list-camera-sizes`), Record3D USB on Windows with the chosen driver path, real fps, stream sizes, battery drain.
2. Cloud: `streams/` base, fakes, writers, collect check (all testable without hardware).
3. Laptop: LED latency calibration per phone and mode; then the 2-minute collection-check PASS with all five sources.

## Sources
- scrcpy camera: https://github.com/Genymobile/scrcpy/blob/master/doc/camera.md ; recording / device timestamps: https://github.com/Genymobile/scrcpy/blob/master/doc/recording.md ; protocol, 12-byte header, standalone server: https://github.com/Genymobile/scrcpy/blob/master/doc/develop.md
- Record3D library (LGPL-2.1, iTunes on Windows): https://github.com/marek-simonik/record3d ; PyPI 1.4.1 wheels: https://pypi.org/project/record3d/ ; Python bindings (`get_misc_data` etc.): https://github.com/marek-simonik/record3d/blob/master/python-bindings/src/PythonBindings.cpp ; per-frame timestamp in miscData (relativeTimestamp): https://github.com/marek-simonik/record3d/issues/91 ; timestamps relative to device start: https://github.com/marek-simonik/record3d/issues/75 ; features and paid parts: https://record3d.app/features ; App Store, prices, privacy label: https://apps.apple.com/us/app/id1477716895 ; Wi-Fi demo (WebRTC, HSV depth): https://github.com/marek-simonik/record3d-simple-wifi-streaming-demo
- Stray Scanner (MIT): https://github.com/strayrobots/scanner ; format: https://github.com/strayrobots/scanner/blob/main/docs/format.md ; App Store privacy: https://apps.apple.com/us/app/stray-scanner/id1557051662
- ARKit sceneDepth: https://developer.apple.com/documentation/arkit/arframe/scenedepth ; 256x192 at 60 Hz (forum): https://developer.apple.com/forums/thread/653539
- IP Webcam ad-supported: https://appgoblin.info/apps/com.pas.webcam ; RTSP path example: https://gist.github.com/rish-0-0/322e4b1d1367b45614e3151c5a822f36 ; Ivideon integration: https://iVideon.com/help/how-to-connect/equipment-to-ivideon-server/android-device/
- DroidCam: https://apps.apple.com/sg/app/droidcam/id1510258102 , https://sourceforge.net/app/droidcam-client/
- Larix: https://softvelum.com/larix ; MediaMTX (MIT): https://github.com/bluenviron/mediamtx
- PyAV (BSD-3, wheels bundle FFmpeg): https://pypi.org/project/av/