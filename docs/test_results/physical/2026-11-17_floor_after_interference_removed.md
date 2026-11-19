# 2026-11-17 16:40: rest floor after the owner removed an interference source

Setup: Candys on ESP32 3V3, outputs on D35 (CH1) and VP (CH2), electrodes on the right forearm,
laptop on battery. 12 s rest each, 0 samples missing. Floor = band-pass 75-450 Hz + mains regression,
RMS at the ADC pin.

| | floor mV | 50 Hz rms mV | 100 Hz pk mV | 150 Hz pk mV | mean mV | at ADC floor |
|---|---|---|---|---|---|---|
| CH1 D35 before (15:55) | 35.5 | 202 | 208 | 71 | 894 | 0 % |
| CH1 D35 now (16:40)    | 12.3 | 73  | 3   | 1  | 864 | 0 % |
| CH2 VP before (15:55)  | 117.6 | 634 | 591 | 245 | 1634 | 21 % |
| CH2 VP now (16:40)     | 18.4 | 40  | 3   | 1  | 1667 | 0 % |

Both floors are below the morning reference (22.4 / 48.9 mV). CH1 idles at 0.86 V (CH2 at the
expected ~1.65 V); within range, cause not checked.
Recordings: data/raw/20261117-155535_rest-floor-recheck, data/raw/20261117-164051_rest-floor-after-change.
