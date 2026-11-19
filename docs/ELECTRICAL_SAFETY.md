# Electrical safety gate

**Status: PASSED WITH CONDITIONS, 2026-11-16, by the owner** (owner's decision, recorded here).
It covers only this configuration:

- **one channel**, CH1 = GPIO34 (red wire), with `configs/esp32_candy_1ch.yaml`
- the owner's **right forearm** (healthy arm), intact skin, no scar or implant under or near the
  electrodes
- the laptop on **battery**, with the charger and **every other cable** (monitor, dock, Ethernet,
  other USB devices) unplugged; only the ESP32's USB cable connected
- `emgmirror record` checks the power status and **refuses to start on AC power**, and stops the
  recording (marking the raw file interrupted) if AC appears during it. Verified 2026-11-16: the
  laptop reported battery

**Accepted residual risk (owner):** the vendor documentation does not describe the Candy's input
protection (item 2). Battery-only operation, the vendor's own instruction, is the isolation
relied on. No USB isolator is used.

**Extended 2026-11-17 at the owner's request:** the faulty Candy was removed. The same conditions
(right forearm, battery only, nothing else plugged in, power interlock) now cover the **two
remaining Candys**, `configs/esp32_candy_2ch.yaml` (GPIO35 and GPIO36). VCC on both: 3.22 V
(owner's multimeter).

Still **blocked**: the left forearm (needs surgeon or physio clearance) and any mains-connected
setup.

The original blocked status, kept for the record: *Do not connect electrodes to any person.*

This gate has to pass before anything connected to this rig touches a body. It is a project rule,
not a certification. The rig is a research prototype and is not a medical device. Passing the gate
means the specific risks below were checked and written down for this exact arrangement. It does
not mean the rig is safe in general.

Nothing in the software can make the arrangement safe, so no software change removes this gate.

## Why "it's only USB" is not enough

Electrodes on the skin make the body part of the circuit. Through the USB cable, the circuit's
ground is the laptop's ground. That ground can be tied to mains earth, or to a mains-powered
supply, through things that have nothing to do with EMG:

- the laptop charger, while plugged in (many chargers couple the output to mains through a small
  filter capacitance; earthed chargers connect it directly)
- an external monitor (HDMI/DisplayPort shields are earthed), a USB-C dock, a powered USB hub
- an Ethernet cable, a printer, speakers, or any other mains-powered USB peripheral
- other modules on the same breadboard if any of them has its own supply

A fault or leakage in any of these can push current through the electrodes. Unplugging the charger
only removes one of these paths.

## Checklist

| # | Item | Required | Status 2026-11-16 |
|---|---|---|---|
| 1 | **Front-end identity**: exact board and version, with its documentation or schematic | Known | Not verified (inventory item H) |
| 2 | **Front-end input protection**: series resistance or current limiting on the electrode inputs, per the schematic | Present and documented | **Unknown** |
| 3 | **Front-end supply**: each Candy VCC at about 3.3 V from the ESP32 3V3 rail, not VIN (5 V) | Measured | Owner says 3V3 + GND. **Not measured.** Only 1 of 3 Candys shows an output. |
| 4 | **Output range into the ESP32**: every Candy OUT between 0 and 3.3 V in all conditions (the ESP32 pins are not 5 V tolerant) | Measured | Partly: the one live output (GPIO34) idles at 1.66 V. The other two are not driving. Range under signal is not measured. |
| 5 | **Grounding**: one ground, the ESP32 GND, shared by all Candy boards; no other ground reference connected to the body | Verified by continuity and photos | **Not verified** (inventory E, G) |
| 6 | **Isolation from mains**: either (a) the laptop runs on battery with **everything** else unplugged (charger, monitor, dock, Ethernet, other USB devices), or (b) a USB isolator rated for the purpose sits between the laptop and the ESP32 | Chosen, and written down for each session | **Not established**. Which other equipment is connected is unknown. |
| 7 | **Other hardware on the rig**: no other module on the breadboard is powered from a separate supply | Verified | **Unknown**: GPIO36/39 show voltages, and earlier sessions had microphones and a hall sensor on the board |
| 8 | **Electrode site**: intact skin; no recent surgical site, implant or broken skin under or near the electrodes without written clearance from the treating clinicians | Confirmed per person and site | Recorded privately, not in this repository |
| 9 | **Stop rule**: anything that tingles, warms or hurts means stop and disconnect | Agreed | Documented here |

## What unblocks the gate

1. Inventory measurements A to H (`HARDWARE_INVENTORY.md`), which close items 1 and 3 to 5.
2. The front-end documentation, for item 2. If the input protection cannot be confirmed, the gate
   stays blocked unless an isolator (option 6b) is used.
3. A written list of what is connected to the laptop during a session, for item 6.
4. Clearance for the specific electrode sites, for item 8.

When every row is satisfied, change the status at the top to **PASSED (date, by whom)**, set
`hardware.safety_gate: passed` in your config, and record the evidence (photos, readings) next to
it. Until then, the software and the documentation treat every real-hardware session as
"EMG capture not verified". They never instruct anyone to attach electrodes.

## What can be done safely while the gate is blocked

Everything that involves no body connection: flashing and testing firmware, checking stream
integrity and rates, checking pin voltages, applying known voltages to the inputs from a bench
source within 0 to 3.3 V, and running the full software stack on the emulator or on virtual data.
