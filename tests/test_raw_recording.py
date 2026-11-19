"""Raw recording layer: append-only, never overwritten, recoverable after a crash, convertible to the
existing Session format without changing what the analysis computes."""

import json

import numpy as np
import pytest

from emgmirror.acquisition.base import Chunk, SourceInfo, mv_chunk
from emgmirror.analysis.bilateral import analyze
from emgmirror.recording import RawRecorder, RawRecording, list_recordings
from emgmirror.recording.derive import to_session, validate
from emgmirror.storage import Channel, Session

CH = [Channel("a", "right", "flexors"), Channel("b", "right", "extensors"), Channel("c", "right", "brachioradialis")]


def esp_info():
    return SourceInfo(kind="esp32", label="test", fs_nominal=2000.0, channels=CH, is_real_hardware=True,
                      raw_dtype="<u2", raw_units="sum of 4 codes",
                      device={"os": "4", "cal_a": "0.8", "cal_b": "10", "fw": "test"},
                      calibration={"bias_mv": 1650.0, "gain": 2420.0})


def esp_chunk(i0, n, run=1, gap=0, value=8000):
    raw = np.full((n, 3), value, dtype=np.uint16)
    raw[:, 2] = 0xFFFF
    pin = 0.8 * raw / 4 + 10
    valid = raw != 0xFFFF
    pin[~valid] = np.nan
    return Chunk(index=np.arange(i0, i0 + n), raw=raw, valid=valid, mv=(pin - 1650) / 2420, pin_mv=pin,
                 t_device_us=(i0 + n) * 500, run=run, seq=i0 // 10, gap_before=gap, gap_cause="device" if gap else "")


def test_roundtrip_preserves_raw_and_metadata(tmp_path):
    rec = RawRecorder(tmp_path, esp_info(), label="unit test", metadata={"arm": "right"},
                      config_snapshot={"acquisition": {"amp_gain": 2420.0}})
    rec.write(esp_chunk(0, 100))
    rec.mark("trial_start", task="rest", level=0.0)
    rec.write(esp_chunk(110, 50, gap=10))
    rec.close()
    r = RawRecording.open(rec.dir)
    assert r.manifest["status"] == "complete" and not r.interrupted
    assert r.manifest["source"]["device"]["fw"] == "test" and r.manifest["metadata"]["arm"] == "right"
    assert r.records["raw"].dtype == np.dtype("<u2") and (r.records["raw"][:, 0] == 8000).all()
    assert (r.records["raw"][:, 2] == 0xFFFF).all()  # invalid markers kept as sent
    integ = r.integrity()
    assert integ["gaps"] == [{"after_index": 99, "missing": 10}] and integ["missing_samples"] == 10
    assert integ["invalid_samples"] == 150 and integ["completeness"] == pytest.approx(150 / 160)
    pin = r.to_pin_mv()
    assert pin[0, 0] == pytest.approx(0.8 * 2000 + 10) and np.isnan(pin[:, 2]).all()
    assert r.to_mv()[0, 0] == pytest.approx((1610 - 1650) / 2420)
    events = [e["event"] for e in r.events]
    assert events[0] == "start" and "marker" in events and "gap" in events and events[-1] == "stop"
    assert list_recordings(tmp_path)[0]["recording_id"] == rec.recording_id


def test_existing_recording_is_never_overwritten(tmp_path):
    rec = RawRecorder(tmp_path, esp_info(), recording_id="fixed")
    rec.write(esp_chunk(0, 10))
    rec.close()
    before = (rec.dir / "samples.bin").read_bytes()
    with pytest.raises(FileExistsError):
        RawRecorder(tmp_path, esp_info(), recording_id="fixed")
    assert (rec.dir / "samples.bin").read_bytes() == before


def test_crash_leaves_a_readable_interrupted_recording(tmp_path):
    rec = RawRecorder(tmp_path, esp_info(), label="crash")
    rec.write(esp_chunk(0, 40))
    rec.flush(sync=True)
    # Simulate a crash mid-write: half a record appended, manifest never finalised.
    with open(rec.dir / "samples.bin", "ab") as fh:
        fh.write(b"\x01\x02\x03")
    with open(rec.dir / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"t": 1, "event": "gap", "ind')
    r = RawRecording.open(rec.dir)
    assert r.interrupted and r.manifest["status"] == "recording"
    assert len(r.records) == 40 and r.truncated_bytes == 3
    assert r.integrity()["truncated_bytes"] == 3


def test_validated_layer_is_separate_from_raw(tmp_path):
    rec = RawRecorder(tmp_path / "raw", esp_info())
    rec.write(esp_chunk(0, 2000))
    rec.close()
    raw_before = (rec.dir / "samples.bin").read_bytes()
    integ = validate(RawRecording.open(rec.dir), tmp_path / "derived", {"rail_low_mv": 100, "rail_high_mv": 3000,
                                                                        "flat_p2p_mv": 2.0})
    out = tmp_path / "derived" / rec.recording_id
    assert (out / "validated.npz").exists() and json.loads((out / "integrity.json").read_text())["samples"] == 2000
    assert [c["verdict"] for c in integ["channels"]] == ["flat", "flat", "no_valid_samples"]
    assert (rec.dir / "samples.bin").read_bytes() == raw_before


def test_to_session_refuses_gaps_unless_asked(tmp_path):
    rec = RawRecorder(tmp_path, esp_info())
    rec.write(esp_chunk(0, 100))
    rec.write(esp_chunk(105, 100, gap=5))
    rec.close()
    r = RawRecording.open(rec.dir)
    with pytest.raises(ValueError, match="5 samples missing"):
        to_session(r, "s")
    s = to_session(r, "s", allow_gaps=True)
    assert s.blocks[0].data.shape == (205, 3)
    assert (s.blocks[0].data[100:105] == 0).all()
    assert s.provenance["fills"] == [{"after_index": 99, "missing": 5, "filled_with": "0 mV"}]
    assert s.source == "esp32" and s.provenance["raw_recording"] == rec.recording_id


def test_virtual_data_through_raw_layer_gives_identical_analysis(tmp_path, cfg, mirror_session):
    """Processing compatibility: chunk -> raw file -> Session must not change what analysis computes."""
    block = mirror_session.blocks[0]
    info = SourceInfo(kind="virtual", label="sim", fs_nominal=mirror_session.fs, channels=block.channels,
                      is_real_hardware=False, raw_dtype="<f4", raw_units="mV (synthetic)")
    rec = RawRecorder(tmp_path / "raw", info)
    fs = mirror_session.fs
    for t in block.trials:  # markers at device sample indices, as the guided recorder writes them
        rec.event("marker", label="trial_start", index=int(round(t.start_s * fs)), task=t.task, level=t.level)
        rec.event("marker", label="trial_end", index=int(round(t.end_s * fs)), task=t.task)
    step = 97  # odd chunk size on purpose
    for i in range(0, len(block.data), step):
        rec.write(mv_chunk(block.data[i:i + step], i))
    rec.close()
    s = to_session(RawRecording.open(rec.dir), "via-raw")
    s.save(tmp_path / "sessions")
    back = Session.load(tmp_path / "sessions" / "via-raw")
    assert back.provenance["raw_recording"] == rec.recording_id
    assert np.allclose(back.blocks[0].data, block.data, atol=1e-6)
    assert [t.task for t in back.blocks[0].trials] == [t.task for t in block.trials]
    a = analyze(mirror_session, cfg)["summary"]["flexors"]["activation_pct"]
    b = analyze(back, cfg)["summary"]["flexors"]["activation_pct"]
    assert b == pytest.approx(a, rel=1e-3)
