> **Reference design (draft, 2026-11-20)** for the data-collection MVP. Written before review; where it
> disagrees with [COLLECTION_MVP_PLAN.md](COLLECTION_MVP_PLAN.md), the plan wins (it is cut down, adds the
> sync LED on GPIO21, a monotonic host clock column, and the safety-gate amendment). Research use, not for
> diagnosis.

# ESP32 firmware and wiring: 1 EMG channel + 2 HC-SR04 ultrasonic sensors (MVP data collection)

Scope: this is my part only (ESP32 side, the protocol and the host plumbing for the ultrasonic data). Phones and cameras belong to other parts. The repo was read only, nothing was changed.

Files read: `firmware/emg_esp32_v2/emg_esp32_v2.ino` (2.1.0), `docs/ACQUISITION_PROTOCOL.md`, `src/emgmirror/acquisition/{emgm2,esp32,emulator,base,pipeline,registry}.py`, `src/emgmirror/recording/raw.py`, `configs/esp32_candy*.yaml`, `docs/HARDWARE_INVENTORY.md`, `docs/ELECTRICAL_SAFETY.md`, `docs/mvp/*`.

Toolchain on this laptop: Arduino-ESP32 core **3.3.12**, which is ESP-IDF 5.5.x (`CONFIG_IDF_INIT_VERSION "5.5.5"` in the installed sdkconfig). It ships `esp_driver_mcpwm/include/driver/mcpwm_cap.h` and has `CONFIG_MCPWM_ISR_HANDLER_IN_IRAM=1`. It has no esp_timer ISR dispatch, so callbacks run in the esp_timer task, which is pinned to CPU0. Arduino `loop()` runs on CPU1 (`CONFIG_ARDUINO_RUNNING_CORE 1`).

---

## 0. Findings in the current code that this work depends on

1. **The 1-channel config points at the wrong pin.** `configs/esp32_candy_1ch.yaml` uses GPIO34 (state on 2026-11-16). The wiring on 2026-11-17 (`esp32_candy_2ch.yaml`, inventory) is D35 = GPIO35 and VP = GPIO36, and GPIO34 is no longer wired. The owner's decision names GPIO35, so the 1-channel config must change (see §5).
2. **Unknown packet types are counted as corruption.** `emgm2.decode_body` raises `ValueError("unknown packet type")`, and `StreamDecoder.feed` counts it in `frame_errors`. `tests/physical/test_esp32_hardware.py` asserts `frame_errors == 0`. Any new packet type therefore needs a decoder change and an opt-in on the device (§3).
3. **The firmware's buffers are too small for a longer HELLO.** `body[320]`, `wire[330]` and `char t[320]` in `send_hello()` limit HELLO to 320 bytes. Today's HELLO is about 250 characters, and adding about 170 characters of `us_*` keys goes past the limit. `send_packet` then returns **without sending anything and without any error**. The same applies to STATUS (`t[220]`). Raise to `body[600]`, `wire[610]`, HELLO `t[560]`, STATUS `t[360]`.
4. **Frame accounting already supports index stamping.** `isr_done` counts every conversion frame, including discarded ones, and resets at `adc_start()`. `ts_ring[k]` holds the esp_timer time at the end of frame k. Frame k covers sample indices `[k*R, (k+1)*R-1]` with R=10. So any esp_timer instant can be converted to an EMG sample index on the device with no new ADC code.
5. **1 channel at 2 kHz** gives `pick_os` → os=10, so the conversion rate is 2000×10×1 = 20 kHz, exactly the ESP32 minimum. Below 1250 Hz one channel does not sample (protocol doc). Keep fs=2000.

---

## 1. Wiring

### 1.1 Sensor choice and voltage
- **HC-SR04 (classic):** "Working Voltage DC 5 V, Working Current 15mA", 2 cm to 4 m, 10 µs TTL trigger. The datasheet suggests a measurement cycle over 60 ms. Distance = µs/58 cm (Elecfreaks datasheet: https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf). The ECHO output is about 5 V, and the ESP32's GPIOs are **not 5 V tolerant**.
- **RCWL-1601 / HC-SR04P / SparkFun 3.3 V HC-SR04:** run on 3 to 5.5 V and are pin-compatible (https://core-electronics.com.au/ultrasonic-distance-sensor-3v-or-5v-hc-sr04-compatible-rcwl-1601.html, https://www.sparkfun.com/ultrasonic-distance-sensor-3-3v-hc-sr04.html). Vendor listings disagree on whether the HC-SR04P echo follows VCC or is always 5 V (Banggood listing). **Measure the echo level before connecting it straight to a GPIO.**
- **Recommendation (frugal, and also the lower-noise option):** whichever module is used, **power it from 5 V (the DevKit VIN pin, fed from USB) and put a divider on ECHO.** Powering a 3.3 V variant from the ESP32 3V3 pin would put the transducer-drive current spikes on the **same rail that feeds the Candy**, which is what we most want to avoid. On 5 V the spikes stay on the USB 5 V net, and the 3V3 regulator sits between them and the Candy.

### 1.2 Pins (30-pin DevKit, WROOM-32)

| Signal | GPIO | Header | Why |
|---|---|---|---|
| EMG CH1 (Candy OUT) | **35** | D35 | unchanged, ADC1_CH7, input-only |
| US1 TRIG | **23** | D23 | output, not a strapping pin, not ADC1, far header row |
| US1 ECHO (via divider) | **22** | D22 | input, routed to MCPWM capture through the GPIO matrix |
| US2 TRIG | **19** | D19 | as above |
| US2 ECHO (via divider) | **18** | D18 | as above |
| spare | 16, 17, 21 | RX2, TX2, D21 | free on WROOM (no PSRAM) |
| **reserved** | 25 | D25 | sync LED in `docs/mvp/design_sync_camera_analysis.md` |

Pins to avoid:
- Strapping pins **0, 2, 5, 12, 15** (and 4, which esptool lists among the strap bits). GPIO12 (MTDI) pulled high at boot switches the flash to 1.8 V (https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html).
- 6 to 11 (flash) and 1/3 (UART0, the EMGM/2 link).
- All ADC1 pins (32 to 39): keep them for EMG, and never drive a digital edge next to the analog inputs.
- 13, 14, 26, 27: they sit on the same header row as D35/VP, next to the EMG wire.

Set the TRIG pins as outputs, low, first thing in `setup()`.

### 1.3 ECHO divider
ECHO → **1 kΩ** → GPIO, and GPIO → **2 kΩ** → GND. At 5.0 V this gives 3.33 V, at 4.6 V (VIN after the DevKit's diode) 3.07 V. Both are well above VIH = 0.75×3.3 = 2.48 V, and the load on ECHO is only about 1.7 mA.
- Any resistor pair with a ratio R2/(R1+R2) between **0.62 and 0.68** works, for example 10k/20k or 4.7k/10k.
- Do **not** go below about 0.6, or a 4.6 V ECHO becomes marginal.
- The divider also holds the line low when the sensor is unplugged, so disable the internal pull-up (the IDF example enables one; we must not).
- TRIG is driven at 3.3 V. This is widely reported to work with the HC-SR04's TTL input. The bench test checks it: the share of `OK` readings must be about 100% on a fixed target.

### 1.4 Power budget (USB 2.0 port: 500 mA)
- ESP32 with the radio off: about 50 to 80 mA. Candy: a few mA. 2× HC-SR04 at 15 mA each, plus short burst peaks. Total under about 150 mA, so there is plenty of margin.
- **Measure VIN** with USB connected. DevKits have a diode or fuse between VBUS and VIN, so expect about 4.6 to 5.0 V.

### 1.5 Noise coupling into the Candy (band 72–720 Hz, gain 2420, Upside Down Labs docs: https://certification.oshwa.org/in000042.html, https://robu.in/product/muscle-bioamp-candy)

Mechanisms:
- **(a) Supply current pulses at the ping rate.** Pings run at 40 per second in total (two sensors × 20 Hz). A periodic disturbance at 40 Hz has harmonics at 80, 120, … Hz, inside the EMG band. This is the main risk.
- **(b) The 40 kHz burst itself.** It lasts 8 cycles = 200 µs. With 20 kHz conversions and a 10-sample sum, 40 kHz aliases to **DC**. Any burst that couples in therefore shows up as a short step or impulse once per ping, not as a tone.
- **(c) Acoustic pickup** by the electrodes: none at 40 kHz.

Mitigations, cheapest first:
1. Sensors on VIN (5 V), Candy on 3V3, so the regulator sits in between.
2. **Star ground:** each sensor's GND wire goes back on its own to an ESP32 GND pin. It must never share the Candy's GND or electrode-lead wiring.
3. At each sensor, put **10–47 µF + 100 nF** across VCC/GND. Do not use 100 µF or more. The board is in a fixed **boot-loop** state (memory note: after the foil lining), and a large capacitor on VIN adds plug-in inrush. Check that the board boots 5 times in a row after adding the sensors.
4. Twist each sensor's VCC with its GND, and TRIG/ECHO with a second GND if a spare wire exists. Route these cables away from the Candy OUT wire and the electrode leads. Bring them into the foil-lined box at the side opposite the Candy, and check with a multimeter that no sensor pin touches the foil.
5. **The artefact is deterministic and every trigger is logged with its EMG sample index.** Analysis can mask ±2 ms around each trigger, or subtract a template, if the bench test shows an artefact.
6. Firmware command `US OFF` lets the same rest-floor recording be taken with and without pinging (§6 B3), so the artefact is measured, not guessed.

### 1.6 Electrical safety: what changes
- **Shared ground.** The sensors are not on the skin, but their GND and metal transducer cans are joined to the **same ground as the electrode circuit**, through the ESP32. Every exposed conductive part of the sensors is therefore part of the "body circuit":
  - mount the sensors so the person cannot touch them (not on the forearm, not where a hand rests)
  - cover exposed header pins with tape or heat-shrink
  - treat the transmitter can as live, since its drive circuit can swing well beyond 5 V on some modules
- **Gate item 7** ("no other module powered from a separate supply") **stays satisfied** only if the sensors take power **only from the ESP32's USB 5 V**. They must never have their own power supply or wall adapter.
- **Gate item 6** (battery only, **only the ESP32's USB cable** connected) is unchanged, and so is the `record` AC interlock. **Cross-part warning:** if the phones are wired to the laptop by USB for streaming, that breaks item 6 as written. A phone on a charger is also a mains path. Recommend phones on local Wi-Fi and off their chargers during electrode sessions, or an explicit gate amendment by the owner.
- The gate "covers only this configuration". Adding modules is a new configuration, so **the owner has to record a decision** in `docs/ELECTRICAL_SAFETY.md`, for example: "1 ch GPIO35 + 2× HC-SR04 on VIN, battery only, sensors not touchable". Software cannot do this, so §5 adds a config key that the software checks.

---

## 2. Timing

### 2.1 Schedule (alternating, cross-talk-safe)
- One **periodic esp_timer** with a **25 ms slot** (`us_slot_ms`, settable from 20 to 60). Slots alternate: US1, US2, US1, …
- Each sensor fires every **50 ms (20 Hz)**, 40 readings per second in total.
- **Echo window (software timeout) = 11 662 µs**, which is 2.0 m at 343 m/s (`us_max_mm=2000`, settable). Edges arriving later are ignored. Minimum valid echo is 116 µs (2 cm).
- **Why 25 ms:**
  - The other sensor fires only after the first sensor's window has closed plus 13 ms of decay. An echo would need a path longer than about 8.6 m to reach the other sensor's window, so cross-talk can only come from long reverberation.
  - Each sensor's own period (50 ms) is longer than the roughly 36 ms a genuine HC-SR04 holds ECHO high when no echo returns (forum quote of the datasheet timeout: https://forum.arduino.cc/t/ultrasonic-distance-sensor-hc-sr04-lack-of-timeout-problem/55161).
  - It is slightly below the datasheet's suggested 60 ms cycle. If the bench shows ghost readings (§6 B2), use `USSLOT 30` (16.7 Hz each), which meets the datasheet.
- **40 Hz per sensor is not reachable** with alternating HC-SR04s. Each sensor would get a 25 ms period, shorter than its own no-echo hold time. Firing both at once would cause cross-talk. Higher rates need different sensors.

### 2.2 Echo measurement without blocking the ADC loop
Options considered:
- `pulseIn()` blocks for up to 36 ms per call. **Rejected**: it would stall `pump_adc()` and the serial link.
- GPIO ISR on both edges + `esp_timer_get_time()`: simple, but the timestamps take on ISR latency jitter (ADC conv-done ISR, flash-cache stalls). At 0.17 mm per µs, tens of µs of jitter means several mm. Kept as a **fallback build option** (`US_TIMER_GPIO_ISR`).
- RMT RX: possible (a 32 ms pulse fits in 15-bit ticks at 1 µs), but it is more complex, buys nothing over MCPWM, and RMT is a likely future LED or sync user.
- **MCPWM capture: chosen.** Espressif's official example `examples/peripherals/mcpwm/mcpwm_capture_hc_sr04` (https://github.com/espressif/esp-idf/tree/master/examples/peripherals/mcpwm/mcpwm_capture_hc_sr04) does exactly this:
  - it captures both edges, and the hardware latches the counter, so ISR latency does not matter
  - tick count = falling − rising
  - on ESP32 the capture timer always runs at the APB clock, 80 MHz (https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/mcpwm.html)
  - the ESP32 has 2 MCPWM groups with 3 capture channels each, so group 0 channels 0 and 1 cover our two sensors

  Unlike the example, we do **not** enable the internal pull-up, because the divider pulls down.

### 2.3 Concurrency
- **Capture ISR** (IRAM_ATTR, ISR on the core that allocated it, CPU1): on a positive edge, store `rise_ticks[s]`. On a negative edge, store `fall_ticks[s]` and set `edge_done[s]=1`. Nothing else happens in the ISR, and it never calls `send_packet`.
- **esp_timer callback**, the single producer (CPU0, every slot):
  1. **Finalise** the sensor fired in the previous slot. Its window closed 13 ms ago. Read its edges and classify the status (below), then push one `Reading` into a 64-entry ring guarded by a `portMUX` spinlock.
  2. **Fire** the next sensor: clear its edge state, `t_trig_us = esp_timer_get_time()`, TRIG high, `esp_rom_delay_us(12)`, TRIG low.
  3. Compute `emg_index` for `t_trig_us` (§2.4).
- **`loop()`**, the single consumer (CPU1): after `pump_adc()`, take up to 8 readings per pass from the ring and send them with `send_packet(PKT_RANGE, …)`. Ring overflow (loop stalled more than 1.6 s) increments `us_dropped` and is reported (§3.3).

### 2.4 Stamping onto the EMG timeline
In the timer callback, as a seqlock-style read:

```c
uint32_t k1 = isr_done; int64_t ts = ts_ring[(k1 - 1) % TS_RING]; uint32_t k2 = isr_done;
if (k1 != k2) retry once;
emg_index = running && k1 ? (uint32_t)(k1 * ROWS_PER_FRAME - 1 + llround((t_trig_us - ts) * rate_hz / 1e6))
                          : 0xFFFFFFFF;
emg_run = run_id;
```

- This is the sample index (same space as DATA `first_index`, 32-bit, same run) of the EMG sample nearest the trigger. Accuracy is about ±1 sample (0.5 ms), against a 50 ms reading period.
- The device clock `t_trig_us` is sent as well, so the host can **also** interpolate the index from `frames.csv` (`t_device_us` per frame). The two values are cross-checked (§4.4).
- **Which instant a reading refers to:** the sound leaves at about `t_trig_us` + a few hundred µs, and the reflection happens at `t_trig + echo_us/2`. The CSV stores `t_trig_us` and `echo_us`, so analysis can use `t_trig_us + echo_us/2` if it wants the time of the reflection.

### 2.5 Status per reading (u8)

| code | name | rule |
|---|---|---|
| 0 | OK | rising and falling edges seen, 116 µs ≤ width ≤ timeout |
| 1 | NO_ECHO | no rising edge within the window (sensor did not answer: wiring, power, or TRIG level) |
| 2 | OUT_OF_RANGE | rising edge seen, no falling edge before the timeout (target beyond `us_max_mm`, or the module's no-target hold) |
| 3 | TOO_CLOSE | width < 116 µs |
| 4 | STUCK | ECHO is still high at the next trigger of this sensor (50 ms later). Known clone bug: ECHO stays high indefinitely when no echo returns (https://forum.sparkfun.com/viewtopic.php?p=180183, https://bitbucket.org/teckel12/arduino-new-ping/issues/3/hc-sr04-sensor-get-stuck-at-zero). **Recovery:** switch the ECHO pin to output low for 1 ms (current is limited by the 1 kΩ, so the module output is not shorted hard), return it to the capture input, then skip one trigger. Count `us_stuck`. If the sensor is stuck 5 times in a row, report it once with `LOG level=warning msg=us_stuck sensor=N`. |
| 5 | SKIPPED | slot not fired (recovery, or `US OFF` in the middle of a cycle). Not sent; counted only. |

`echo_us` = width in µs, rounded from 80 MHz ticks (0 when there is no width). The raw width is always sent, and distance is computed on the host (§3), following protocol principle 4: raw values stay raw.

---

## 3. Protocol: new packet type RANGE (0x06), opt-in

### 3.1 Why a binary packet type and not LOG text
- EMGM/2's design goal 3 is "data and diagnostics never get mixed up". LOG is defined as diagnostics. Ultrasonic readings are a **data stream** with their own loss accounting.
- At 40 LOG lines per second, the host's `events` deque (maxlen 200) would cycle every 5 s and push out real errors. It would also end up in `events.jsonl`, which is the wrong layer.
- A fixed binary layout gives a cheap, exact parser. A sequence number per stream makes loss countable, the same way `frame_seq` does for EMG.
- The existing LOG precedent (`kind=sync` in the MVP plan) concerns rare events (a few per minute). That remains appropriate for those.
- **Compatibility:** the firmware sends RANGE **only after the host sends `US ON`**. HELLO advertises the capability. An old host never asks, so it never sees 0x06 and its `frame_errors == 0` invariant holds. The decoder change below makes new hosts robust to any future type anyway.

### 3.2 Layout (payload 28 bytes; body 31 bytes; about 34 bytes on the wire). 40/s ≈ 1.4 kB/s, against about 10 kB/s for 1-channel EMG on a 50 kB/s link.

```
offset size field
0      u32  us_seq        +1 per RANGE packet emitted (never resets, like frame_seq)
4      u32  run           EMG run id at trigger (same as HELLO run)
8      u64  t_trig_us     esp_timer µs since boot at the TRIG rising edge
16     u32  emg_index     EMG sample index (32-bit, same space as DATA first_index) at t_trig; 0xFFFFFFFF = ADC not running
20     u32  echo_us       echo pulse width in µs (0 if none)
24     u8   sensor        0 or 1 (US1, US2)
25     u8   status        0 OK, 1 NO_ECHO, 2 OUT_OF_RANGE, 3 TOO_CLOSE, 4 STUCK
26     u16  dropped       cumulative readings lost in the device ring (wraps); host diffs it
```

Constant: `RANGE_STRUCT = struct.Struct("<IIQIIBBH")` (28 bytes).

### 3.3 Commands, HELLO and STATUS

| command | effect |
|---|---|
| `US ON` / `US OFF` | start or stop pinging and RANGE output. **Default after boot: OFF** (old hosts unaffected). Not stored in NVS. ACK `cmd=US ok=1 on=1`. |
| `USSLOT <ms>` | slot length, 20 to 60 ms (per-sensor rate = 1000/(2×slot)); ACK, HELLO |
| `USMAX <mm>` | timeout as a distance, 200 to 4000 mm; must fit inside the slot (`timeout_us + 2000 < slot`) or `ok=0` |
| `USPINS t1,e1[,t2,e2]` | optional, NVS-stored like PINS; validated against allowed GPIOs {16,17,18,19,21,22,23}, no duplicates, no overlap with the EMG `pins` |

HELLO adds (all space-separated, after the existing keys):

`us_n=2 us_on=0 us_trig=23,19 us_echo=22,18 us_slot_ms=25 us_rate_hz=20.0 us_timeout_us=11662 us_min_us=116 us_timer=mcpwm_cap us_tick_hz=80000000 us_order=alt range_type=6 range_ver=1`

STATUS adds:

`us_on us_trig=<total triggers> us_ok us_noecho us_oor us_close us_stuck us_dropped`

The host compares `us_trig` with the RANGE packets received, giving a third independent loss check.

`FW_VERSION "2.2.0-us"`. `fw` remains `emg_esp32_v2/…`, and `proto` stays `EMGM/2`. This is additive: no existing packet changes.

### 3.4 Loss accounting (host)
- `link_lost = us_seq − prev_us_seq − 1` (mod 2³²).
- `device_dropped = dropped − prev_dropped` (mod 2¹⁶).
- `expected` = `us_trig` delta between two STATUS packets, compared with readings received in the same span.
- A RANGE with a `run` the host has not seen is held until the matching HELLO, up to 100 readings.

### 3.5 Decoder change (`emgm2.py`)
- Add `RANGE = 0x06` to `TYPE_NAMES` and a `RangeReading` dataclass. Decode in `decode_body`, raising `ValueError("range length")` if the payload is not 28 bytes.
- **Unknown types:** return `Packet(type, payload)` and increment `StreamDecoder.unknown_types`. Do **not** raise and do **not** count `frame_errors` (the CRC already proved the frame is intact).
- `counters()` gains `unknown_types`.
- `Esp32Source._on_packet` logs the first unknown type once per type as a warning.

---

## 4. Host side

### 4.1 `acquisition/esp32.py`
- `_on_hello`:
  - parse the `us_*` keys and compare `us_trig`/`us_echo` with `cfg.hardware.ultrasonic.sensors`
  - on a mismatch, send `USPINS` once (same pattern as PINS)
  - if config `ultrasonic.enabled` and `us_on=0`, send `USSLOT`/`USMAX` if they differ, then `US ON`
  - if the HELLO has no `us_n` but config enables ultrasonic, set `self.error = "firmware has no ultrasonic support (flash 2.2.0-us)"` and fail the handshake. Never silently record without the sensor.
- `_on_range(r)`:
  - loss accounting (§3.4)
  - unwrap `emg_index` with the **same run's** `_idx_base`/`_last_idx32` logic, using a separate copy so the DATA unwrap state is not disturbed
  - `distance_mm = echo_us × c / 2000` with `c = 331.3 + 0.606·T` m/s, where `T = ultrasonic.temperature_c` (default 25 °C → 346.5 m/s), only when `status == OK`
  - append a `RangeSample` to `self._range_queue` (deque, maxlen 2000)
- New `read_ranges() -> list[RangeSample]`.
- `status()` gains an `ultrasonic` block: per sensor, readings, rate on the device clock, status counts, link_lost, device_dropped, last value and age.

### 4.2 `acquisition/base.py`
- New dataclass `RangeSample(run, seq, sensor, t_trig_us, emg_index:int|None, echo_us, distance_mm:float|None, status:int, t_host:float, lost_before:int)`.
- `DataSource.read_ranges()` returns `[]` by default, so the virtual, replay and text sources are unchanged.

### 4.3 `acquisition/pipeline.py`
`poll()` also drains `self.src.read_ranges()` → `self.monitor.update_ranges(rs)` → `self.recorder.write_ranges(rs)`, under the same lock.

### 4.4 `recording/raw.py` (format stays `emgmirror-raw/1`; additive only)
- `write_ranges()` opens **`ultrasonic.csv` lazily on the first reading**, so EMG-only recordings stay byte-identical. Header:

  `run,us_seq,sensor,t_trig_us,emg_index,echo_us,distance_mm,status,t_host_ns,lost_before`

  `distance_mm` is empty unless status is OK. It is flushed with the other files.
- Manifest gains an optional key:

  `"streams": {"ultrasonic": {"file": "ultrasonic.csv", "sensors": [{"id":0,"trig":23,"echo":22,"label":"..."},…], "slot_ms":25, "timeout_us":11662, "c_mps":346.5, "temperature_c":25, "status_codes": {...}}}`

  plus `totals.ultrasonic` (per-sensor counts).
- `RawRecording.open()` reads `ultrasonic.csv` if it exists, into a new attribute `ranges` (numpy structured array, `None` if absent). A cut last line is ignored, like frames.csv.
- Old readers ignore the extra file and key. `RawRecording.open` checks only `format`, which is unchanged.
- `integrity()` adds an `ultrasonic` section:
  - per sensor: n, rate on the device clock (from `t_trig_us`), status breakdown, missing (seq gaps + dropped), max interval
  - **index consistency:** for each reading, the host-interpolated index from frames.csv minus the device `emg_index`. Report the median and max |Δ|, which must be ≤ 2 samples.
- The viewer (`emgmirror view`) can stay as it is for the MVP. An optional later change adds a distance lane under the EMG.

### 4.5 Emulator (`acquisition/emulator.py`)
- `EmulatorConfig` additions:
  - `us_pins=((23,22),(19,18))`, `us_slot_ms=25`, `us_max_mm=2000`, `us_on=False`
  - `us_targets=("sine:300:80:0.5", "sine:500:50:0.3")` (mean mm : amplitude mm : Hz; also `"dc<mm>"`, `"none"` = NO_ECHO, `"far"` = OUT_OF_RANGE, `"close"`, `"stuck_after:<s>"`)
  - `us_noise_mm=2`, `us_c_mps=346.5`
  - faults: `drop_range_seqs`, `corrupt_range_seqs`, `us_ring_drop_at` (device ring overflow), `us_firmware=True` (False = a 2.1.0 HELLO without `us_*`, to test the handshake error)
- `_advance()`: while `us_on`, emit readings for every slot due, alternating sensors. Set `t_trig_us = slot_k × slot_ms × 1000 × (1+ppm)`, `emg_index = round(t/1e6 × fs)` (same clock as DATA), and `echo_us = 2·d/c`. Interleave them in time order with DATA frames, which tests reordering-free assumptions.
- Commands `US ON/OFF`, `USSLOT`, `USMAX`, `USPINS` with the same ACK texts. HELLO and STATUS include the `us_*` keys.
- Registry spec `emulator:emg` plus config `ultrasonic.enabled: true` gives an emulated rig.

### 4.6 Collection check (new CLI action)
`emgmirror acq check --source esp32:COM9|emulator --seconds 30 [--out report.json]`:
- runs the normal pipeline with a raw recording, then prints and writes:
  - EMG rate (device and host clock), missing, invalid
  - per ultrasonic sensor: readings/s (device clock; expected 1000/(2×slot)), % OK / NO_ECHO / OUT_OF_RANGE / STUCK, median/min/max mm, link_lost, device_dropped, longest interval
  - `us_trig` vs received
  - index Δ (device vs host interpolation)
- verdict per source: `ok` / `warn` / `fail`. Thresholds:
  - ultrasonic rate ≥ 95% of expected, OK ≥ 90% on a fixed target, |index Δ| ≤ 2, 0 link loss
  - EMG: existing monitor thresholds
- Exit code 0 / 1 / 2 for scripting.
- The check makes no network calls. The camera parts can add their own rows to the same report.

---

## 5. Configuration changes

- **`configs/esp32_candy_1ch.yaml`: edit.**
  - `gpio: 35, adc_channel: ADC1_CH7, header: D35`, with a history comment ("was GPIO34 until 2026-11-17; wire moved"), and `channels: [{name: ch1, arm: right, muscle: flexors}]`
  - EMG acquisition otherwise unchanged: fs 2000 (os becomes 10), bias and gain nominal
  - the host still sends `PINS 35` on connect
- **New `configs/mvp_collect_1ch_us.yaml`** with `base: configs/esp32_candy_1ch.yaml`:

```yaml
hardware:
  firmware: { version: 2.2.0-us }
  ultrasonic:
    model: HC-SR04 (5 V from VIN, ECHO via 1k/2k divider)   # owner to confirm the model
    supply: VIN 5 V (USB)                                   # NOT measured
    sensors:
      - { id: 0, trig: 23, echo: 22, label: us1, aim: "TBD (owner)" }
      - { id: 1, trig: 19, echo: 18, label: us2, aim: "TBD (owner)" }
  safety_gate_config: "1ch GPIO35 + 2x HC-SR04"            # must match a recorded owner decision
ultrasonic:
  enabled: true
  slot_ms: 25          # 20 Hz per sensor; 30 if ghosting
  max_mm: 2000
  temperature_c: 25    # speed of sound; record the room temperature per session
```

- `safety.py` (one small check): if `ultrasonic.enabled` and `hardware.safety_gate_config` is not in the list of gate decisions recorded in `docs/ELECTRICAL_SAFETY.md` (a simple string match, the same pattern as the existing gate status), then `record` refuses electrodes-on recordings. Bench and emulator runs are unaffected.

---

## 6. File-level change list and test plan

### Firmware: `firmware/emg_esp32_v2/emg_esp32_v2.ino` → 2.2.0-us
Same sketch, behind `#define WITH_ULTRASONIC 1` so 2.1.0 behaviour stays buildable.
- Includes: `driver/mcpwm_cap.h`, `driver/gpio.h`, `esp_rom_sys.h`.
- Buffer enlargement (§0.3).
- `PKT_RANGE = 0x06`.
- `us_setup()`: capture timer (group 0) plus 2 channels on both edges, pull-up off, IRAM callback, enable, start. TRIG pins as outputs, low. One `esp_timer` periodic created but not started.
- `us_slot_cb()` (finalise, fire, stamp), `us_drain()` in `loop()` after `pump_adc()`.
- Commands `US`/`USSLOT`/`USMAX`/`USPINS`, plus the HELLO and STATUS keys.
- `restart_sampling()` leaves pinging running. Readings during an ADC restart get `emg_index = 0xFFFFFFFF`.
- Library choice: no extra Arduino libraries (no NewPing). Only ESP-IDF drivers that are already bundled.

### Firmware test harness (cloud, no hardware)
- Move `crc16` + COBS + RANGE packing into `firmware/emg_esp32_v2/emgm2_core.h` (the shared-core idea already in the MVP plan, W4).
- `tests/firmware/test_core.cpp` (g++) packs a RANGE and compares the bytes with a Python-generated vector.
- `arduino-cli compile --fqbn esp32:esp32:esp32` runs as a compile-only check where the core is installed.

### Host files
- `acquisition/emgm2.py` (RANGE, `unknown_types`)
- `acquisition/base.py` (`RangeSample`, `read_ranges`)
- `acquisition/esp32.py` (handshake, `_on_range`, status)
- `acquisition/pipeline.py`
- `acquisition/monitor.py` (`update_ranges`, report block)
- `acquisition/emulator.py`
- `acquisition/registry.py` (pass the ultrasonic config into `EmulatorConfig`)
- `recording/raw.py`
- `cli.py` (`acq check`)
- `acquisition/safety.py`
- `docs/ACQUISITION_PROTOCOL.md` (RANGE section, commands, HELLO/STATUS keys, loss accounting)
- `docs/HARDWARE_INVENTORY.md` (pins, divider, measured VIN)
- `docs/ELECTRICAL_SAFETY.md` (owner records the new configuration; not written by software)
- `configs/*` as in §5

### Tests: cloud, emulator only (pytest)
- **C1 `test_emgm2_protocol.py`:** RANGE round-trip (byte vector), wrong length → `frame_errors`, unknown type 0x7F → `unknown_types == 1`, `frame_errors == 0`, and the stream continues.
- **C2 `test_ultrasonic_emulator.py`:**
  - with `US ON`, 10 s: each sensor 200 ± 1 readings
  - alternating order (sensor ids strictly alternate, Δt_trig = 25 ms)
  - distances match the target sine within noise
  - `emg_index == round(t_trig × fs)` and the |host-interpolated − device| ≤ 1
- **C3 faults:**
  - `drop_range_seqs` → `link_lost` exact
  - `us_ring_drop_at` → `device_dropped` exact
  - corrupt → CRC count
  - `none`/`far`/`close`/`stuck_after` → correct status codes and no distance
  - an ADC restart in the middle of a run → readings carry the new run, and the index restarts
- **C4 compatibility:**
  - an old-style host flow (no `US ON`) on 2.2.0 → zero RANGE, `frame_errors == 0`
  - a 2.1.0 emulated HELLO with `ultrasonic.enabled` → a clear handshake error
  - existing test suite unchanged and green
- **C5 raw recording:**
  - an EMG-only recording has no `ultrasonic.csv` and an unchanged manifest (golden comparison)
  - with ultrasonic: CSV columns, manifest `streams`, `integrity()['ultrasonic']`, and a crash-truncated last line ignored
- **C6 `acq check` on the emulator:** exit 0 on a clean run, exit 2 with `stuck_after`, JSON schema stable.

### Tests: bench, laptop + ESP32, **no electrodes on anyone**, laptop on battery
- **B0** Back up the flash (esptool, as in the inventory).
  - Check the board boots 5/5 times after the sensors and capacitors are added (boot-loop history).
  - Measure VIN, then each ECHO at the GPIO side of the divider (must be ≤ 3.4 V when high).
  - Run `acq scan`: GPIO35 still driven, 18/19/22/23 not interfering.
- **B1** Flash 2.2.0-us. Run the existing `tests/physical/test_esp32_hardware.py` (`US OFF`): 0 CRC/frame errors, 5 min with 0 missing. This is the EMG regression check.
- **B2 Accuracy and rate:**
  - flat target (book or box) at 200, 500 and 1000 mm, measured with a tape: 60 s each, `acq check`
  - pass if rate is 20.0 ± 0.2 Hz per sensor, OK ≥ 98%, median error ≤ 10 mm, σ ≤ 3 mm
  - point both sensors at the same target, then at each other: count ghost readings
  - if ghosts > 1%, switch to `USSLOT 30` and repeat
- **B3 Artefact on EMG** (Candy powered, inputs shorted or open as in the floor check; no body):
  - 60 s `US OFF`, then 60 s `US ON`, same setup
  - compare the PSD at 40·k Hz and the trigger-locked average of the EMG (±5 ms around each `emg_index`)
  - pass if the trigger-locked average is below the room floor (12–18 mV at the pin, `esp32_candy_2ch.yaml` room_noise_floor). Otherwise apply mitigations 2 to 4 from §1.5 and repeat.
- **B4 No-target and stuck:** aim at open space and at a cushion (expect OUT_OF_RANGE, not STUCK). Unplug a sensor's ECHO (expect NO_ECHO). Unplug its VCC (expect NO_ECHO, no crash).
- **B5 Timeline:** wave a hand in front of US1 while tapping the Candy cable. Check in the raw recording that the distance dips and the EMG taps line up on `emg_index`, and that integrity index Δ ≤ 2.
- **B6 Gate:** the owner records the new configuration in `docs/ELECTRICAL_SAFETY.md`. Only after that, the first electrodes-on collection: right forearm, battery, phones not on USB or chargers.

---

## 7. Licences, cost and privacy of everything used

| Item | Licence | Cost | Phones home / account / cloud | Source |
|---|---|---|---|---|
| ESP-IDF drivers (MCPWM, esp_timer, adc_continuous), bundled in the core | Apache-2.0 | free | none at runtime; firmware has radio off | github.com/espressif/esp-idf |
| Arduino-ESP32 core 3.3.12 (already installed) | LGPL-2.1 | free | none at runtime; install/update downloads from GitHub/Espressif only | github.com/espressif/arduino-esp32 |
| arduino-cli (already used) | GPL-3.0 | free | `metrics` exists but only in daemon mode, for local monitoring, not internet (https://github.com/arduino/arduino-cli/issues/1620, https://arduino.github.io/arduino-cli/dev/configuration/). Set `metrics.enabled: false` to be sure. `core update-index` fetches package indexes; no account. | github.com/arduino/arduino-cli |
| esptool (already used) | GPL-2.0+ | free | none | github.com/espressif/esptool |
| Python: numpy, pyserial (already dependencies) | BSD-3 | free | none | yes |
| No new pip packages. No NewPing library (blocking design and GPL-3; not needed). | | | | |
| Hardware | | 2× HC-SR04 about €1–2 each; 4 resistors; 2× 10–47 µF + 100 nF | | |
