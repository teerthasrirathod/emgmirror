# Architecture

Part 1 describes the system as it was found on 2026-11-16 (git commit `5258398`, "Baseline").
Part 2 describes it after the real-hardware acquisition work on branch `acquisition-foundation`.

## Part 1: Existing system (baseline)

`emgmirror` compares a recovering forearm with the healthy one using surface EMG. Everything below
was working on virtual data: 370 tests passed at the baseline.

```
simulate/  (motor-unit simulator) ──► Session (data/sessions/<id>/) ◄── record.py ◄── SerialSource (text CSV)
                                          │                                              ▲
                                          ▼                                              │
              processing/ (filters, features) ──► analysis/ (bilateral, motor units) ──► report.py, anatomy/
                                          │
                          server/app.py (REST + /ws/live) ──► static/index.html (dashboard)
```

| Area | Modules | What it does | Preserved? |
|---|---|---|---|
| Configuration | `config.py`, `configs/*.yaml` | defaults deep-merged with a personal file | yes; new keys added |
| Storage | `storage/session.py` | Session → blocks (float32 mV npz) + trials + subject + ground truth | yes; optional `provenance` added, old files load unchanged |
| Simulator | `simulate/{motor_units,synthetic,frontend,movements}.py` | motor-unit pools, injury model, synthetic sessions that look like recordings | unchanged |
| Protocol | `protocol.py` | task list and target profiles shared by simulator and recorder | unchanged |
| Processing | `processing/filters.py`, `features.py` | zero-phase and streaming band-pass, notch or regression mains removal, envelope, features | unchanged |
| Analysis | `analysis/bilateral.py`, `motor_units.py`, `validate.py` | left-as-%-of-right activation, recruitment, motor-unit estimates (experimental), validation against ground truth | unchanged |
| Calibration engine | `calibration/engine.py` | fits the simulator to recordings | unchanged |
| Experiments | `experiments/` | synthetic studies | unchanged |
| Visualisation | `anatomy/`, `report.py`, `server/static/index.html` | 3D forearm, cross-section, live mirror, HTML report | unchanged (one link added) |
| Live | `live.py` | streaming filter + left/right ratio from any `Source.read()` | unchanged |
| Acquisition | `acquisition/sources.py` | `ReplaySource`; `SerialSource` for the text firmware (one CSV line per sample, no index) | kept; refactored onto the new interface |
| Recorder | `record.py` | guided protocol; saves one Session block | kept; now runs through the common pipeline |
| Firmware | `firmware/emg_stream`, `emg_candy_esp32`, `esp32_rig_check`, `esp32_pin_survey`, mic sketches | text protocol and bench/diagnostic sketches | unchanged |
| CT | `ct/`, `scripts/ct_inventory.py` | clinical CT → 3D models (separate track) | untouched |

**Assumptions the downstream code makes** (checked before connecting real data):

- Data arrives as `(n, channels)` float **millivolts** at a constant `fs`, with no gaps. The filters
  carry state between chunks, and the analysis cuts trials by `start_s * fs`. A dropped sample
  therefore silently shifts everything after it.
- `fs` is fixed when the filters are built (`LiveMirror.__init__`).
- `Channel(name, arm, muscle)`. Analysis pairs channels by muscle across arms.
- `session.source == "synthetic"` switches on the synthetic warnings and the ground-truth checks.

**Gaps found in the baseline acquisition path:**

- The text protocol has no sample index or checksum. Lost or corrupted lines were dropped silently
  (only a "malformed lines" counter), so recordings could be shorter than real time without anyone
  noticing.
- Raw ADC counts were converted to mV on arrival and only mV was stored. The original data and the
  conversion behind it were not kept.
- Nothing distinguished "port open", "device identified", "samples flowing" and "EMG present".
- Serial handling lived in `sources.py`, but `server/app.py` and `cli.py` built `SerialSource`
  directly.

## Part 2: Updated architecture

```
 VIRTUAL  (VirtualSource / ReplaySource)  ─┐
 ESP32    (Esp32Source  ← EMGM/2 ← firmware/emg_esp32_v2) ─┤
 EMULATOR (Esp32Source  ← Esp32Emulator) ─┤        acquisition/registry.py: one spec string
 LEGACY   (SerialSource ← text firmware) ─┘        "virtual" | "replay:<id>" | "esp32:COM9" | "emulator" | "text:COM9"
                     │
                     ▼  DataSource.read_chunks() → Chunk(index, raw, valid, pin_mv, mv, t_device_us, run, gap_before)
          acquisition/pipeline.py  Acquisition.poll()
                     │
        ┌────────────┼──────────────────────────┬───────────────────────────────┐
        ▼            ▼                          ▼                               ▼
  StreamMonitor   RawRecorder              Acquisition.read()             server/acq.py
  (5-stage        data/raw/<id>/           mV, gaps re-inserted           /api/acq/*, /ws/acq
   diagnostics)   append-only              as logged fill                 /diagnostics page
                     │                          │
                     ▼                          ▼
          recording/derive.py           live.py LiveMirror, record.py (unchanged algorithms)
          data/derived/<id>/                    │
          (validated, integrity)                ▼
                     │                   Session (data/sessions/<id>/) + provenance
                     └──────────────► analysis/, report.py, dashboard (unchanged)
```

Design rules:

1. **Hardware is reachable only through `DataSource`.** No module outside `acquisition/` opens a
   port. The server and CLI go through `registry.open_source`.
2. **Raw stays raw.** `Chunk.raw` holds the device's own values (for the ESP32, sums of 12-bit
   codes) and is the only thing the raw recorder stores. Pin voltage and the electrode-referred
   estimate are derived from it, and `SourceInfo.calibration` states how. The estimate uses a
   nominal gain and an assumed bias, and every layer labels it that way.
3. **Losses are counted, never hidden.** The sample index is device-assigned. The cause of each
   gap is attributed (device or link). `Acquisition.read()` keeps the time base by inserting
   explicit 0 mV fill and logging each fill. `to_session` refuses gaps unless asked, and lists each
   fill in the provenance.
4. **Layers are separate.** raw (`data/raw`) → validated (`data/derived`) → processed (Session in
   `data/sessions`) → features and analysis (`analysis.json`, reports). Only the raw layer is
   append-only, and a raw recording directory is never reused.
5. **Diagnostics are staged.** Connection → device → acquisition → channel signals → EMG. "EMG"
   is never claimed for real hardware automatically. It stays "not verified" while the safety gate
   is blocked.
6. **The smallest change to working code.** The filters, analysis, simulator, live mirror and
   report are untouched. `Acquisition` exposes the old `Source` surface (`fs`, `channels`, `start`,
   `read`, `stop`), so the live mirror and the guided recorder run on it unchanged.

### Data schemas

**Chunk** (`acquisition/base.py`): `index` int64 (n,), `raw` (n, ch) in the source dtype, `valid`
bool (n, ch), `mv` float64 (n, ch) (NaN where invalid), `pin_mv` float64 (n, ch) or None,
`t_host` (s, epoch), `t_device_us`, `run`, `seq`, `flags`, `gap_before`, `gap_cause`.

**SourceInfo**: `kind`, `label`, `fs_nominal`, `channels`, `is_real_hardware`, `raw_dtype`,
`raw_units`, `device` (the HELLO fields for the ESP32), `calibration`, `hardware`.

**Raw recording** (`recording/raw.py`, format `emgmirror-raw/1`):

```
data/raw/<YYYYmmdd-HHMMSS_label>/
  manifest.json   format, recording_id, status (recording|complete|interrupted), started/stopped,
                  source (SourceInfo), sample_record dtype, metadata (label, arm, notes),
                  config snapshot (acquisition + hardware), totals
  samples.bin     records of [index:<u8, raw:<dtype>[nch]], little-endian, appended
  frames.csv      run, seq, first_index, n, t_device_us, t_host_ns, flags, gap_before, gap_cause
  events.jsonl    {"t", "event": start|stop|gap|marker|source_status, ...}
```

Read it with `RawRecording.open(dir)`. `.integrity()` re-derives gaps, completeness and rates from
the stored indices. `.to_pin_mv()` and `.to_mv()` apply the conversion recorded in the manifest.

**Session provenance** (`session.json → provenance`): `raw_recording`, `device`, `calibration`,
`is_real_hardware`, `integrity` (missing samples, completeness, rates), `fills`, and per-block
entries written by the guided recorder.

### Where things are

| Concern | File |
|---|---|
| Wire protocol (host) | `src/emgmirror/acquisition/emgm2.py` |
| Wire protocol (device) | `firmware/emg_esp32_v2/emg_esp32_v2.ino` |
| Protocol specification | `docs/ACQUISITION_PROTOCOL.md` |
| Source interface | `src/emgmirror/acquisition/base.py` |
| ESP32 source | `src/emgmirror/acquisition/esp32.py` |
| Emulator (fault injection) | `src/emgmirror/acquisition/emulator.py` |
| Virtual, replay, legacy text | `src/emgmirror/acquisition/sources.py` |
| Source specs | `src/emgmirror/acquisition/registry.py` |
| Common pipeline | `src/emgmirror/acquisition/pipeline.py` |
| Diagnostics | `src/emgmirror/acquisition/monitor.py` |
| Driven-vs-floating pin scan | `src/emgmirror/acquisition/pinscan.py` (`emgmirror acq scan`) |
| Raw recording | `src/emgmirror/recording/raw.py` |
| Validated layer, Session conversion | `src/emgmirror/recording/derive.py` |
| Server acquisition API | `src/emgmirror/server/acq.py` |
| Diagnostics page | `src/emgmirror/server/static/diagnostics.html` |
| Hardware config | `configs/esp32_candy.yaml` (`hardware:` section) |
| CLI | `src/emgmirror/cli.py` (`acq`, `raw`, `record`) |
