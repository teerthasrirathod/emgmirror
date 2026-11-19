# Test results

Results are kept in two folders so that a simulated pass is never mistaken for a hardware pass.

- `simulated/`: software and simulated-hardware runs: the pytest suite (JUnit XML), the
  emulator-based tests and benchmarks. **No board was involved.**
- `physical/`: runs against the real ESP32 on a stated port and date, with the firmware that was
  on the board at the time. These files come from `emgmirror acq probe --out`,
  `scripts/read_pin_survey.py --out` and `tests/physical` (which writes here on its own).

No result in either folder involves electrodes on a person. That stays blocked by
`docs/ELECTRICAL_SAFETY.md`.

| File | What | Firmware on the board |
|---|---|---|
| `physical/2026-11-16_pin_survey.json` | read-only pin voltages, 49 rows | esp32_pin_survey (structured CSV) |
| `physical/2026-11-16_probe_emgm2_on_pin_survey_firmware.json` | EMGM/2 handshake refused as designed | esp32_pin_survey |
| `simulated/2026-11-16_pytest_full.xml` | full pytest suite: 409 passed, 1 failed (timing flake, fixed), 4 skipped | none (no board) |
| `simulated/2026-11-16_pytest_acquisition_rerun.xml` | acquisition and pipeline tests after the fix: 53 passed | none (no board) |
| `simulated/2026-11-16_bench_emgm2.txt` | host decoding throughput | none (no board) |
| `simulated/2026-11-16_pytest_v21.xml` | all non-CT tests after firmware 2.1 support: 63 passed | none (no board) |
| `physical/2026-11-16_pinscan_plugged.json` | driven/floating scan, connector on (first time): GPIO36 driven | emg_esp32_v2 2.1.0 |
| `physical/2026-11-16_pinscan_unplugged.json` | same, connector off: every pin floating | emg_esp32_v2 2.1.0 |
| `physical/2026-11-16_pinscan_replugged.json` | same, connector back on D34/VN/VP: GPIO34 driven | emg_esp32_v2 2.1.0 |
| `physical/2026-11-16_probe_v2.1_30s.json` | 30 s stream check: 60,200 samples, 0 missing | emg_esp32_v2 2.1.0 |
| `physical/2026-11-16_pytest_physical.xml` + `*_pytest_*.json` | tests/physical: 4 passed | emg_esp32_v2 2.1.0 |
| `physical/2026-11-16_record_5min.json`, `*_raw_inspect_5min.json` | 5 min raw recording: 600,100 samples, 0 missing, 2000.004 Hz | emg_esp32_v2 2.1.0 |
