# ESP32 mic recorder

- Board: ESP32-D0WD-V3 (4 MB flash)
- Supported sensor: analog-output microphone module (for example KY-038/KY-037 class); firmware captures GPIO34 ADC1 at 8 kHz, 8-bit mono and wraps it as a playable WAV in the browser.
- Dashboard: connect to Wi-Fi `ESP32-Mic` (password `voicecapture`) and open `http://192.168.4.1/`.
- The dashboard stores recordings in the browser's IndexedDB and offers playback and WAV download. Audio is not uploaded to a cloud service.
- Firmware source is `esp32_mic_recorder.ino`; inline dashboard source is `dashboard.h`.
- Full pre-change 4 MB flash image: `..\..\firmware_backups\esp32_pre_mic_recorder_2026-11-14.bin`.

## Wiring (analog-output mic modules only)

| Microphone terminal | ESP32 terminal |
| --- | --- |
| `VCC` / `+` | `3V3` |
| `GND` / `-` | `GND` |
| `AO` / `A0` / analog `OUT` | `GPIO34` |
| `DO` / digital `OUT` (if present) | leave disconnected |

Do not connect a 5 V analog output to GPIO34. This firmware does not support I2S microphones with `BCLK`/`SCK`, `WS`/`LRCL`, and `DOUT`/`SD` pins; those require a different wiring map and capture mode.
