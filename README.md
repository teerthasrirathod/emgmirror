# emgmirror

**Use a healthy forearm as the live reference for a recovering one.**

`emgmirror` records surface EMG (sEMG) from both forearms during the same tasks and expresses the
recovering (left) arm as a percentage of the healthy (right) arm, muscle group by muscle group. It
shows the result on a 3D forearm and a cross-section, live or from a recording.

It is a first proof of concept, built before product design. The whole pipeline (acquisition,
storage, processing, analysis, visualisation, API, dashboard) runs today on **synthetic data from a
motor-unit simulator**. When the EMG kit arrives, you record. Nothing else needs to change.

> Research prototype. Not a medical device and not a diagnosis. Discuss any findings with your
> surgeon or physiotherapist.

## The two questions, and what sEMG can and cannot answer

| Question | What the pipeline measures | Confidence |
|---|---|---|
| **1. Visualise the left forearm relative to the right** | Activation: noise-corrected EMG amplitude in each muscle group's strongest task, left ÷ right. Recruitment curve on a slow ramp. Turns/amplitude and spectral differences. Live left ÷ right ratio while both arms move. | Good, as long as electrode placement is mirrored carefully |
| **2. How many motor units fire in the left arm relative to the right** | Effective firing motor units: a cumulant (Campbell's theorem) estimator on a light steady grip, plus experimental spike sorting. | **Experimental.** See [validation](#validation) |
| Muscle and bone *shape* | Template anatomy (radius, ulna, 10 muscles) scaled to tape-measure lengths and girths, with left muscle bulk from girth. | It is a model, not imaging. Ultrasound thickness can be plugged in per muscle |

Why question 2 is hard: after a nerve injury, surviving motor units adopt orphaned muscle fibres
and grow (collateral reinnervation). *Fewer but bigger* units can produce close-to-normal EMG
amplitude, so amplitude alone overestimates how many units are working. The simulator models this
on purpose, and the validation shows by how much each estimator is fooled.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"            # editable install from the clone: emgmirror reads configs/ and
                                   # writes data/ and results/ next to src/, so a plain `pip install .`
                                   # or `pip install git+...` is not supported

emgmirror simulate                 # synthetic two-arm session (mirror mode)
emgmirror simulate --mode sequential
emgmirror analyze <session-id>     # prints the summary, writes report.html
emgmirror validate                 # estimators vs simulator ground truth
emgmirror experiment all           # effort / movements / rotation / noise / duration studies
emgmirror calibrate train --jobs 2 # engine that fits the simulator to a recording
emgmirror serve                    # dashboard at http://127.0.0.1:8000
pytest
```

Sessions live in `data/sessions/` (git-ignored: recordings are personal health data).

## Real hardware (ESP32 + 3 channels)

The real-hardware path is documented in [docs/ACQUISITION_SETUP.md](docs/ACQUISITION_SETUP.md)
(flash, probe, the `/diagnostics` page, raw recording, conversion to sessions) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The board, the channel map and what is still
unverified are in [docs/HARDWARE_INVENTORY.md](docs/HARDWARE_INVENTORY.md).
**Electrodes on a person are blocked** until [docs/ELECTRICAL_SAFETY.md](docs/ELECTRICAL_SAFETY.md)
passes.

## When the sensors arrive (text-protocol firmware)

1. **Flash the firmware**: open `firmware/emg_stream/emg_stream.ino` in the Arduino IDE and set
   `NUM_CHANNELS` (2 or 4). Use the EMG board's **raw** output, not an envelope output.
   For 3x Muscle BioAmp Candy on an ESP32, flash `firmware/emg_candy_esp32/` instead
   (DMA-timed sampling, 4x oversampling, eFuse-calibrated values).
2. **Configure**: copy `configs/default.yaml` to `configs/me.local.yaml` (git-ignored). Set
   `adc_bits`, `adc_vref`, `amp_gain` from your board and your tape measurements under `subject`.
   The `channels` list must have exactly as many entries as the firmware sends (`NUM_CHANNELS`):
   `default.yaml` lists 4 (both arms, mirror mode), so for sequential mode with 2 channels keep only
   the two channels of one arm (e.g. `R_flex`, `R_ext`); `--arm` relabels them for the arm you record.
   A mismatch is reported (`channel count mismatch`) and nothing is saved.
3. **Find the port**: `emgmirror ports`
4. **Record**:
   - *Sequential* (2 channels, one arm at a time; a config with those 2 channels, see step 2):
     ```bash
     emgmirror --config configs/me.local.yaml record --port COM5 --arm right --session day1
     emgmirror --config configs/me.local.yaml record --port COM5 --arm left  --session day1
     ```
   - *Mirror* (4 channels, both arms at once):
     ```bash
     emgmirror --config configs/me.local.yaml record --port COM5 --session day1
     ```
5. **Analyse**: `emgmirror --config configs/me.local.yaml analyze day1`. Or run `serve`, pick the
   session, or enter the COM port and press *Start live mirror*.

See [docs/RECORDING.md](docs/RECORDING.md) for electrode placement and safety. Mirrored placement
matters more than anything else in this project.

## Architecture

```
firmware/emg_stream/        Arduino/ESP32 sketch: N analog channels -> USB serial CSV
firmware/emg_candy_esp32/   ESP32 + 3x BioAmp Candy, DMA sampling at 2 kHz, same output format
configs/default.yaml        channels, protocol, subject measurements, simulator settings
src/emgmirror/
  acquisition/base.py       DataSource interface: raw values, pin voltage, mV estimate, provenance
  acquisition/esp32.py      ESP32 over EMGM/2 (firmware/emg_esp32_v2): indexed, loss-detecting
  acquisition/emulator.py   software ESP32 with fault injection (no hardware needed)
  acquisition/sources.py    VirtualSource, ReplaySource, legacy text SerialSource
  acquisition/pipeline.py   common pipeline: monitor + raw recorder + mV stream for live/record
  acquisition/monitor.py    five-stage diagnostics (connection, device, acquisition, channels, EMG)
  recording/                append-only raw recordings, validated layer, conversion to sessions
  simulate/                 Fuglevand-style motor-unit pool simulator + left-arm injury model
  storage/session.py        session folder format (JSON metadata + NPZ blocks)
  protocol.py               task sequence shared by simulator and recorder
  record.py                 guided terminal recording
  processing/               filters (offline zero-phase, live causal) and sEMG features
  analysis/bilateral.py     left-vs-right metrics, recruitment, summary
  analysis/motor_units.py   firing-motor-unit estimators (experimental)
  analysis/validate.py      sweep the injury model, score estimators against ground truth
  simulate/movements.py     5 muscle groups, 17 movements and physio exercises, forearm rotation
  simulate/frontend.py      recording chain: motion, mains, amplifier noise, anti-alias filter, ADC
  experiments/              synthetic studies + HTML report (docs/EXPERIMENTS.md)
  calibration/engine.py     simulation-based calibration: real recording -> simulator settings
  anatomy/                  parametric forearm geometry, 3D and cross-section figures
  live.py                   streaming left/right ratio for the live mirror
  report.py                 standalone HTML report
  server/                   FastAPI REST + WebSocket backend, single-page dashboard
tests/                      pytest suite (runs on synthetic data, no hardware)
```

Every source produces the same `Session` structure, so synthetic, replayed and real data go through
the same code path.

### Session format

```
data/sessions/<id>/
  session.json   source, fs, subject, blocks[{channels, trials[{task,start_s,end_s,level}]}], ground_truth (synthetic only)
  block_000.npz  data: float32 (n_samples, n_channels) in mV; optional force
  analysis.json  cached analysis
  report.html
```

## Validation

`emgmirror validate` sweeps the simulated injury from healthy (100% of motor units) to severe (20%)
and compares each headline number with the simulator's ground truth. See
[docs/VALIDATION.md](docs/VALIDATION.md) for the current results and how to read them. Re-run it
whenever an estimator, the protocol or the channel count changes.

## Experiments and calibration

[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) records what the synthetic studies found: expected signal per effort and
movement, where to put 2–3 electrodes, how much forearm rotation distorts readings, which noise
pipeline to use, how long to hold, and how the calibration engine matches the simulator to real
recordings. `configs/extended_protocol.yaml` adds rotations and physio exercises to a recording.

## Roadmap

- [ ] Record day 1 with the real kit; check noise floor and placement repeatability (right arm twice)
- [ ] "Live left vs recorded right": replay a stored right-arm session as the reference while the
      left arm is recorded live, for 2-channel kits
- [ ] Optional grip dynamometer (load cell + HX711) as a force channel
- [ ] More channels / a high-density grid for real motor-unit decomposition
- [ ] Ultrasound muscle thickness per muscle (`muscle_overrides`) for patient-specific shape
- [ ] Longitudinal tracking: one session per week, trend of the left ÷ right ratios
- [ ] Public dataset loaders for algorithm benchmarking

## Contributing

Issues and pull requests are welcome. Keep the processing code free of hardware assumptions (add a
`Source` instead), keep personal recordings out of git, and add a test with synthetic data for any
new metric.

## License

Apache License 2.0. See [LICENSE](LICENSE).
