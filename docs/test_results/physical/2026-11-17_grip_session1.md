# 2026-11-17 16:49: first grip dataset session (right forearm, 2 channels)

Session: data/dataset/self/20261117-164940_grips (git-ignored). Setup: Candys on ESP32 3V3, CH1 D35
(flexors), CH2 VP (extensors), gel electrodes, enclosed rig, laptop on battery.
Grip recording: 379,330 samples, 0 missing, host-clock rate 1999.996 Hz.

Median over 3 repetitions, metrics at the ADC pin after 75-450 Hz band-pass + mains regression,
relative to the session's own floor check:

| ch | task | target | SNR dB | net RMS mV | active (>2x floor) | median Hz |
|---|---|---|---|---|---|---|
| 1 | rest | 0 | -3.5 | 0.0 | 0.00 | 166 |
| 1 | grip_light | 0.2 | 3.0 | 14.6 | 0.10 | 128 |
| 1 | grip_medium | 0.5 | 16.8 | 99.1 | 1.00 | 120 |
| 1 | grip_max | 1.0 | 17.2 | 104.5 | 1.00 | 120 |
| 2 | rest | 0 | -3.3 | 0.0 | 0.00 | 194 |
| 2 | grip_light | 0.2 | 1.1 | 18.4 | 0.00 | 160 |
| 2 | grip_medium | 0.5 | 14.4 | 176.8 | 1.00 | 120 |
| 2 | grip_max | 1.0 | 15.9 | 211.2 | 1.00 | 124 |

Timing check: host lag behind the device ~1 ms at every cue (p95 5 ms, one 688 ms blip), so the
cue labels sit where the cues were shown. The person's activity (CH1 > 3x floor) started 1-3 s
BEFORE the cue (during the countdown) and lasted 2-4 s past the cue end; one max grip started
2.8 s late. Cue labels = when asked, not when done; measured activity windows are needed (EMG
onset now, camera later). The countdown should say "get ready, don't move" before "GO".
