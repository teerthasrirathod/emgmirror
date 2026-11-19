import numpy as np
import pytest

from emgmirror.acquisition.sources import ReplaySource, counts_to_mv, parse_line
from emgmirror.analysis.bilateral import analyze
from emgmirror.anatomy.figure import cross_section_figure, forearm_figure, pct_color
from emgmirror.anatomy.forearm import soft_tissue_scale
from emgmirror.live import LiveMirror
from emgmirror.processing.filters import StreamingFilter, clean
from emgmirror.simulate.motor_units import make_pool
from emgmirror.storage import Session


def test_filter_removes_mains_and_dc():
    fs = 1000
    t = np.arange(4 * fs) / fs
    x = 0.5 + 0.2 * np.sin(2 * np.pi * 50 * t) + 0.05 * np.sin(2 * np.pi * 120 * t)
    y = clean(x, fs, (20, 450), 50)[fs:-fs]
    assert abs(y.mean()) < 1e-3
    assert np.std(y) == pytest.approx(0.05 / np.sqrt(2), rel=0.15)


def test_streaming_filter_matches_shape():
    f = StreamingFilter(1000, 3)
    y, env = f.process(np.random.default_rng(0).standard_normal((50, 3)))
    assert y.shape == (50, 3) and env.shape == (3,)


def test_injury_model_removes_and_enlarges_units():
    healthy = make_pool("flexors", 100, seed=1)
    hurt = make_pool("flexors", 100, seed=1, impairment={"mu_fraction": 0.4, "enlargement": 2.0})
    assert healthy.n_alive == 100
    assert 25 < hurt.n_alive < 55
    assert hurt.muap_amp[hurt.alive].mean() > 1.5 * healthy.muap_amp[hurt.alive].mean()


def test_session_roundtrip(tmp_path, mirror_session):
    folder = mirror_session.save(tmp_path)
    back = Session.load(folder)
    assert back.fs == mirror_session.fs
    assert len(back.blocks) == 1
    assert back.blocks[0].data.shape == mirror_session.blocks[0].data.shape
    assert [t.task for t in back.blocks[0].trials] == [t.task for t in mirror_session.blocks[0].trials]
    assert back.ground_truth is not None


def test_analysis_detects_weaker_left_flexors(cfg, mirror_session):
    res = analyze(mirror_session, cfg)
    s = res["summary"]["flexors"]
    assert s["activation_pct"] < 90
    gt = res["ground_truth_check"]["flexors"]
    assert gt["true_mu_pool_pct"] < 60
    assert set(res["recruitment"]["flexors"]) == {"left", "right"}


def test_sequential_and_mirror_agree(cfg, mirror_session, sequential_session):
    a = analyze(mirror_session, cfg)["summary"]["flexors"]["activation_pct"]
    b = analyze(sequential_session, cfg)["summary"]["flexors"]["activation_pct"]
    assert a == pytest.approx(b, rel=1e-6)  # same simulated signals, different packaging


def test_replay_merges_sequential_blocks(cfg, sequential_session):
    src = ReplaySource(sequential_session)
    assert {c.arm for c in src.channels} == {"left", "right"}
    assert src.data.shape[1] == 4


def test_live_mirror_reports_ratio(cfg, mirror_session):
    src = ReplaySource(mirror_session)
    live = LiveMirror(src, cfg)
    fs = int(src.fs)
    # Feed the whole recording in 50 ms chunks without waiting in real time.
    ratios = []
    for i in range(0, len(src.data), fs // 20):
        chunk = src.data[i: i + fs // 20]
        src.read = lambda c=chunk: c
        msg = live.step()
        if msg and msg["ratio_pct"]["flexors"] is not None:
            ratios.append(msg["ratio_pct"]["flexors"])
    assert ratios, "ratio never became available"
    assert 30 < np.median(ratios) < 110


def test_serial_parsing():
    assert parse_line("512,498\n", 2) == [512.0, 498.0]
    assert parse_line("# emgmirror fs=1000", 2) is None
    assert parse_line("12,abc", 2) is None
    assert parse_line("1,2,3", 2) is None
    mv = counts_to_mv(np.array([512.0, 1023.0]), bits=10, vref=5.0, gain=1000.0)
    assert mv[0] == pytest.approx(0.0)
    assert mv[1] == pytest.approx(2.495, rel=1e-3)


def test_anatomy_scales_with_girth(cfg):
    assert soft_tissue_scale(cfg["subject"]["left"], cfg["subject"]["right"]) < 1.0
    fig, meta = forearm_figure(cfg, {"flexors": {"activation_pct": 50.0}})
    left_flex = [i for i, m in enumerate(meta) if m["arm"] == "left" and m["group"] == "flexors"]
    assert left_flex and all(fig.data[i].color == pct_color(50.0) for i in left_flex)
    assert len(cross_section_figure(cfg).data) > 10


def test_pct_color_endpoints():
    assert pct_color(None) == "#8c8c8c"
    assert pct_color(-10) == pct_color(0)
    assert pct_color(500) == pct_color(110)
