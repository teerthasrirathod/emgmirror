# 2026-11-17: Candys powered from a Raspberry Pi 5 V rail (Pi on a USB-C supply), ESP32 GND tied to Pi GND

Read-only, 3 s, all ADC1 pins, firmware emg_esp32_v2 2.1.0, COM7, 0 samples missing.

| pin | min mV | max mV | time at full scale (>= 3.4 V) | time at floor | 50 Hz share |
|---|---|---|---|---|---|
| GPIO32 | 152 | 3453 | 49.8 % | 6.7 % | 84 % |
| GPIO33 | 152 | 3453 | 50.0 % | 9.3 % | 85 % |
| GPIO34 | 152 | 3453 | 49.2 % | 29.7 % | 86 % |
| GPIO35 | 152 | 3453 | 49.8 % | 27.3 % | 86 % |
| GPIO36 | 152 | 3453 | 53.9 % | 11.4 % | 61 % |
| GPIO39 | 152 | 3453 | 52.2 % | 9.7 % | 77 % |

Rail-to-rail 50 Hz on every pin: the ADC range (152-3453 mV with this chip's eFuse calibration) is
exceeded at both ends about half the time. With a 5 V Candy supply the output can exceed the ESP32's
~3.6 V absolute maximum. No noise floor or EMG response can be measured in this state.
Advice given: disconnect the Candy outputs from the ESP32; return to 3.3 V from the ESP32 (vendor
docs list 3.3 V as supported), or use a battery-powered 5 V source plus a divider on each output.
