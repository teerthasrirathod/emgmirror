# EMGM/2 acquisition protocol

EMGM/2 is the wire protocol between an acquisition device and the host. The reference device is
`firmware/emg_esp32_v2/emg_esp32_v2.ino`. The reference host decoder is
`src/emgmirror/acquisition/emgm2.py`, and the device logic around it is in `acquisition/esp32.py`.
`acquisition/emulator.py` is a software device that speaks the same protocol.

The protocol solves four problems that the older text protocol ("one `a,b,c` line per sample") could
not:

1. **Lost samples are detectable and countable.** Every sample has an index.
2. **Losses on the device can be told apart from losses on the link.** The sample index counts
   samples the device itself had to discard, while the frame sequence number counts packets.
3. **Data and diagnostics never get mixed up.** Every packet is typed and CRC-checked.
4. **Raw values stay raw.** The device sends ADC codes plus its calibration constants. It never
   sends converted voltages.

## Transport

- USB serial (CH340 on the reference board), **500000 baud, 8N1**. The device sends a continuous
  byte stream.
- Opening the port must not reset the board. Keep DTR and RTS de-asserted; `open_serial` in
  `acquisition/esp32.py` does this. The device is usually already streaming when the host opens the
  port.

## Framing

```
packet on the wire:  COBS( body ) 0x00
body:                type:u8 | payload | crc:u16le
crc:                 CRC-16/CCITT-FALSE over type+payload
                     (poly 0x1021, init 0xFFFF, MSB-first, no final xor; check("123456789") = 0x29B1)
```

- COBS (Consistent Overhead Byte Stuffing) removes every 0x00 from the body, so 0x00 only ever
  appears as the packet delimiter.
- To resynchronise, discard bytes up to the next 0x00. The bytes before the first delimiter after
  opening the port are the tail of an earlier packet, not an error.
- A packet that fails COBS decoding, the CRC check, or the length check is dropped and counted.
  Dropped DATA packets then show up as a `frame_seq` gap.
- All integers are little-endian.

## Packet types

| type | name | direction | payload |
|---|---|---|---|
| 0x01 | HELLO | device → host | ASCII `key=value` pairs separated by single spaces |
| 0x02 | DATA | device → host | binary, see below |
| 0x03 | STATUS | device → host | ASCII `key=value`, once per second |
| 0x04 | LOG | device → host | ASCII `key=value`; `level=error` for faults |
| 0x05 | ACK | device → host | ASCII `key=value`, the reply to a command |

### HELLO

The device sends HELLO at boot, on `INFO`, and after every (re)start of sampling. Keys sent by
firmware 2.1.0:

| key | example | meaning |
|---|---|---|
| `proto` | `EMGM/2` | protocol identifier; the host refuses anything else |
| `fw` | `emg_esp32_v2/2.1.0` | firmware name and version |
| `built` | `Nov_16_2026_17:02:11` | compile time |
| `chip`, `rev` | `ESP32-D0WD-V3`, `301` | chip model and revision |
| `mac` | `489d31047070` | Wi-Fi STA MAC, a unique device id |
| `run` | `1` | run counter; increments every time sampling (re)starts |
| `running` | `1` | 1 while sampling |
| `fs` | `2000` | output samples per second per channel |
| `os` | `4` | conversions summed into each output sample |
| `nch` | `3` | channels per row (1 to 6) |
| `pins` | `34,39,36` | GPIO per channel, in column order (set with `PINS`, stored in NVS) |
| `adc_unit`, `atten_db`, `bits` | `1`, `12`, `12` | ADC configuration |
| `rows_per_frame` | `10` | rows in each DATA packet |
| `cal` | `efuse_line_fitting` | calibration source (`nominal` if the eFuse has none) |
| `cal_a`, `cal_b` | `0.805900`, `0.0000` | pin mV = `cal_a * code + cal_b` |
| `baud` | `500000` | |

The host must check `proto`, `nch` and `pins` against its configuration. If they differ, the
reference host sends `PINS` with the configured map once and waits for the HELLO that follows. If
they still differ (or `apply_pins=False`), it fails the handshake.

### DATA (0x02)

```
offset size field
0      u32  frame_seq      increments by 1 for every DATA packet the device emits (never resets)
4      u32  first_index    sample index of row 0; counts every sample since the run started,
                           including samples the device discarded; wraps at 2^32 (~24.8 days at 2 kHz)
8      u64  t_us           device clock (esp_timer, µs since boot) when the DMA finished this frame,
                           i.e. at the last row of the packet
16     u32  dma_overflow   cumulative count of conversion frames the device discarded (pool full)
20     u8   n_rows         rows in this packet (10)
21     u8   n_ch           channels per row (3)
22     u8   os             conversions summed per sample
23     u8   flags          bit0 INCOMPLETE_ROW: some sample in this packet is invalid
                           bit1 RUN_START: first packet of a run
                           bit2 AFTER_DROP: the device discarded frames right before this one
24     u16[n_rows][n_ch]   raw samples, row-major
```

- **Raw sample** = the sum of `os` consecutive 12-bit conversions of that channel (0 to 4095·os).
  `0xFFFF` means invalid: that row did not receive all of its conversions.
- **Pin voltage**: `pin_mV = cal_a * (raw / os) + cal_b`.
- **Electrode-referred estimate** (host side, not part of the protocol):
  `mV = (pin_mV - bias_mV) / gain`. Both bias and gain are configuration values, and neither has
  been measured on the reference rig. See the hardware inventory.
- **Timing**: sample spacing is set by the ADC clock. Sample `first_index + r` lies `r / fs` after
  sample `first_index`. `t_us` timestamps the end of the packet on the device clock, which gives an
  exact rate estimate that is independent of USB latency.

### Loss accounting (host)

Let `expected` be the index that follows the previous packet's last row (same run).

- `gap = first_index - expected`. If `gap > 0`, samples are missing.
- `lost_frames = frame_seq - previous_frame_seq - 1`. These frames were lost on the link or failed
  the CRC check. `link = min(gap, lost_frames * n_rows)`.
- `device = gap - link`. The device discarded these itself; such a packet also has `AFTER_DROP` set.
- `first_index < expected`, or a HELLO with a new `run`: a new run. The index restarts and no gap is
  counted.

### STATUS (0x03), once per second

`run running rate_dev index frames dma_overflow incomplete_rows ts_lost tx_free uptime_ms`

`rate_dev` is rows/s measured on the device clock over the last second. `tx_free` is the free space
in the serial TX buffer; values near 0 mean the link is saturated.

## Commands (host → device)

ASCII lines ending in `\n`, case-insensitive. Each command is answered with an ACK packet.

| command | effect |
|---|---|
| `INFO` | re-send HELLO |
| `STOP` | stop sampling; no DATA packets until START |
| `START` | (re)start sampling: index restarts at 0, `run` increments, HELLO follows |
| `RATE <hz>` | set `fs` (500 to 4000) and restart sampling. `os` is chosen to bring the conversion rate `fs × os × nch` to at least 20 kHz (ESP32 ADC DMA lower limit), but `os` cannot exceed 16, so the lowest rate that actually samples depends on the channel count: **1 channel ≥ 1250 Hz, 2 channels ≥ 625 Hz, 3 or more channels ≥ 500 Hz**. Below that the firmware accepts the rate but the ADC does not start (`adc_start_failed` LOG, HELLO `running=0`, no DATA); the host then sends `START` once and, if the device still reports `running=0`, stops with an error naming the minimum rate |
| `PINS <g1,g2,...>` | 1 to 6 distinct ADC1 GPIOs (32, 33, 34, 35, 36, 39) in column order; stored in NVS so they survive a reboot; restarts sampling and sends HELLO. Invalid lists are refused with `ok=0` |

## Bandwidth

At 2 kHz and 3 channels: 200 packets/s of 84 body bytes, about 18 kB/s on the wire including COBS
and the delimiter. The link carries about 50 kB/s at 500000 baud, so it runs at roughly 35% load.
At 6 channels and 2 kHz it is about 30 kB/s (60%). Measured on the reference board (3 channels,
2 kHz, 20 s, 30 s and 5 min): no CRC or framing errors and no losses (`docs/test_results/physical/`).

Note: `t_us` and the ADC sample clock both derive from the same 40 MHz crystal, so the device-clock
rate is always about 2000.0. Only the host-clock rate is an independent check of the sample rate.

## Writing a compatible client

1. Open the port with DTR and RTS low. Send `INFO\n`.
2. Split the stream on 0x00, COBS-decode each piece, check the CRC, and dispatch on the type byte.
3. Wait for HELLO, then check `proto`, `nch` and `pins`. Keep `os`, `cal_a`, `cal_b` and `fs`.
4. For DATA, unwrap `first_index` to 64 bits, apply the loss accounting above, and keep `raw` as it
   was received.

`tests/test_emgm2_protocol.py` contains byte-level test vectors (the CRC check value, COBS edge
cases, and a full DATA round-trip).
