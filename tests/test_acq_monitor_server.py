"""Diagnostics (stage model, per-pin checks) and the acquisition API, against emulated hardware."""
import pytest

import time

import numpy as np
from fastapi.testclient import TestClient

from emgmirror.acquisition.emulator import EmulatorConfig, Esp32Emulator
from emgmirror.acquisition.esp32 import Esp32Source
from emgmirror.acquisition.monitor import StreamMonitor
from emgmirror.acquisition.pipeline import Acquisition
from emgmirror.acquisition.registry import open_source
from emgmirror.server.app import create_app
from emgmirror.storage import Channel

CH3 = [Channel("flex", "right", "flexors"), Channel("ext", "right", "extensors"),
       Channel("brach", "right", "brachioradialis")]
ACQ = {"fs": 2000, "amp_gain": 2420.0, "mains_hz": 50}


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def monitored(modes, seconds=1.2, **ecfg):
    clock = Clock()
    emu = Esp32Emulator(EmulatorConfig(modes=modes, **ecfg), clock=clock, realtime=False)
    src = Esp32Source("SIM", CH3, ACQ, transport_factory=lambda: emu)
    src.ser, src.connected = emu, True
    src.process_bytes(emu.read(10**8))
    mon = StreamMonitor(src, mains_hz=50)
    clock.t += seconds
    src.process_bytes(emu.read(10**8))
    for c in src.read_chunks():
        mon.update(c)
    return src, mon


def test_channel_classification_covers_the_rig_failure_modes():
    _, mon = monitored(("flat_low", "saturated", "flat_mid"))
    rep = {c["ch"]: c for c in mon.channel_report()}
    assert rep[1]["state"] == "fail" and "0 V" in rep[1]["detail"]
    assert rep[2]["state"] == "fail" and "full scale" in rep[2]["detail"]
    assert rep[3]["state"] == "warn" and "flat" in rep[3]["detail"]
    _, mon = monitored(("emg", "floating", "emg"))
    states = [c["state"] for c in mon.channel_report()]
    assert states[1] == "ok"  # a floating pin varies: only a person-free check, never "EMG"


def test_mostly_mains_hum_is_flagged():
    _, mon = monitored(("emg", "emg", "emg"))
    ch = mon.channel_report()[0]
    assert ch["mains_fraction"] is not None and 0 <= ch["mains_fraction"] <= 1


def test_stage_report_separates_connection_acquisition_and_emg():
    src, mon = monitored(("emg", "emg", "emg"))
    r = mon.report()
    assert [r["stages"][k]["state"] for k in ("connection", "device", "acquisition")] == ["ok", "ok", "ok"]
    assert r["rates"]["device_clock_hz"] == 2000
    mon.last_host_t -= 5  # nothing arrived for 5 s
    assert "stalled" in mon.report()["stages"]["acquisition"]["detail"]
    src.connected, src.error = False, "transport lost: unplugged"
    assert mon.report()["stages"]["connection"] == {"state": "fail", "detail": "transport lost: unplugged"}
    src.info.kind, src.info.is_real_hardware = "esp32", True
    assert mon.report()["stages"]["emg"]["state"] == "not_verified"


def test_waveform_is_decimated_and_keeps_extremes():
    _, mon = monitored(("emg", "flat_mid", "saturated"), seconds=2.5)
    wf = mon.waveform(seconds=2.0, points=400)
    assert len(wf["series"]) == 3 and 300 <= len(wf["series"][0]) <= 420
    assert max(wf["series"][2]) > 3200


def test_virtual_source_runs_through_the_same_pipeline(cfg):
    acq = Acquisition(open_source("virtual", cfg), cfg)
    acq.start()
    time.sleep(0.3)
    out = acq.read()
    r = acq.report()
    acq.stop()
    assert out.shape[1] == 4 and len(out) > 100
    assert r["source"]["kind"] == "virtual" and not r["source"]["is_real_hardware"]
    assert r["stages"]["emg"]["state"] == "n/a"


def test_acquisition_api_with_emulator(tmp_path, cfg):
    client = TestClient(create_app(data_dir=tmp_path / "sessions", raw_dir=tmp_path / "raw"))
    assert client.get("/diagnostics").status_code == 200
    assert client.get("/api/acq/status").json()["running"] is False
    bad = client.post("/api/acq/start", json={"source": "esp32:COM_DOES_NOT_EXIST"})
    assert bad.status_code == 409
    assert client.get("/api/acq/status").json()["error"]
    r = client.post("/api/acq/start", json={"source": "emulator:emg,emg,emg,emg"})
    assert r.status_code == 200, r.text
    assert client.post("/api/acq/record/start", json={"label": "api test"}).status_code == 200
    assert client.post("/api/acq/record/start", json={"label": "twice"}).status_code == 409
    time.sleep(0.6)
    assert client.post("/api/acq/mark", json={"label": "tap"}).status_code == 200
    with client.websocket_connect("/ws/acq") as ws:
        msg = ws.receive_json()
    assert msg["status"]["running"] and msg["status"]["recording"]["samples"] > 0
    assert len(msg["waveform"]["series"]) == 4
    stopped = client.post("/api/acq/record/stop").json()
    assert stopped["closed"] and stopped["samples"] > 500
    raws = client.get("/api/raw").json()
    assert raws[0]["status"] == "complete"
    integ = client.get(f"/api/raw/{raws[0]['recording_id']}/integrity").json()
    assert integ["missing_samples"] == 0 and integ["samples"] == stopped["samples"]
    assert client.get("/api/raw/..%2F..%2Fx/integrity").status_code == 404
    client.post("/api/acq/stop")
    assert client.get("/api/acq/status").json()["running"] is False


def test_live_mirror_websocket_accepts_a_port_through_the_pipeline(tmp_path, cfg):
    client = TestClient(create_app(data_dir=tmp_path, raw_dir=tmp_path / "raw"))
    with client.websocket_connect("/ws/live?port=emulator:emg,emg,emg,emg") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and len(hello["channels"]) == 4
        data = ws.receive_json()
        assert data["type"] == "data" and np.isfinite(data["envelope"]).all()


def test_live_check_cues_markers_and_latency(tmp_path, cfg):
    from emgmirror.recording import RawRecording
    from emgmirror.recording.derive import to_session

    client = TestClient(create_app(data_dir=tmp_path / "sessions", raw_dir=tmp_path / "raw"))
    assert client.get("/live-check").status_code == 200
    assert client.post("/api/acq/check/start", json={}).status_code == 409  # no source yet
    assert client.post("/api/acq/start", json={"source": "emulator:emg,emg,emg,emg"}).status_code == 200
    cues = [{"task": "rest", "say": "RELAX", "seconds": 0.8}, {"task": "grip_mvc", "say": "SQUEEZE", "seconds": 0.6,
                                                               "level": 1.0}, {"task": "rest", "say": "RELAX", "seconds": 0.4}]
    r = client.post("/api/acq/check/start", json={"label": "lc test", "cues": cues})
    assert r.status_code == 200, r.text
    seen_cues, items = set(), []
    with client.websocket_connect("/ws/live-check") as ws:
        t0 = time.time()
        while time.time() - t0 < 15:  # generous: the machine may be busy; the loop exits on "done"
            m = ws.receive_json()
            items += m["items"]
            if m["items"]:
                ws.send_json({"t_host": m["items"][-1]["t_host"], "rendered": time.time()})
            if "cue" in m:
                if m["cue"].get("running"):
                    seen_cues.add(m["cue"]["say"])
                if m["cue"].get("done") and m.get("last_check"):
                    last = m["last_check"]
                    break
    assert seen_cues == {"RELAX", "SQUEEZE"}
    assert items and all(len(it["rms"]) == 4 for it in items)
    assert [it["seq"] for it in items] == sorted({it["seq"] for it in items})  # no repeats, in order
    assert last["aborted"] is None and last["rest_env"] is not None
    lat = last["latency"]
    assert lat["host_to_screen_ms"]["p50"] is not None and lat["device_to_host_excess_ms"]["p50"] is not None
    rec = RawRecording.open(last["recording"]["dir"])
    assert rec.manifest["status"] == "complete"
    labels = [e["label"] for e in rec.events if e.get("event") == "marker"]
    assert labels.count("trial_start") == 3 and labels.count("trial_end") == 3 and "latency" in labels
    s = to_session(rec, "lc")
    assert [t.task for t in s.blocks[0].trials] == ["rest", "grip_mvc", "rest"]
    client.post("/api/acq/stop")


def test_live_feed_latency_definitions():
    from emgmirror.acquisition.base import mv_chunk
    from emgmirror.acquisition.livecheck import LiveFeed

    feed = LiveFeed(2000.0, 1, {"processing": {"band_hz": [20, 450]}, "acquisition": {"mains_hz": 50}})
    for k, delay in enumerate([0.010, 0.002, 0.030]):  # host arrival minus device time, in s
        c = mv_chunk(np.zeros((40, 1)), k * 40)
        c.t_device_us = int((k * 40 + 40) / 2000 * 1e6)
        c.t_host = 1000.0 + c.t_device_us / 1e6 + delay
        feed.update(c)
    # The fastest packet (2 ms) defines zero; the others are 0 (first, then re-baselined), 28 ms above it.
    assert list(feed.dev_excess_ms) == pytest.approx([0.0, 0.0, 28.0], abs=1e-6)
    feed.note_screen(12.0)
    assert feed.latency()["host_to_screen_ms"]["p50"] == 12.0
