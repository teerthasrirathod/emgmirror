# Hardware inventory and channel map

Status as of **2026-11-16, evening**. Every line says how it is known. "Measured" means measured
on the board that day. "Owner" means you told me. Anything else has **not** been checked.

## Board

| Item | Value | How it is known |
|---|---|---|
| Module / chip | ESP32-D0WD-V3, revision v3.1, dual core, 240 MHz, 40 MHz crystal | Measured: `esptool flash-id` |
| MAC | 48:9d:31:04:70:70 | Measured: esptool, and the device HELLO |
| Flash | 4 MB (manufacturer 0xC4, device 0x6016) | Measured: esptool |
| USB-serial bridge | CH340, VID:PID 1A86:7523, **COM9** (was COM12 on 11-07 and COM7 on 11-08; changes with the USB socket) | Measured |
| Board | 30-pin "ESP32 DevKit", ESP-WROOM-32 module. Header row as printed: VIN, GND, D13, D12, D14, D27, D26, D25, D33, D32, D35, D34, VN, VP, EN | Owner's photo, 2026-11-16 |
| ADC calibration | eFuse line fitting: pin mV = 0.806 × code + 152. **Code 0 = 152 mV**, so readings of 152 mV mean "at or below the ADC floor (about 0.15 V)" | Measured: device HELLO |

## Firmware

| | |
|---|---|
| On the board now | **`firmware/emg_esp32_v2` v2.1.0** (EMGM/2), flashed 2026-11-16 with arduino-cli, hash verified. Pin map stored in NVS: 34,39,36. |
| Backup of the previous firmware | `firmware_backups/esp32_flash_pin_survey_csv_2026-11-16.bin`: the full 4 MB of the pin-survey sketch, SHA-256 `b5690cb5…ba0f0` (git-ignored). Restore: `python -m esptool --port COM9 --baud 921600 write-flash 0 <file>` |
| Older backup | `firmware_backups/esp32_flash_before_structured_csv_2026-11-14.bin` |

## Channel map

| Channel | Header label | GPIO / ADC | Wire | Goes to | Signal at the pin (no electrodes) | Status |
|---|---|---|---|---|---|---|
| **CH1** | D34 | GPIO34 / ADC1_CH6 | red | a Candy OUT (owner) | **driven**: 1661 mV idle (mid-rail of 3.3 V), about 50 mV rms, 61 to 74% of it mains hum | **Working output** |
| **CH2** | VN | GPIO39 / ADC1_CH3 | green | a Candy OUT (owner) | **floating**: its reading follows the previously sampled channel | **No output from its Candy** |
| **CH3** | VP | GPIO36 / ADC1_CH0 | purple | a Candy OUT (owner) | **floating** | **No output from its Candy** |

Wire colours per pin come from the photo: red, green and purple, left to right, on D34, VN, VP.
You confirmed the pins.

### How the map was established (measurements, not assumptions)

1. **Driven-or-floating scan** (`emgmirror acq scan`). The ESP32 samples all channels onto one
   sample-and-hold capacitor. A pin with nothing driving it mostly reports what the previous
   channel left there. So every ADC1 pin is scanned in forward and reverse order: a driven pin
   reads the same in both orders, a floating pin does not.
2. **Plug / unplug / re-plug.** Scans were taken with the connector plugged in, unplugged, and
   plugged in again.
3. **Single-pin runs and 18 channel orders** (1 to 6 pins) to make sure samples are never
   attributed to the wrong channel.

Results (`docs/test_results/physical/2026-11-16_pinscan_*.json`):

| Scan | GPIO32 | GPIO33 | GPIO34 | GPIO35 | GPIO36 | GPIO39 |
|---|---|---|---|---|---|---|
| plugged in (first) | floating | floating | floating | floating | **driven, 1661 mV** | floating |
| unplugged | floating | floating | floating | floating | floating | floating |
| re-plugged (now) | floating | floating | **driven, 1662 mV** | floating | floating | floating |
| single-pin runs (now) | | | **1660 mV**, σ 56 | | 245 mV, σ 134 | 429 mV, σ 395 |

**Unexplained, needs you:** before the unplug, the live signal was on VP (GPIO36). After the
re-plug it is on D34 (GPIO34). Either the connector sat one pin further along before (on VN, VP,
EN), or a different Candy is live now than before. The second would point to an **intermittent
power or ground contact**. Labelling has been checked in 18 orders, so this is not a software
mix-up. Only measurements on the Candys can tell these two apart.

### The earlier wrong numbers

On 2026-11-16 (afternoon) I reported "CH1 GPIO35 0.24 V, CH2 GPIO32 2.68 V, CH3 GPIO33 2.86 V". Those
pins were read correctly, but **nothing is plugged into them**: they are floating inputs, and the
CH1 to CH3 labels came from an out-of-date config. That table has been withdrawn.

## What a working channel looks like (CH1)

With no electrodes on, the live Candy output sits at mid-rail (about 1.66 V) and carries mostly
50 Hz hum. That is what a powered amplifier with open inputs is expected to do. It is **not** EMG,
and it does not tell us the gain.

## What I need from you (no electrodes on anyone)

Use a multimeter on DC volts. Keep the black probe on an ESP32 **GND** pin. Power the board from
USB.

| # | Measure | Expected if correct |
|---|---|---|
| A | ESP32 3V3 pin | about 3.3 V |
| B | each Candy's **VCC** pin, on the Candy itself (red-wire Candy, green-wire Candy, purple-wire Candy) | about 3.3 V on all three |
| C | each Candy's **OUT** pin | about 1.65 V if powered (the red one should match the 1.66 V seen at GPIO34) |
| D | with USB unplugged, in continuity mode: each Candy GND to ESP32 GND | beep |

If **B** is about 0 V on the green and purple Candys, their VCC or GND jumpers aren't making
contact (a loose jumper or a split breadboard rail). That would explain everything above.
Afterwards, `emgmirror acq scan --source esp32:COM9` should show GPIO39 and GPIO36 as **driven**
at about 1.65 V.

## Rig enclosure (2026-11-17)

Built by the owner from household materials:

- each Muscle BioAmp Candy heat-sealed in plastic packaging film (protects the pins from touch,
  sweat and moisture)
- ESP32, breadboard and Candys placed in a cardboard box, suspended in the air by ropes

Measured right after the change (12 s rest, same method, `docs/test_results/physical/2026-11-17_floor_after_interference_removed.md`):
noise floor CH1 35.5 -> 12.3 mV, CH2 117.6 -> 18.4 mV; 50 Hz 202/634 -> 73/40 mV rms;
100/150 Hz harmonics about 200x lower; CH2 clipping 21 % -> 0 %.

Plastic and cardboard are insulators and do not block electric fields. The improvement most likely
came from distance to mains sources and fixed, separated cable runs; the individual contributions
have not been separated yet. Frugal next steps, each to be checked with the floor check before and
after:

1. aluminium-foil lining inside the box, connected to ESP32 GND (an actual electric-field shield;
   keep the plastic film between foil and circuit)
2. twisted pairs: each Candy's electrode leads together; each OUT wire with a GND wire
3. secure the box at two points so it cannot swing (moving cables cause motion artifacts)
4. keep the rig 1-2 m from wall sockets, adapters, LED tube lights and fan regulators
