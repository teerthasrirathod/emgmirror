# Signal viewer

```
emgmirror --config configs/esp32_candy_2ch.yaml view          # http://127.0.0.1:8030/ (this computer only)
```

One screen for recorded and live sessions: the raw recordings in `data/raw` on a scrollable, zoomable
timeline, with envelope, detected activity, spectrum, statistics and the CT forearm in sync. Personal
health data: it is served on 127.0.0.1 only and never published.

## Live

The viewer reads the raw recording files directly. It never talks to the acquisition process:

```
terminal 1:  emgmirror --config configs/esp32_candy_2ch.yaml acq record --source esp32:COM9 --seconds 0 --arm right
browser:     http://127.0.0.1:8030/  ->  "Newest"
```

The recorder appends to `samples.bin` and pushes it to the OS at least every 50 ms. The viewer polls
every 100 ms for the rows it has not seen (`/api/rec/<id>/data?row=N`). If the viewer is slow or
closed, the recording is not affected. "Data age" in the header is the time since the recorder
received the newest chunk.

- Measured 2026-11-17 with the emulator through the viewer server: median 125 ms, p90 231 ms, max 293 ms.
- Before the recorder flushed every 50 ms, it was 230–680 ms.

While a recording is open, the browser filters causally:
- 4th-order Butterworth band-pass 75–450 Hz.
- Notches at 50 Hz and its harmonics.
- The envelope uses a trailing window.

When the recording closes, the view switches to the offline cleaning used for the dataset: zero-phase
band-pass plus mains regression. The numbers then match `trials.csv`.

## Features

The feature list comes from what comparable tools offer.

| Feature | Comparable in |
|---|---|
| Scroll and drag through time; Ctrl+scroll or two-finger pinch to zoom; double-click for the whole recording | SigViewer and EDFbrowser time navigation |
| Overview strip of the whole recording with a draggable window | Clinical EEG/EMG review tools |
| Min/max decimation per pixel, full-resolution samples when zoomed in | plotly-resampler (predict-idlab) |
| Window presets 1 s–60 s, keyboard (Space, ←/→, +/−, Home, End, Esc) | EDFbrowser, SigViewer |
| Filtered and raw-pin views; per-channel gain (scroll on the channel label) | OpenBCI GUI time series |
| RMS envelope lane relative to the room floor, with threshold lines | Upside Down Labs Chords (envelope), Noraxon MR3 |
| Detected activity periods (onset/offset with hysteresis), with onset compared with the cue | Noraxon MR3 / myoMUSCLE timing analysis |
| Spectrogram lane and Welch spectrum with median and mean frequency, mains lines marked | OpenBCI FFT widget, Noraxon frequency analysis |
| Statistics for the selection or the visible window: RMS, net RMS, SNR, MAV, peak, P2P, median/mean frequency, % active, % of recording max | Noraxon EMG analysis reports |
| Trials table from the dataset (click to zoom), cue bands, events (markers, gaps) | Noraxon periods and markers, SigViewer annotations |
| "Floor from selection": set the baseline from a rest stretch | Baseline/offset tools in MR3 |
| Export the selection as CSV | Noraxon ASCII/Excel export |
| Playback at 0.25–4×, loop a selection, CT forearm coloured at the cursor time | — |
| Live: follow mode, LIVE badge, data age, missing and invalid sample counts | OpenBCI GUI status |
| mV at the pin and µV at the skin (estimated, nominal gain) | — |

## Checked

`tests/test_viewer.py`:
- Known answers for the envelope, activity detection and statistics.
- Incremental reading of a growing recording, including lost samples and a device restart.
- The HTTP API: live, then closed, the offline cleaning, and unsafe names rejected.
- The browser maths (`static/emgcore.js`, run in Node) give the same envelope, activity periods and RMS
  as the Python reference.
- The live filter's response: mains notched, pass band flat between harmonics.

## Limits

- **Two channels are two regions on the CT,** not single muscles.
- **µV at the skin uses the nominal Candy gain (2420×),** which has not been measured.
- **"% of recording max" is not %MVC** unless a maximum contraction was recorded.
- **The live filter (causal notches) and the offline cleaning (regression) differ slightly.** The
  offline numbers are the reference.
- **The spectrogram is computed only for windows of 2 minutes or less.**
