# 2026-11-17: two channels at rest with electrodes on (right forearm, battery)

Raw recording: data/raw/20261117-144249_rest-2ch-contact-check (git-ignored).
Stream: 30,260 samples, 0 missing, status complete.

| | 50 Hz at ADC pin (rms) | swing | time at ADC floor | vendor envelope at rest |
|---|---|---|---|---|
| CH1 GPIO35 (D35) | 93-405 mV | 1.1-1.5 V p2p | 0-12 % | 262 mV = 54 counts |
| CH2 GPIO36 (VP)  | 46-589 mV | ~2.0 V p2p    | 0-11 % | 439 mV = 90 counts |

Vendor reference (Upside Down Labs bar-graph example): rest < 16 counts, full flex ~36 counts.
At rest both channels already exceed full-flex level from mains hum and clipping, so EMG cannot be
distinguished. Cause: electrode contact or pickup (hum varies second to second), not the
acquisition (0 loss). On 2026-11-16 CH1 with electrodes had ~9 mV rms total at the pin.
