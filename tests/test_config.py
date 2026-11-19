"""Config layering: a file can build on another with `base:`; lists are replaced, not merged."""

import pytest

from emgmirror.config import REPO_ROOT, load_config


def test_one_channel_config_builds_on_the_three_channel_one():
    one = load_config(REPO_ROOT / "configs" / "esp32_candy_1ch.yaml")
    three = load_config(REPO_ROOT / "configs" / "esp32_candy.yaml")
    assert [c["name"] for c in one["channels"]] == ["ch1"]
    assert [h["gpio"] for h in one["hardware"]["channels"]] == [34]
    assert [h["gpio"] for h in three["hardware"]["channels"]] == [34, 39, 36]
    assert one["acquisition"] == three["acquisition"]  # inherited unchanged
    assert one["hardware"]["board"] == three["hardware"]["board"]
    assert "base" not in one


def test_base_chain_and_loop_detection(tmp_path):
    (tmp_path / "a.yaml").write_text("acquisition: {fs: 1234}\nchannels: [{name: x, arm: left, muscle: flexors}]\n")
    (tmp_path / "b.yaml").write_text("base: a.yaml\nacquisition: {mains_hz: 60}\n")
    c = load_config(tmp_path / "b.yaml")
    assert c["acquisition"]["fs"] == 1234 and c["acquisition"]["mains_hz"] == 60
    assert c["acquisition"]["serial_baud"] == 500000  # from default.yaml underneath
    assert c["channels"] == [{"name": "x", "arm": "left", "muscle": "flexors"}]
    (tmp_path / "a.yaml").write_text("base: b.yaml\n")
    with pytest.raises(ValueError, match="loop"):
        load_config(tmp_path / "b.yaml")


def test_guided_recording_stops_on_a_safety_abort(cfg):
    from emgmirror.acquisition.pipeline import Acquisition
    from emgmirror.acquisition.registry import open_source
    from emgmirror.record import RecordingAborted, record_block

    acq = Acquisition(open_source("emulator:emg,emg,emg,emg", cfg), cfg)
    acq.abort_reason = "mains (AC) power connected during the recording"
    with pytest.raises(RecordingAborted, match="mains"):
        record_block(acq, cfg)
    assert not acq.started  # the source was stopped on the way out


def test_power_check_reports_ac(monkeypatch):
    from emgmirror.acquisition import safety

    monkeypatch.setattr(safety, "ac_power_connected", lambda: True)
    assert "AC" in safety.check_before_person_recording()[0]
    monkeypatch.setattr(safety, "ac_power_connected", lambda: False)
    assert safety.check_before_person_recording() == []
