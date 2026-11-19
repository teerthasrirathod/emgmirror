# Acquisition: setup and operation

Commands are for Windows from the repository root. On Linux or macOS, replace `.venv\Scripts\` with
`.venv/bin/`. **Nothing in this guide involves electrodes.** Electrodes on a person stay off-limits
until `docs/ELECTRICAL_SAFETY.md` says the gate passed.

## 1. Install

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Firmware tools: the Arduino IDE 2 ships `arduino-cli`, and the board needs the esp32 core
(3.3.12 was used). `esptool` (5.x) is used for identification and backups:
`python -m pip install esptool`.

```
set ACLI=%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe
```

## 2. Identify the board (read-only)

```
.venv\Scripts\emgmirror ports
python -m esptool --port COM9 flash-id
```

## 3. Back up, then flash the acquisition firmware

Back up the board's current flash first. It takes about 1 minute and is read-only:

```
python -m esptool --port COM9 --baud 921600 read-flash 0 0x400000 firmware_backups\esp32_flash_<what>_<date>.bin
```

Compile and upload:

```
"%ACLI%" compile --fqbn esp32:esp32:esp32 firmware\emg_esp32_v2
"%ACLI%" upload  --fqbn esp32:esp32:esp32 -p COM9 firmware\emg_esp32_v2
```

Roll back to your own backup at any time:

```
python -m esptool --port COM9 --baud 921600 write-flash 0 firmware_backups\esp32_flash_<what>_<date>.bin
```

Backups are git-ignored (`firmware_backups/*.bin`), because the flash can hold stored Wi-Fi
credentials. A fresh clone has none: each person backs up their own board with the read-flash step
above before flashing it.

## 4. Check the device and the stream (no electrodes)

```
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq probe --source esp32:COM9 --seconds 20 --out docs\test_results\physical\<date>_probe.json
```

The output has one line per stage (connection, device, acquisition, channels, emg), followed by
the rates, the loss counts and a per-channel pin report. What each result means:

| Stage | OK means | Typical failure |
|---|---|---|
| connection | the port opened | wrong COM port, port busy (close Arduino IDE / Serial Monitor) |
| device | an EMGM/2 HELLO arrived and matches the config (3 channels, pins 35,32,33) | other firmware on the board |
| acquisition | samples arrive at the nominal rate with nothing missing | link saturated, host stalled |
| channels | each pin is mid-range and varying | stuck at 0 V (front-end output not reaching the pin) |
| emg | never "ok" for real hardware while the safety gate is blocked | — |

### Which pins are actually driven (no electrodes)

```
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq scan --source esp32:COM9 --out docs\test_results\physical\<date>_pinscan.json
```

This scans all six ADC1 pins (GPIO32, 33, 34, 35, 36, 39) in forward and reverse order. A pin
wired to a powered amplifier output reads the same in both orders ("driven"). An unconnected pin,
or one wired to a board that isn't driving its output, follows whichever pin was sampled before
it ("floating"). Run it again with the connector unplugged to see which pins the connector feeds.
After a scan the device keeps the six-pin map. The next normal session sends the configured map
(`hardware.channels` in the config) back to the device automatically with `PINS`.

The channel map is the `hardware.channels` list in `configs/esp32_candy.yaml`. Change it there.
The host applies it to the device on connect, and the device stores it in NVS.

Other rates (the device picks the oversampling):

```
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq probe --source esp32:COM9 --rate 1000 --seconds 10
```

Physical test suite (runs only when `EMGMIRROR_PORT` is set). It writes its results to
`docs/test_results/physical/`:

```
set EMGMIRROR_PORT=COM9
.venv\Scripts\python -m pytest tests\physical -m hardware -v
```

## 5. Live diagnostics page

```
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml serve
```

Open http://127.0.0.1:8000/diagnostics, enter a source (`esp32:COM9`, `emulator`,
`emulator:emg,flat_low,saturated`, `virtual`), and press **Connect**. The page shows:

- the five stages
- the device-clock and host-clock rates
- the sample count, missing samples (device or link), invalid samples, and CRC/framing errors
- reconnects and runs
- the recording state
- the last 2 s per channel at the ADC pin, with mean, range, peak-to-peak, mains share and a verdict
- recent events

**Start raw recording** writes `data/raw/<id>/`. The page keeps recording even with no browser
open. The analysis dashboard is still at `/`.

## 6. Recording

Free-running raw recording (Ctrl+C to stop):

```
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq record --source esp32:COM9 --label bench --seconds 0
```

Guided protocol (raw recording plus a Session block with trials):

```
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml record --source esp32:COM9 --arm right --session day1
```

**Power check.** With a real device, `record`, `dataset run` and the live check refuse to start unless
the computer reports that it runs on battery, and they stop (raw recording marked `interrupted`) if
mains power appears during the recording. The power state is read on:

- Windows: `GetSystemPowerStatus`
- macOS: `pmset -g batt` (`'Battery Power'` = on battery)
- Linux: `/sys/class/power_supply/*/online` (present on most laptops; usually absent on desktops,
  virtual machines and containers)

If the state cannot be read, the recording stays **blocked** and there is no override: battery-only
operation is an electrical-safety condition for a person wearing electrodes
([ELECTRICAL_SAFETY.md](ELECTRICAL_SAFETY.md)), and software that cannot see the power state cannot
vouch for it. Use a laptop on one of the platforms above. Emulator, virtual and replay sources are not
affected.

Inspect a raw recording and convert it for analysis:

```
.venv\Scripts\emgmirror raw list
.venv\Scripts\emgmirror raw inspect <recording_id>
.venv\Scripts\emgmirror raw to-session <recording_id> --session <name> [--arm right] [--allow-gaps]
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml analyze <name>
```

`raw inspect` writes `data/derived/<id>/integrity.json` and `validated.npz` and never modifies the
raw files. `to-session` refuses a recording with missing samples unless you pass `--allow-gaps`, in
which case each fill is listed in the session's `provenance`.

## 7. Without hardware

Every command above also works with `--source emulator[:modes]` (an emulated ESP32 speaking
EMGM/2, with optional faults) or `--source virtual` (the motor-unit simulator). Both are labelled
as not real hardware everywhere: CLI, page, manifest and session.

```
.venv\Scripts\emgmirror --config configs\esp32_candy.yaml acq probe --source emulator:emg,flat_low,saturated
```

## 8. Tests

```
.venv\Scripts\python -m pytest -q                                   # everything (CT tests take ~20 min)
.venv\Scripts\python -m pytest -q tests\test_emgm2_protocol.py tests\test_esp32_simulated.py tests\test_raw_recording.py tests\test_acq_monitor_server.py
.venv\Scripts\python scripts\bench_emgm2.py                         # host decoding headroom
```
