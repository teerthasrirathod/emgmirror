"""Dataset sessions: metrics with a known answer, and the full session flow on the emulator."""

import csv
import json

import numpy as np
import pytest
import yaml

from emgmirror import dataset
from emgmirror.acquisition.base import Chunk, SourceInfo
from emgmirror.config import REPO_ROOT, load_config
from emgmirror.recording import RawRecorder, RawRecording
from emgmirror.storage import Channel


def esp_info(nch=2):
    return SourceInfo(kind="esp32", label="t", fs_nominal=2000.0,
                      channels=[Channel(f"ch{k + 1}", "right", "flexors") for k in range(nch)], is_real_hardware=True,
                      raw_dtype="<u2", raw_units="sum", device={"os": "4", "cal_a": "1.0", "cal_b": "0.0"},
                      calibration={"bias_mv": 1650.0, "gain": 2420.0})


def test_metrics_have_the_expected_values(tmp_path):
    """Band-limited noise of rms s at rest, 4*s during the trial on CH1 only: SNR = 12 dB, active = 1."""
    fs, rng = 2000, np.random.default_rng(3)
    n = fs * 12
    from scipy.signal import butter, sosfiltfilt

    sos = butter(4, [100, 300], "bandpass", fs=fs, output="sos")
    noise = sosfiltfilt(sos, rng.standard_normal((n, 2)), axis=0)
    noise /= noise.std(axis=0)
    amp = np.full((n, 2), 10.0)  # 10 mV rms floor at the pin
    amp[4 * fs: 9 * fs, 0] = 40.0  # 4x on CH1 during the trial
    pin = 1650 + amp * noise + 300 * np.sin(2 * np.pi * 50 * np.arange(n) / fs)[:, None]  # plus strong hum
    raw = np.round(pin * 4).astype(np.uint16)  # cal_a=1, cal_b=0, os=4: code sum = 4 * mV
    rec = RawRecorder(tmp_path, esp_info(), label="known")
    for i in range(0, n, 20):
        rec.write(Chunk(index=np.arange(i, i + 20), raw=raw[i:i + 20], valid=np.ones((20, 2), bool),
                        mv=np.zeros((20, 2)), run=1))
        if i == 4 * fs:
            rec.mark("trial_start", index=i, task="grip_max", level=1.0)
        if i == 9 * fs:
            rec.mark("trial_end", index=i, task="grip_max")
    rec.close()
    r = RawRecording.open(rec.dir)
    rows = dataset.analyse_recording(r, floor=[10.0, 10.0])
    ch1, ch2 = rows
    assert ch1["snr_db"] == pytest.approx(12.0, abs=0.6)
    assert ch1["active_fraction"] == 1.0 and ch1["peak_250ms_x_floor"] == pytest.approx(4, rel=0.15)
    assert ch2["snr_db"] == pytest.approx(0.0, abs=0.6) and ch2["active_fraction"] == 0.0
    assert 100 <= ch1["median_hz"] <= 300
    assert ch1["mains50_pin_mv"] == pytest.approx(300 / np.sqrt(2), rel=0.05)  # hum measured, not counted as EMG
    assert ch1["quality"] == "ok" and ch1["clipped_fraction"] == 0.0
    fl = dataset.floor_from_recording(r)
    assert fl["channels"][1]["floor_pin_mv"] == pytest.approx(10.0, rel=0.1)


def test_session_runs_end_to_end_on_the_emulator(tmp_path):
    cfg = load_config(REPO_ROOT / "configs" / "grip_dataset.yaml")
    cfg["protocol"] = [{"task": "rest", "kind": "hold", "level": 0.0, "duration_s": 1.0, "instruction": "relax"},
                       {"task": "grip_max", "kind": "hold", "level": 1.0, "duration_s": 1.0, "instruction": "squeeze"},
                       {"task": "rest", "kind": "hold", "level": 0.0, "duration_s": 1.0, "instruction": "relax"}]
    cfg["protocol_rest_between_s"] = 0.5
    # The emulated noise is far above the real room reference, so the floor check asks; the
    # first run declines (session stops), the second accepts.
    no = iter([""] * 8 + ["n"])  # 8 setup prompts, then the floor question
    with pytest.raises(SystemExit, match="floor check"):
        dataset.run_session(cfg, "emulator:emg,emg", "tester", "right", tmp_path / "dataset", tmp_path / "raw",
                            tmp_path / "sessions", ask=lambda q: next(no), countdown=0, floor_s=1.5)
    answers = iter([""] * 8 + ["y"])
    folder = dataset.run_session(cfg, "emulator:emg,emg", "tester", "right", tmp_path / "dataset", tmp_path / "raw",
                                 tmp_path / "sessions", ask=lambda q: next(answers), countdown=0, floor_s=1.5)
    setup = yaml.safe_load((folder / "setup.yaml").read_text())
    assert setup["device"]["pins"] == "35,36" and setup["channels"][0]["muscle_group"] == "flexors"
    assert set(setup["room_floor_pin_mv"]) == {"ch1", "ch2"}
    session = json.loads((folder / "session.json").read_text())
    assert len(session["recordings"]) == 2
    for rid in session["recordings"]:
        assert RawRecording.open(tmp_path / "raw" / rid).manifest["status"] == "complete"
    rows = list(csv.DictReader(open(folder / "trials.csv")))
    assert [(r["ch"], r["task"]) for r in rows] == [("1", "rest"), ("2", "rest"), ("1", "grip_max"), ("2", "grip_max"),
                                                   ("1", "rest"), ("2", "rest")]
    assert all(r["snr_db"] not in ("", None) for r in rows)
    index = list(csv.DictReader(open(tmp_path / "dataset" / "index.csv")))
    assert len(index) == len(rows) and index[0]["session_id"] == session["session_id"]


def test_mains_estimate_survives_off_nominal_frequency():
    fs = 2000
    t = np.arange(12 * fs) / fs
    v = 1600 + 891 * np.sin(2 * np.pi * 49.9 * t)  # 891 mV peak at 49.9 Hz, as measured on 2026-11-17
    assert dataset._mains50(v, fs, 50.0) == pytest.approx(891 / np.sqrt(2), rel=0.03)
