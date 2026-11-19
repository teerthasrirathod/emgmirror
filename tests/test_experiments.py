import numpy as np
import pytest

from emgmirror.calibration import engine
from emgmirror.experiments import suite
from emgmirror.processing.filters import PIPELINES, pipeline, remove_mains_regression
from emgmirror.simulate.frontend import FrontEnd, record
from emgmirror.simulate.movements import GROUPS, MOVEMENTS, simulate_movement


def test_frontend_quantises_and_clips():
    fe = FrontEnd(gain=1000, adc_bits=10, adc_vref=5.0, mains_uv=0, wander_uv=0, motion_uv=0, amp_noise_uv=0)
    assert fe.lsb_uv == pytest.approx(4.8828, rel=1e-3)
    x = np.concatenate([np.zeros(2000), np.full(2000, 10.0)])  # 10 mV: beyond the ±2.5 mV range
    rec, _ = record(x, 2000.0, fe, None, np.random.default_rng(0))
    assert rec.max() == pytest.approx(fe.range_mv)
    assert len(rec) == 2000  # decimated 2 kHz -> 1 kHz


def test_mains_regression_keeps_emg_power():
    fs = 1000
    rng = np.random.default_rng(1)
    emg = 0.1 * rng.standard_normal(10 * fs)
    t = np.arange(len(emg)) / fs
    x = emg + 0.3 * np.sin(2 * np.pi * 50.02 * t) + 0.1 * np.sin(2 * np.pi * 150.06 * t + 1)
    y = remove_mains_regression(x, fs, 50.0)
    assert np.std(y) == pytest.approx(np.std(emg), rel=0.03)


def test_pipelines_run():
    x = np.random.default_rng(2).standard_normal((3000, 2))
    for p in PIPELINES:
        assert pipeline(x, 1000.0, p).shape == x.shape


def test_movement_library_drives_expected_groups():
    sim = simulate_movement("pronation_hold", 0.6, 2.0, 2000.0, seed=3, mu_per_muscle=60)
    truth = sim["truth"]
    assert truth["pronators"]["active_pct_of_alive"] > truth["supinators"]["active_pct_of_alive"]
    assert sim["clean"].shape[1] == len(GROUPS)
    assert {m.category for m in MOVEMENTS.values()} == {"isometric", "rotation", "exercise"}


def test_rotation_reduces_flexor_signal(cfg):
    r = suite.run_rotation(cfg, mu=50, duration_s=2.0, progress=lambda s: None)
    assert r["relative_pct"]["-90"]["flexors"] < r["relative_pct"]["90"]["flexors"]


def test_calibration_engine_roundtrip(tmp_path):
    fe = FrontEnd().__dict__
    bank = engine.build_bank(12, fe, seed=1, mu=60, progress=lambda s: None)
    cal = engine.Calibrator(sorted(bank[0]["features"])).fit(bank[:10])
    rep = cal.evaluate(bank[10:])
    assert set(rep) == {"mu_fraction", "enlargement", "drive_max", "electrode_gain", "amp_noise_uv"}
    cal.save(tmp_path / "c.joblib")
    pred = engine.Calibrator.load(tmp_path / "c.joblib").predict([bank[0]["features"]])[0]
    m = pred["mu_fraction"]
    assert m["low"] <= m["estimate"] <= m["high"]


def test_frontend_from_rest_measures_noise():
    fe = FrontEnd(amp_noise_uv=6.0, mains_uv=40.0, motion_uv=0)
    rec, _ = record(np.zeros(16000), 2000.0, fe, None, np.random.default_rng(4))
    est = engine.frontend_from_rest(rec, fe.fs_out, 50.0)
    assert 3 < est["amp_noise_uv"] < 10
    assert 20 < est["mains_uv"] < 70


def test_paired_engine_roundtrip(tmp_path):
    fe = FrontEnd().__dict__
    bank = engine.build_pairs(6, fe, seed=2, mu=50, progress=lambda s: None)
    cal = engine.PairedCalibrator(sorted(bank[0]["f_left"])).fit_pairs(bank[:4])
    rep = cal.evaluate_pairs(bank[4:])
    assert "paired_engine_mu_ratio" in rep
    cal.save(tmp_path / "p.joblib")
    m = engine.PairedCalibrator.load(tmp_path / "p.joblib").predict_pairs([(bank[0]["f_left"], bank[0]["f_right"])])[0]
    assert m["mu_fraction"]["low"] <= m["mu_fraction"]["high"]
    assert 0.1 <= m["mu_fraction"]["estimate"] <= 1.0
