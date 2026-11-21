> **Reference design (draft, 2026-11-19).** Written before review. Where it disagrees with
> [MVP_MULTIMODAL_PLAN.md](MVP_MULTIMODAL_PLAN.md), the plan wins: see its sections 4 and 5 for the
> corrections (one protocol addendum, boot id, buffer sizes, verb-only upper-casing, PING with t_rx/t_tx,
> liveness watchdog, placement off the racket arm). Research use, not for diagnosis.

# Wireless EMG for emgmirror: design

Scope: replace the USB cable between the body-worn ESP32 and the laptop, so that the owner can play squash freely while 2-channel forearm sEMG is recorded at 2000 Hz, with the same integrity guarantees EMGM/2 gives over USB today.

## 0. Facts from the repo

| Item | Value | Source |
|---|---|---|
| Board | ESP32-D0WD-V3, 30-pin DevKit, ESP-WROOM-32 (no PSRAM), CH340 USB-serial, 4 MB flash | configs/esp32_candy.yaml, docs/HARDWARE_INVENTORY.md |
| Channels | CH1 = GPIO35 (header D35, **ADC1_CH7**), CH2 = GPIO36 (header VP, **ADC1_CH0**) | configs/esp32_candy_2ch.yaml |
| ADC | continuous DMA ADC1, 12 dB attenuation, 20 kHz conversion minimum; `os` is chosen by fs x os x nch >= 20 kHz | firmware/emg_esp32_v2/emg_esp32_v2.ino |
| Pin policy | firmware `PINS` accepts **only ADC1** GPIOs (32, 33, 34, 35, 36, 39) | firmware, protocol doc |
| Framing | COBS + 0x00 delimiter, type byte, CRC-16/CCITT-FALSE; DATA = 24-byte header + rows x nch x u16 | docs/ACQUISITION_PROTOCOL.md |
| Loss accounting | `gap = first_index - expected`; `lost_frames = seq gap`; `link = min(gap, lost_frames*n_rows)`; `device = gap - link`; a new run on an index restart or a new HELLO `run` | protocol doc, acquisition/esp32.py `_on_data` |
| Transport seam | `Esp32Source(transport_factory=...)` needs only a pyserial-like object: `read(n)`, `in_waiting`, `write(b)`, `close()`, and an OSError on loss, which triggers auto-reconnect | acquisition/esp32.py, registry.py (the emulator already uses this seam) |
| Raw format | samples.bin (index:u8 + raw u2[nch]); frames.csv (run, seq, first_index, n, t_device_us, t_host_ns, flags, gap_before, gap_cause); events.jsonl | recording/raw.py |
| Safety gate | PASSED WITH CONDITIONS for USB on a laptop running on battery, right forearm, 2 Candys; `check_before_person_recording()` blocks on AC or unknown power; `start_power_watch` aborts if AC appears | docs/ELECTRICAL_SAFETY.md, acquisition/safety.py, cli.py:220, dataset.py:342 |

**ADC2 / Wi-Fi conflict:** none for this rig. Both channels are on ADC1, and the firmware refuses non-ADC1 pins. What Wi-Fi does affect, even on ADC1, is the **supply**. Transmit bursts draw current peaks of about 180 to 240 mA (datasheet figures for 802.11b/n TX at high power) from the DevKit's AMS1117 3V3 regulator. The Candys are powered from the same 3V3 rail, and their output idles at about VCC/2. Rail ripple therefore appears at the ADC pin at roughly half its amplitude, as a comb at the transmit cadence and at the beacon interval (102.4 ms, so 9.77 Hz and its harmonics). This has to be measured (H3 below), not assumed.

## 1. Required data rate (2 ch, 2000 Hz)

- DATA payload = 24 + 10 rows x 2 ch x 2 B = **64 B**. Body = 1 type + 64 + 2 CRC = 67 B. On the wire = 67 + 1 COBS + 1 delimiter = **69 B**.
- 200 DATA packets/s gives **13.8 kB/s**. STATUS (once per second, about 150 B) and the occasional ACK, HELLO or LOG add about 0.2 kB/s. Total **about 14 kB/s = 112 kbit/s**.
- With TCP/IP and 802.11 overhead, sending every 16 ms (about 63 segments/s, 3 to 4 DATA packets each, about 76 B of headers per segment) gives **about 18 kB/s on air**.
- At 6 channels (future sensors): 24 + 120 = 144 B payload, about 30 kB/s. For comparison, raw samples alone are 2 x 2 x 2000 = 8 kB/s.

## 2. Options compared

| | (a) Wi-Fi, soft-AP, TCP *(recommended)* | (a') Wi-Fi, UDP | (b) BLE 4.2 (classic ESP32) | (c) On-board log (SD) | (d) Hybrid: SD log + Wi-Fi preview |
|---|---|---|---|---|---|
| Throughput margin at 14 kB/s | very large (ESP32 TCP reaches several Mbit/s; we need 0.11) | very large | small: classic ESP32 has no 2M PHY; Windows picks the connection interval (about 15 to 30 ms), so practical rates through bleak on Windows are about 10 to 40 kB/s, roughly 1x to 3x (estimate, unmeasured) | n/a (SPI SD sustains far more) | as (a) + (c) |
| Delivery | reliable, in order; losses only at connection resets; fades become latency, absorbed by the device queue | lossy, can reorder; needs a NAK/retransmit scheme and a reorder buffer for zero loss | reliable at the link layer until the supervision timeout drops the connection | complete, unless the card fails or power is cut (FAT corruption) | complete on SD, best-effort preview |
| Existing loss accounting | unchanged (+1 flag bit for queue drops) | works for counting (a seq gap = link loss); recovery needs new code | needs fragmentation over 244 B notifications plus a new host stack (bleak) | the same EMGM/2 bytes could be logged and replayed through the same decoder | both |
| Latency (preview) | RTT typically a few ms on a quiet channel; tail of about 100 ms to 1 to 3 s when TCP waits for a full retransmission timeout in a deep fade (queue covers it) | lowest, no head-of-line blocking | 15 to 30 ms connection interval plus queueing | none (no live view) | preview as (a) |
| Host complexity | tiny: a socket adapter behind the existing `transport_factory` | moderate to high (reorder, NAK) | high (bleak, Windows pairing quirks, MTU) | download tool + parser; no live view | highest |
| Firmware complexity | moderate (Wi-Fi AP, TCP server, transmit queue, NVS settings) | moderate + retransmit buffer | moderate to high (GATT, notify flow control) | moderate (SD over SPI, double buffer, file rotation) + extra wiring | highest |
| Extra hardware | none | none | none | microSD module + card, 6 more wires on the body-worn unit | as (c) |
| Current draw (5 V) | about 110 to 140 mA average with Wi-Fi power save off | same | about 40 to 100 mA | about 50 to 80 mA + SD write spikes | about 140 to 180 mA |
| Noise risk | TX bursts on the shared 3V3 (measurable, mitigable) | same | smaller bursts, still present | SD write spikes (100 mA class) on the same rail | both |
| Time sync to camera | live SYNC round trips; ±1 to 3 ms with a min-RTT filter | same | harder (connection-interval quantised) | needs optical/audio sync events only | both |

The 4 MB internal flash is ruled out for logging. With the default 1.5 MB SPIFFS partition (or about 2.5 MB with a minimal app partition) at about 8 to 14 kB/s, it holds about 2 to 3 minutes. A squash session needs 30 to 60 minutes, about 50 MB per hour, which only an SD card can hold.

**Decision:** (a) for the MVP. The data rate is tiny compared with Wi-Fi capacity. TCP keeps data in order, so the reference host stack, the raw recorder and the viewer run unchanged. The ESP32's own access point (soft-AP) works on a court that has no venue Wi-Fi, and it keeps all data on a local link between the device and the laptop (nothing leaves the laptop). The device queue turns body-shadowing fades into latency instead of loss. (d) is phase 2, triggered only by field data: losses above 0.1% or outages longer than about 5 s in H5.

## 3. Firmware changes: emg_esp32_v2 2.2.0 (same sketch; serial stays the default)

Keep one sketch and one protocol. The link is selected at run time from NVS, so the USB path, which is verified on hardware, does not regress.

1. **Move the testable core into `firmware/emg_esp32_v2/emgm_core.h`.** Plain C++ with no Arduino includes: `crc16`, `cobs_encode`, and a `PacketQueue` byte ring that holds whole encoded packets with a packet-boundary index. `send_packet()` encodes into the queue instead of calling `Serial.write`.
2. **Transmit queue.** A static or `malloc` buffer, `TXQ_BYTES` default 98304, about 7 s at 2 ch (classic ESP32 with Wi-Fi up has about 150 to 200 kB free heap; the largest block is usually above 100 kB, so it is checked at boot and reported). A drain function writes queued packets to the active link: `Serial` (as today) or the TCP client, with non-blocking writes. Partial writes are tracked, so a packet is never split across reconnects.
3. **Queue overflow keeps the accounting exact.** In `emit_frame`, if the encoded frame does not fit:
   - advance `sample_index` by `ROWS_PER_FRAME`, but **not** `frame_seq`, because frame_seq keeps meaning "DATA packets the device emitted";
   - set `pending_drop` and a new `pending_txq_drop`;
   - increment `txq_drop_frames`.

   The next queued frame carries `AFTER_DROP` plus the new **flag bit3 `TXQ_DROP`**. The host already classes an index jump without a seq gap as a device loss; bit3 refines it to `gap_cause=device_txq` (DMA overflow stays `device_dma`).
4. **Wi-Fi link, AP mode (default).**
   - SSID `emgm-<last 4 hex of MAC>`, WPA2 passphrase **set over USB once with `WIFI AP <pass>` and stored in NVS**. It is never in the .ino, the repo or a config file, because the repo is public.
   - Fixed channel (`WIFI CH <1|6|11>`); `WiFi.setSleep(false)` for steady timing and to stop power banks from cutting out at low current.
   - `TXPOWER <dBm>` (default 11 dBm).
   - TCP server on port 3333 with one client; a new client replaces the old one. On accept: `setNoDelay(true)`, send HELLO, then continue draining the queue from the next packet boundary.
   - `WIFI STA <ssid> <pass>` exists as an option (laptop hotspot), but AP is the reference.
5. **Send cadence.** The sender task (pinned to core 0 with the Wi-Fi stack; the ADC pump stays in `loop()` on core 1) drains every `TXINT` ms, default **16 ms**. 62.5 Hz is deliberately not a 50 Hz mains harmonic, so radio ripple and mains hum can be told apart in the spectrum. The rate is set with `TXINT <ms>` (5 to 50).
6. **Commands** come from Serial **and** from the TCP client, through the same `handle_command`. New commands:
   - `SYNC <host_ns>`: ACK `cmd=SYNC ok=1 host_ns=<echo> t_us=<esp_timer_get_time()>`
   - `LINK SERIAL|WIFI` (stored in NVS, restarts the link, not sampling), `WIFI ...`, `TXPOWER`, `TXINT`
   - `RESUME <seq>` (milestone 2). Delivered packets stay in the queue until the host confirms them with `ACKSEQ <seq>` every 0.5 s. After a reconnect the host sends `RESUME <last_seq+1>`, and the device rewinds to that packet if it is still queued. If it is gone, the seq gap is counted as a link loss, exactly as today.
   - `BLINK <n> <period_ms>`: drives the DevKit LED on GPIO2 and sends an **EVENT packet (new type 0x06)** with the `t_us` of each LED edge, for direct device-to-video alignment with a 120 to 240 fps phone. This is a hook for the sync design. GPIO2 is an ADC2 pin, which is unused here, so driving it digitally is fine.
7. **HELLO adds** `link=serial|wifi wifi_mode=ap ip=192.168.4.1 port=3333 ch=6 tx_dbm=11 txint_ms=16 txq_cap=98304`.
   **STATUS adds** `link rssi txq_used txq_peak txq_drop_frames tcp_clients wifi_reconnects heap_min`. The old `tx_free` is kept and means queue free space.
8. **Boot behaviour.** If `link=wifi`, the AP comes up at boot and sampling starts, so a power-bank-only unit streams with no laptop action. The USB serial still prints HELLO, which keeps reflashing and recovery easy.

The protocol doc gets a "Transports" section (serial and TCP stream: the same byte stream, the same framing), flag bit3, the EVENT type, the new commands, and new HELLO/STATUS keys. `proto` stays `EMGM/2`: every change is an addition, and old hosts ignore unknown keys, flag bits and types.

## 4. Host changes

- **`acquisition/net.py` (new):** `TcpTransport(host, port, timeout=0.1)`, a pyserial-like adapter over `socket`.
  - `read(n)` returns b"" on timeout and raises `ConnectionError` (an OSError) on EOF or reset; `in_waiting` comes from a non-blocking peek or a small internal buffer; `write`; `close`.
  - It sets `TCP_NODELAY` and a 1 MB `SO_RCVBUF` (the same reasoning as `open_serial`'s 1 MB buffer).
  - `connect` has a timeout and a clear error naming the AP SSID that the laptop has to join.
- **`registry.py`:**
  - `wifi[:<host>[:<port>]]` builds `Esp32Source(port=f"tcp://{host}:{port}", transport_factory=lambda: TcpTransport(...), handshake_timeout_s=15, reconnect_timeout_s=cfg wifi.reconnect_timeout_s (default 120))` with `info.kind="esp32-wifi"`, `device={"transport": "tcp", "host":..., "port":...}`.
  - A bare `wifi` takes the host and port from config.
  - `emulator-wifi[:modes]` runs the emulator behind a localhost TCP server (see section 6).
- **`esp32.py` (small, transport-agnostic):**
  1. Kind and label come from the caller.
  2. **Duplicate guard:** a DATA frame in the same device run with `seq <= last_seq` (within 2^31) is dropped and counted (`dup_frames`). Without this, a RESUME replay would hit `first_index < expected` and be misread as a new run.
  3. `gap_cause` refinement from flag bit3 (`device_txq`) and bit2 (`device_dma`).
  4. A **SYNC loop** every `sync_period_s` (1 s) when `link=wifi`, and optionally on serial too. Each round trip goes to events.jsonl as `{"kind": "clock_sync", "host_send_ns", "host_recv_ns", "t_device_us", "rtt_us"}`.
  5. STATUS keys (`rssi`, `txq_used`, `txq_drop_frames`) are exposed through `status()` for the monitor and the viewer's diagnostics.
- **`recording/derive.py` or a new `recording/clock.py`:** `device_to_host(t_device_us)` fits offset plus drift to the **lower-RTT quantile** of the clock_sync events (keeping RTT below the 20th percentile and using midpoint pairing), with a residual report (expected ±1 to 3 ms over Wi-Fi). This replaces regressions on per-chunk `t_host_ns` for Wi-Fi recordings. Those values are still written, but on TCP with a queue they are arrival times, not acquisition times. The camera alignment layer consumes `device_to_host`, or BLINK events directly.
- **Raw format:** unchanged files; `FORMAT` stays `emgmirror-raw/1`. New items: manifest `source.device.transport`, `isolation` (see section 5), and new event kinds `clock_sync`, `device_event` (BLINK edges), `link_status`.
- **Config keys** (`configs/esp32_candy_2ch_wifi.yaml`, `base: configs/esp32_candy_2ch.yaml`):

```yaml
acquisition:
  link: wifi                 # serial | wifi
  wifi:
    host: 192.168.4.1        # soft-AP address of the ESP32
    port: 3333
    mode: ap                 # ap | sta
    ssid: emgm-7070          # from the MAC; the passphrase lives only in NVS and the OS Wi-Fi profile
    channel: 6
    tx_power_dbm: 11
    tx_interval_ms: 16
    txq_bytes: 98304
    handshake_timeout_s: 15
    reconnect_timeout_s: 120
    sync_period_s: 1.0
hardware:
  power: power_bank          # usb_laptop | power_bank
  usb_vid_pid: "1A86:7523"   # used by the wireless isolation check
  wearable: { unit: upper_arm_pouch, candys: forearm, notes: "" }
```

- **CLI:**
  - `emgmirror acq probe --source wifi` and `emgmirror record --source wifi` (existing commands; nothing else changes);
  - `emgmirror acq wifi-setup --port COMx --pass-env EMGMIRROR_WIFI_PASS --channel 6`, which sends `WIFI AP`, `TXPOWER`, `TXINT` and `LINK WIFI` over USB once. The passphrase is read from an environment variable and never echoed or logged.

## 5. Electrical safety: what the gate and the code should require

On a power bank with no cable to anything, the body-worn unit (electrodes, Candys, ESP32, bank) is a floating, battery-only system. No galvanic path to mains exists, so the laptop's AC state no longer matters to the person. The new hazards are mechanical and chemical (a lithium bank on the body during a contact sport, sweat). There is also one easy mistake that brings the mains path back: the USB cable left plugged into the laptop, or the power bank charging from a wall adapter (pass-through) while worn.

ELECTRICAL_SAFETY.md gets a **separate configuration, "6c: wireless, battery-only"**, with its own status line that the owner passes explicitly. The current pass covers the USB configuration only.

1. The body-worn unit is powered **only** by a power bank (or an AA/AAA pack). **No cable** may connect the unit to anything else during a session. The bank must not be charging and must not be in pass-through mode from a charger. No other module on the unit has its own supply.
2. Use a commercial power bank with protection circuitry, in a padded pouch on the upper arm or waist, never against the racket-side wrist. No bare Li-ion or LiPo cells and no DIY packs. Never charge it while worn. Stop and remove it if it is dented, swollen or hot.
3. Sweat: Candys and ESP32 are sealed (heat-sealed film or a zip bag). The USB connector of the bank stays outside the sweat path, or the bag goes over the plug as well.
4. Radio: TX power is at most 11 dBm by default (about 13 mW; a phone transmits up to about 200 mW), and the antenna is not pressed against the skin.
5. Unchanged: items 1 to 5, 7, 8 and 9 (front-end identity, protection, supply, output range, single ground, site clearance, stop rule). Only the cleared electrode site; every other site stays blocked.

**Software (`safety.py`):** `check_before_person_recording(isolation)`, where `isolation` comes from the source: `"usb_battery_laptop"` for `esp32`/`text`, `"wireless_battery"` for `esp32-wifi`.

- `usb_battery_laptop`: as today (block on AC or unknown).
- `wireless_battery`: **block if any USB device with `hardware.usb_vid_pid` (CH340 1A86:7523) is enumerated** (from `serial.tools.list_ports`). That would mean the board's USB is cabled to the laptop, which is the main way the mains path comes back. The block message says to unplug the cable, or to use `esp32:<port>` with the USB rules instead. This is deliberately conservative: another CH340 gadget also blocks, and the message says so. There is no override.
  - The laptop AC state is recorded but not blocking.
  - The device MAC from HELLO must equal `hardware.board.mac`.
  - A one-line attestation is required at start (`--attest-battery-only`, or an interactive y/N): "the unit runs on a power bank that is not charging and has no other cable". The attestation and all check results go into the manifest (`isolation`, `checks`).
- `start_power_watch` becomes `start_isolation_watch`: for wireless recordings it aborts if the CH340 appears mid-recording; for USB recordings it keeps the AC watch.
- Software cannot see whether the bank is charging, so that condition stays in the doc plus the attestation. The doc says this plainly.

## 6. Cloud testing before flashing (no hardware)

1. **`acquisition/emulator.py` additions** (the existing faults are kept):
   - `txq_bytes`: an emulated device queue between the frame generator and the link, with the firmware's overflow semantics (index advances, seq does not, bit3 + bit2 flags).
   - `link_outages=[(t_start_s, dur_s)]`: the emulated link accepts no bytes (the queue fills).
   - `reset_at_s=[...]`: the TCP connection drops mid-packet, and the bytes that lwIP "sent" but never delivered are lost (configurable 0 to 4 kB).
   - `latency_ms`, `jitter_ms`, `tx_interval_ms`: bursty delivery.
   - `rssi_profile`: reported in STATUS.
   - a `radio_ripple_mv` signal option: adds a comb at 1/tx_interval and at 9.77 Hz plus harmonics to the "emg" mode, so the diagnostics and viewer can be tested to flag radio artefacts separately from mains.
   - `SYNC`/`RESUME`/`ACKSEQ`/`BLINK` command handling with the device clock (`clock_ppm`, `t_jitter_us` already exist).
2. **`tests/net_emulator.py` (fixture):** a threaded localhost TCP server wrapping `Esp32Emulator` with the deterministic clock, so tests exercise the real `TcpTransport` socket code.
3. **Tests** (`tests/test_wifi_link.py`, deterministic clock, seconds of wall time):
   - a clean 60 s stream: 0 lost, rate 2000, one run;
   - a 2 s outage with a 7 s queue: **0 lost**, latency peak reported, `txq_peak` about 28 kB;
   - a 10 s outage: device drops exactly (10 - 7.1) s x 2000 samples, ±1 frame, `gap_cause=device_txq`, no link loss;
   - a reset mid-packet without RESUME: exact `link_lost_frames`, decoder resync with no CRC false-positives, auto-reconnect, the same run;
   - the same reset with RESUME (milestone 2): 0 lost and `dup_frames` counted, not a new run;
   - device reboot over Wi-Fi: a new run;
   - SYNC with 0 to 30 ms jitter and clock_ppm=50: offset error below 1 ms, drift recovered within 2 ppm;
   - safety, with mocked `list_ports` and power: wireless + CH340 present blocks; wireless + AC + no CH340 + attestation passes; USB + AC blocks as before; the manifest contains `isolation`;
   - recorder + viewer end to end on `emulator-wifi`: raw recording integrity, `clock_sync` events present;
   - a regression run of the whole existing suite (serial path unchanged).
4. **Firmware core in the cloud:** `tests/firmware/test_core.cpp` (g++, run by pytest when g++ exists) for COBS, CRC (check value 0x29B1), queue wrap, overflow-flag semantics and RESUME rewind. Byte-level vectors are shared with `tests/test_emgm2_protocol.py`. Optional: an `arduino-cli compile --fqbn esp32:esp32:esp32` CI step, if the cloud can fetch the ESP32 core (compile only, never flash).

## 7. Hardware tests (laptop, after `git pull`; blocked until `acq scan` shows GPIO35/36 driven)

- **H1, USB regression:** flash 2.2.0 over USB (back up the flash first), `tests/physical` 4/4, a 5 min serial recording with 0 missing. Then `acq wifi-setup`.
- **H2, bench Wi-Fi, no body:** ESP32 on a power bank, laptop joined to `emgm-7070`, 10 min `record --source wifi` at 3 m and then 10 m. Pass: 0 lost, device rate 2000.00, `txq_peak` below 10% at 3 m, SYNC residual below 3 ms.
- **H3, radio noise A/B, no body.** Inputs of each Candy shorted together (IN+, IN−, REF tied) or a bench voltage, rig in its box. Compare PSDs and rest RMS for:
  - serial, Wi-Fi off;
  - Wi-Fi at 11 dBm;
  - Wi-Fi at 19.5 dBm;
  - Wi-Fi with an RC filter on the Candy VCC (100 Ω + 100 µF from 3V3) and 100 µF on the ESP32 3V3.

  Pass: the 20 to 450 Hz floor is within 1 dB of serial, with no 62.5 Hz or 9.77 Hz comb above the room floor (12.3 / 18.4 mV pin RMS). If it fails: lower TX power, then the RC filter, then a separate LDO for the Candys (shared GND).
- **H4, body-worn walk and swing test**, after the owner passes gate 6c. Electrodes on the right forearm, unit in an upper-arm pouch, laptop at the court back-wall position:
  - walk all corners and turn the body away (shadowing);
  - shadow swings without a ball;
  - record losses, `rssi`, `txq_peak` and motion artefact (envelope below 20 Hz).
- **H5, court session:** 20 to 30 min of play. This decides on phase 2 (SD hybrid) if losses exceed 0.1% or any outage exceeds the queue.

## 8. Power, mounting, motion artefact (frugal)

- **Power:** ESP32 with Wi-Fi (no power save) plus 2 Candys draws about 120 to 140 mA at 5 V, about 0.7 W. A 5000 mAh bank (18.5 Wh) gives roughly 15 to 20 h after conversion losses. Any phone power bank the owner has works. Many banks switch off below about 50 to 100 mA. The ~130 mA load with `setSleep(false)` is above that, and the H2 run confirms the bank stays on for 30 min. Use a short USB cable, about 20 cm.
- **Placement:** Candys on the forearm next to the electrodes. Their short high-impedance electrode leads are where motion artefact and pickup arise. After the Candy, the output is low impedance and far less sensitive. From the Candys, a 4-wire lead (3V3, GND, OUT1, OUT2), twisted and taped, runs up to the ESP32 and power bank in an upper-arm pouch: an old phone armband, or a cut sock with velcro or rubber bands. The pouch goes on the outer side of the arm with the PCB antenna facing out. Total about 150 g.
- **Strain relief:** a slack loop at the elbow, taped above and below so that swing forces pull on tape, not on the connectors. Cover the forearm with a tube bandage or a sleeve cut from a sock to hold leads and Candys.
- **Electrodes and sweat:** fresh gel pads every session; skin prep; tape over pad edges. Candys stay heat-sealed. A 20 Hz high-pass already in the processing handles most motion artefact. The impact of ball contact sends a mechanical vibration through the racket and hand into the forearm; mark it as a possible artefact window in the per-stroke analysis.
- **Mechanical safety:** no hard edges on the inner arm; the bank in padding; take it off if a wall collision is likely (competition drills).

## 9. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Radio ripple on the shared 3V3 | a comb in the EMG band, raised floor | H3 A/B; TX power 11 dBm; 16 ms cadence; RC filter on Candy VCC; separate LDO if needed |
| Body shadowing / glass wall | fades of 0.1 to 3 s | 7 s queue; fixed clean channel; antenna facing out; RESUME; phase-2 SD hybrid if H5 fails |
| Heap too small for the 96 kB queue | boot failure / smaller queue | the firmware reports `txq_cap`; fall back to 64 kB (about 4.6 s); an ESP32-S3 with PSRAM (about ₹500 to 700) as a later upgrade |
| TCP retransmission timeout stalls (lwIP RTO about 1 to 3 s) | preview latency spikes | the queue absorbs them; recording integrity unaffected; preview shows latency |
| Clock mapping over Wi-Fi | sync error to camera | SYNC min-RTT fit (±1 to 3 ms); BLINK LED events for direct video alignment |
| Mains path re-created (USB left in, bank charging) | electrical safety | gate 6c conditions, CH340 interlock, attestation, isolation watch |
| Lithium bank on the body in a contact sport | fire or injury | commercial protected bank, padded pouch, never charge while worn |
| Passphrase leakage (public repo) | someone else joins the AP | the passphrase only in NVS and the laptop's Wi-Fi profile; config holds an env var name only |
| Rig not producing EMG yet (11-19 scan: pins floating) | hardware milestones blocked | do the cloud work now; H1+ only after the scan shows mid-rail drive |

## 10. Cost

MVP: ₹0 if the owner has a power bank and a short USB cable. Otherwise about ₹500 to 800 (5000 to 10000 mAh bank) and ₹50 to 100 (cable). RC filter parts about ₹10. Pouch, sock, tape and zip bag from the household. Phase 2 (SD hybrid): a microSD SPI module about ₹80 to 150, plus a spare microSD card. Optional upgrade: an ESP32-S3 with 8 MB PSRAM about ₹500 to 700 (minutes of on-board buffer, BLE 5 2M PHY). Cloud development uses only existing tooling.
