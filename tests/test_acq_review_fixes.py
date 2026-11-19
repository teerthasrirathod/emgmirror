"""Regression tests for acquisition / recording / dataset / server / cli review findings."""

import json
import sys
import threading
import time

import numpy as np
import pytest

from emgmirror.acquisition import emgm2, safety
from emgmirror.acquisition.base import Chunk, DataSource, SourceInfo, mv_chunk
from emgmirror.acquisition.emulator import EmulatorConfig, Esp32Emulator
from emgmirror.acquisition.esp32 import Esp32Source, HandshakeError, min_rate_hz
from emgmirror.acquisition.pipeline import Acquisition
from emgmirror.recording import RawRecorder, RawRecording
from emgmirror.storage import Channel

CH2 = [Channel("a", "right", "flexors"), Channel("b", "right", "extensors")]


class TickSource(DataSource):
    """Virtual mV source: delivers samples in real time; optionally raises from read_chunks."""

    def __init__(self, fs=500.0, nch=2, raise_after_s=None, exc=KeyboardInterrupt):
        self.info = SourceInfo(kind="virtual", label="tick", fs_nominal=fs, channels=list(CH2[:nch]),
                               is_real_hardware=False, raw_dtype="<f4", raw_units="mV")
        self.raise_after_s, self.exc = raise_after_s, exc
        self._pos = 0

    def _start(self):
        self._t0 = time.perf_counter()

    def read_chunks(self):
        el = time.perf_counter() - self._t0
        if self.raise_after_s is not None and el > self.raise_after_s:
            raise self.exc()
        due = int(el * self.fs)
        n = due - self._pos
        if n <= 0:
            return []
        c = mv_chunk(np.zeros((n, len(self.channels))), self._pos, run=1)
        self._pos = due
        return [c]


def _short_cfg(cfg):
    c = json.loads(json.dumps(cfg))
    c["protocol"] = [{"task": "rest", "kind": "hold", "level": 0.0, "duration_s": 0.5, "instruction": "relax"}]
    c["protocol_rest_between_s"] = 0.5
    return c


# ------------------------------------------------------------------ #1
def test_aborted_guided_recording_is_marked_interrupted(tmp_path, cfg):
    from emgmirror.record import RecordingAborted, record_block

    acq = Acquisition(TickSource(), cfg)
    acq.start()
    rec = RawRecorder(tmp_path, acq.info, label="abort")
    acq.start_recording(rec)
    acq.abort_reason = "mains (AC) power connected during the recording"
    with pytest.raises(RecordingAborted):
        record_block(acq, _short_cfg(cfg), countdown=0)
    m = RawRecording.open(rec.dir).manifest
    assert m["status"] == "interrupted" and "mains" in m["status_reason"]


def test_ctrl_c_during_guided_recording_is_marked_interrupted(tmp_path, cfg):
    from emgmirror.record import record_block

    acq = Acquisition(TickSource(raise_after_s=0.3), cfg)
    acq.start()
    rec = RawRecorder(tmp_path, acq.info, label="ctrlc")
    acq.start_recording(rec)
    with pytest.raises(KeyboardInterrupt):
        record_block(acq, _short_cfg(cfg), countdown=0)
    m = RawRecording.open(rec.dir).manifest
    assert m["status"] == "interrupted" and m["status_reason"] == "KeyboardInterrupt"


# ------------------------------------------------------------------ #2
def _esp_info(nch=2):
    return SourceInfo(kind="esp32", label="t", fs_nominal=2000.0,
                      channels=[Channel(f"ch{k + 1}", "right", "flexors") for k in range(nch)], is_real_hardware=True,
                      raw_dtype="<u2", raw_units="sum", device={"os": "4", "cal_a": "1.0", "cal_b": "0.0"},
                      calibration={"bias_mv": 1650.0, "gain": 2420.0})


def _write_run(rec, run, n, amp, rng, marks=()):
    from scipy.signal import butter, sosfiltfilt

    sos = butter(4, [100, 300], "bandpass", fs=2000, output="sos")
    x = sosfiltfilt(sos, rng.standard_normal((n, 2)), axis=0)
    x /= x.std(axis=0)
    raw = np.round((1650 + amp * x) * 4).astype(np.uint16)
    for i in range(0, n, 20):
        rec.write(Chunk(index=np.arange(i, i + 20), raw=raw[i:i + 20], valid=np.ones((20, 2), bool),
                        mv=np.zeros((20, 2)), run=run))
        for at, label in marks:
            if i + 20 == at:
                rec.mark(label, task="grip_mvc", level=1.0)


def test_markers_carry_the_run_and_match_only_their_own_run(tmp_path):
    from emgmirror import dataset
    from emgmirror.recording.derive import to_session

    rng = np.random.default_rng(1)
    rec = RawRecorder(tmp_path, _esp_info(), label="runs")
    _write_run(rec, 1, 5000, 10.0, rng, marks=[(1000, "trial_start"), (5000, "trial_end")])
    _write_run(rec, 2, 20000, 200.0, rng)  # device restarted: index from 0 again, much louder
    rec.close()
    r = RawRecording.open(rec.dir)
    assert {e["run"] for e in r.events if e.get("event") == "marker"} == {1}
    s = to_session(r, "s")
    assert len(s.blocks) == 2
    assert [t.task for t in s.blocks[0].trials] == ["grip_mvc"] and s.blocks[1].trials == []
    rows = dataset.analyse_recording(r, floor=[10.0, 10.0])
    assert len(rows) == 2 and all(row["missing_samples"] == 0 for row in rows)
    assert rows[0]["rms_pin_mv"] == pytest.approx(10.0, rel=0.2)  # only run 1's samples


def test_markers_without_run_still_load(tmp_path):
    from emgmirror import dataset
    from emgmirror.recording.derive import to_session

    rng = np.random.default_rng(2)
    rec = RawRecorder(tmp_path, _esp_info(), label="legacy")
    _write_run(rec, 1, 6000, 10.0, rng)
    rec.event("marker", label="trial_start", index=1000, task="grip_mvc", level=1.0)  # old format: no run
    rec.event("marker", label="trial_end", index=5000, task="grip_mvc")
    rec.close()
    r = RawRecording.open(rec.dir)
    assert [t["run"] for t in dataset.trials_from_markers(r)] == [None]
    assert len(dataset.analyse_recording(r, floor=[10.0, 10.0])) == 2
    assert len(to_session(r, "s").blocks[0].trials) == 1


# ------------------------------------------------------------------ #3
class FakeTextPort:
    def __init__(self, lines):
        self.data = bytearray("".join(lines).encode())
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, n=1):
        if not self.data:
            time.sleep(0.01)
            return b""
        out = bytes(self.data[:n])
        del self.data[:n]
        return out

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


def test_text_channel_count_mismatch_is_reported(cfg):
    from emgmirror.acquisition.registry import channels_from_config
    from emgmirror.acquisition.sources import SerialSource

    port = FakeTextPort(["# emgmirror fs=1000 ch=2 bits=10\n"] + ["512,520\n"] * 50)
    src = SerialSource("FAKE", channels_from_config(cfg), cfg["acquisition"], transport=port)
    assert len(src.channels) == 4
    src.start()
    t0 = time.time()
    while src.error is None and time.time() - t0 < 3:
        time.sleep(0.02)
    st = src.status()
    src.stop()
    assert "channel count mismatch" in (st["error"] or "") and "2 channel" in st["error"]
    assert st["connected"] is False


def test_record_refuses_to_save_an_empty_block(tmp_path, cfg, monkeypatch):
    import serial

    from emgmirror import cli

    c = _short_cfg(cfg)
    port = FakeTextPort(["# emgmirror fs=1000 ch=2 bits=10\n"] + ["512,520\n"] * 50)
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: port)
    monkeypatch.setattr(cli, "load_config", lambda path=None: c)
    monkeypatch.setattr(safety, "ac_power_connected", lambda: False)
    with pytest.raises(SystemExit, match="no samples arrived.*channel count mismatch"):
        cli.main(["--data", str(tmp_path / "sessions"), "record", "--source", "text:FAKE",
                  "--raw", str(tmp_path / "raw")])
    assert not list((tmp_path / "sessions").glob("*/session.json"))
    (rid,) = [p for p in (tmp_path / "raw").iterdir()]
    assert RawRecording.open(rid).manifest["status"] == "interrupted"


# ------------------------------------------------------------------ #4
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


ACQ = {"fs": 2000, "amp_gain": 2420.0, "serial_baud": 500000, "mains_hz": 50}
CH1 = [Channel("flex", "right", "flexors")]
HW1 = {"channels": [{"ch": 1, "gpio": 35}]}


def _rig(**ecfg):
    clock = Clock()
    emu = Esp32Emulator(EmulatorConfig(**ecfg), clock=clock, realtime=False)
    nch = len(ecfg.get("modes", ("emg", "emg", "emg")))
    chans = CH1 if nch == 1 else [Channel(f"c{k}", "right", "flexors") for k in range(nch)]
    hw = HW1 if nch == 1 else None
    src = Esp32Source("SIM", chans, ACQ, hardware=hw, transport_factory=lambda: emu)
    src.ser = emu
    for _ in range(4):  # HELLO -> START -> ACK + HELLO round trips, all synchronous
        src.process_bytes(emu.read(10**8))
    return src, emu, clock


def test_hello_with_running_0_gets_a_start():
    src, emu, clock = _rig(running=False)
    assert "START" in emu.commands and src.hello is not None and src.hello["running"] == "1"
    assert src.error is None
    clock.t += 0.5
    src.process_bytes(emu.read(10**8))
    assert sum(len(c) for c in src.read_chunks()) == 1000


def test_device_that_cannot_sample_fails_with_the_minimum_rate():
    src, emu, _ = _rig(fs=1000, modes=("emg",), pins=(35,), running=False, min_conv_hz=20000)
    assert emu.commands.count("START") == 1
    assert src.error.startswith("device is not sampling") and "1250 Hz" in src.error


def test_low_rate_command_reports_not_sampling():
    src, emu, clock = _rig(fs=2000, modes=("emg",), pins=(35,), min_conv_hz=20000)
    assert src.error is None
    src.send_command("RATE 1000")
    for _ in range(4):
        src.process_bytes(emu.read(10**8))
    assert emu.commands.count("START") == 1 and "1250 Hz" in (src.error or "")


def test_handshake_raises_when_the_device_does_not_sample():
    emu = Esp32Emulator(EmulatorConfig(fs=500, modes=("emg", "emg"), pins=(35, 32), running=False,
                                       min_conv_hz=20000))
    src = Esp32Source("SIM", CH2, ACQ, transport_factory=lambda: emu, handshake_timeout_s=3)
    with pytest.raises(HandshakeError, match="at least 625 Hz"):
        src.start()


def test_min_rate_per_channel_count():
    assert [min_rate_hz(n) for n in (1, 2, 3, 6)] == [1250, 625, 500, 500]


# ------------------------------------------------------------------ #6
def test_unknown_device_timestamp_is_none_not_a_huge_number():
    raw = np.full((10, 1), 6600, dtype=np.uint16)
    pkt = emgm2.encode_data(0, 0, -1, 0, raw, 4)
    (p,) = emgm2.StreamDecoder().feed(bytes(1) + pkt)
    assert p.frame.t_us == -1
    src, emu, clock = _rig(fs=2000, modes=("emg",), pins=(35,))
    src.read_chunks()
    clock.t += 0.01
    src.process_bytes(emu.read(10**8))
    src.read_chunks()
    src.process_bytes(emgm2.encode_data(emu.seq, emu.frames_made * 10, -1, 0, raw, 4))
    (c,) = src.read_chunks()
    assert c.t_device_us is None


# ------------------------------------------------------------------ #5
def test_dataset_run_aborts_when_mains_appears(tmp_path, monkeypatch):
    from emgmirror import dataset
    from emgmirror.acquisition import registry
    from emgmirror.config import REPO_ROOT, load_config

    c = load_config(REPO_ROOT / "configs" / "grip_dataset.yaml")
    c["protocol"] = [{"task": "grip_max", "kind": "hold", "level": 1.0, "duration_s": 1.0, "instruction": "squeeze"}]
    c["protocol_rest_between_s"] = 0.5
    real_open = registry.open_source

    def open_as_hardware(*a, **k):
        src = real_open(*a, **k)
        src.info.is_real_hardware = True  # emulator posing as the real board
        return src

    monkeypatch.setattr(registry, "open_source", open_as_hardware)
    # On battery for the start checks; the charger "appears" as soon as the mid-session watcher looks.
    monkeypatch.setattr(safety, "ac_power_connected", lambda: threading.current_thread().name == "power-watch")
    with pytest.raises(SystemExit, match="mains"):
        dataset.run_session(c, "emulator:emg,emg", "tester", "right", tmp_path / "dataset", tmp_path / "raw",
                            tmp_path / "sessions", confirm=False, countdown=0, floor_s=3.0)
    (rid,) = list((tmp_path / "raw").iterdir())
    m = RawRecording.open(rid).manifest
    assert m["status"] == "interrupted" and "mains" in m["status_reason"]


# ------------------------------------------------------------------ #7 / #29
class _Done:
    def __init__(self, out):
        self.stdout = out


def test_macos_power_status_from_pmset(monkeypatch):
    import subprocess

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(
        "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t80%; discharging;\n"))
    assert safety.ac_power_connected() is False
    assert safety.check_before_person_recording() == []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done("Now drawing from 'AC Power'\n"))
    assert safety.ac_power_connected() is True


def test_unknown_power_status_stays_blocked_and_says_why(monkeypatch):
    import subprocess

    def missing(*a, **k):
        raise FileNotFoundError("pmset")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", missing)
    assert safety.ac_power_connected() is None
    (msg,) = safety.check_before_person_recording()
    assert "no override" in msg and "macOS" in msg and "Windows" in msg and "Linux" in msg


# ------------------------------------------------------------------ #8
def test_dashboard_raw_folder_follows_the_config(tmp_path):
    from emgmirror.server.app import create_app

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(f"raw_dir: {json.dumps(str(tmp_path / 'rawcfg'))}\n", encoding="utf-8")
    app = create_app(str(cfg_file), data_dir=tmp_path / "custom")
    assert app.state.acq.raw_dir == tmp_path / "rawcfg"
    app = create_app(str(cfg_file), data_dir=tmp_path / "custom", raw_dir=tmp_path / "explicit")
    assert app.state.acq.raw_dir == tmp_path / "explicit"


# ------------------------------------------------------------------ #9
def _slow_open(monkeypatch, module, delay=1.5):
    real = module.open_source

    def slow(*a, **k):
        src = real(*a, **k)
        start = src._start

        def slow_start():
            time.sleep(delay)
            start()

        src._start = slow_start
        return src

    monkeypatch.setattr(module, "open_source", slow)


def test_acq_websocket_does_not_freeze_the_server_during_a_handshake(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from emgmirror.server import acq as acq_mod
    from emgmirror.server.app import create_app

    _slow_open(monkeypatch, acq_mod)
    with TestClient(create_app(data_dir=tmp_path / "s", raw_dir=tmp_path / "raw")) as client:
        stop = threading.Event()

        def ws_reader():
            with client.websocket_connect("/ws/acq") as ws:
                while not stop.is_set():
                    ws.receive_json()

        starter = threading.Thread(target=lambda: client.post("/api/acq/start", json={"source": "emulator:emg,emg,emg,emg"}))
        reader = threading.Thread(target=ws_reader, daemon=True)
        reader.start()
        time.sleep(0.3)
        starter.start()
        time.sleep(0.4)  # the handshake is now sleeping inside the start
        t0 = time.time()
        assert client.get("/api/config").status_code == 200
        took = time.time() - t0
        starter.join()
        stop.set()
        reader.join(timeout=3)
        client.post("/api/acq/stop")
    assert took < 0.7, f"/api/config waited {took:.2f} s"


def test_live_websocket_does_not_freeze_the_server_during_a_handshake(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from emgmirror.server import app as app_mod

    _slow_open(monkeypatch, app_mod)
    with TestClient(app_mod.create_app(data_dir=tmp_path / "s", raw_dir=tmp_path / "raw")) as client:
        def live():
            with client.websocket_connect("/ws/live?port=emulator:emg,emg,emg,emg") as ws:
                ws.receive_json()

        t = threading.Thread(target=live, daemon=True)
        t.start()
        time.sleep(0.4)
        t0 = time.time()
        assert client.get("/api/config").status_code == 200
        took = time.time() - t0
        t.join(timeout=10)
    assert took < 0.7, f"/api/config waited {took:.2f} s"


# ------------------------------------------------------------------ #17 / #30
def test_calibrate_predict_without_session_or_model_gives_a_message(tmp_path):
    from emgmirror import cli

    with pytest.raises(SystemExit, match="give a session"):
        cli.main(["calibrate", "predict", "--model", str(tmp_path / "m.joblib")])
    with pytest.raises(SystemExit, match="calibrate train"):
        cli.main(["calibrate", "predict", "some-session", "--model", str(tmp_path / "none.joblib")])


def test_relative_model_path_is_relative_to_the_repository(tmp_path, monkeypatch):
    from emgmirror import cli
    from emgmirror.config import REPO_ROOT

    monkeypatch.chdir(tmp_path)
    assert cli._model_path("models/calibrator.joblib") == REPO_ROOT / "models" / "calibrator.joblib"
    (tmp_path / "local.joblib").write_bytes(b"x")
    assert cli._model_path("local.joblib", must_exist=True).resolve() == (tmp_path / "local.joblib").resolve()


# ------------------------------------------------------------------ #33
def test_dashboard_serves_plotly_locally(tmp_path):
    from fastapi.testclient import TestClient

    from emgmirror.server.app import STATIC, create_app

    client = TestClient(create_app(data_dir=tmp_path / "s", raw_dir=tmp_path / "raw"))
    r = client.get("/static/plotly.min.js")
    assert r.status_code == 200 and "plotly" in r.text[:2000].lower()
    for page in ("index.html", "diagnostics.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert "cdn.jsdelivr" not in html and '/static/plotly.min.js' in html


def test_cues_after_a_device_restart_land_in_their_own_run(tmp_path):
    """Viewer cues and CT-overlay cues use the marker's run: a trial in run 2 is placed after run 1."""
    from emgmirror import dataset
    from emgmirror.viewer.analysis import TailReader, recording_meta

    rng = np.random.default_rng(3)
    rec = RawRecorder(tmp_path, _esp_info(), label="cue-runs")
    _write_run(rec, 1, 4000, 10.0, rng)
    _write_run(rec, 2, 6000, 10.0, rng, marks=[(1000, "trial_start"), (3000, "trial_end")])
    rec.close()
    r = RawRecording.open(rec.dir)
    cue = recording_meta(r, {})["cues"][0]
    assert cue["start_s"] == pytest.approx((4000 + 1000) / 2000)  # run 1 is 4000 samples long
    assert cue["end_s"] == pytest.approx((4000 + 3000) / 2000)
    # the same position the viewer's TailReader gives that sample
    first, y, _ = TailReader(rec.dir).read(0)
    assert first == 0 and len(y) == 10000
    # stacked-record timeline (CT overlay): no gaps here, so the same answer
    t = dataset.trials_from_markers(r)[0]
    pos = dataset.record_positions(r, fill_gaps=False)
    assert dataset.marker_position(r, t["start"], t["run"], pos) == 5000
    # old markers without a run keep the old meaning
    assert dataset.marker_position(r, 1000, None, pos) == 1000


def test_viewer_meta_endpoint_places_cues_after_a_restart_in_their_run(tmp_path):
    """The live viewer opens recordings without frames.csv for speed; after a device restart it must still
    place cues on the TailReader timeline."""
    import urllib.request

    from emgmirror.viewer.server import serve

    rng = np.random.default_rng(4)
    rec = RawRecorder(tmp_path, _esp_info(), label="restart-meta")
    _write_run(rec, 1, 3000, 10.0, rng)
    _write_run(rec, 2, 5000, 10.0, rng, marks=[(1000, "trial_start"), (3000, "trial_end")])
    rec.close()
    httpd = serve(tmp_path, {}, dataset_root=None, scene=None, port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/api/rec/{rec.dir.name}/meta"
        with urllib.request.urlopen(url, timeout=10) as r:
            meta = json.loads(r.read())
    finally:
        httpd.shutdown()
        httpd.server_close()
    cue = meta["cues"][0]
    assert (cue["start_s"], cue["end_s"]) == (pytest.approx(2.0), pytest.approx(3.0))  # (3000+1000)/2000 ...


def test_a_half_written_last_frames_line_is_ignored(tmp_path):
    """A battery death mid-write can cut the last frames.csv line; analysis must still run."""
    from emgmirror import dataset

    rng = np.random.default_rng(5)
    rec = RawRecorder(tmp_path, _esp_info(), label="cut")
    _write_run(rec, 1, 5000, 10.0, rng, marks=[(1000, "trial_start"), (4000, "trial_end")])
    rec.close()
    with open(rec.dir / "frames.csv", "a", encoding="utf-8", newline="") as fh:
        fh.write("1,4,")  # cut off
    r = RawRecording.open(rec.dir)
    assert r.record_runs() is not None and len(r.record_runs()) == 5000
    assert len(dataset.analyse_recording(r, floor=[10.0, 10.0])) == 2


def test_stop_pressed_while_connecting_is_not_lost(tmp_path, monkeypatch):
    from emgmirror.server import acq as acq_mod
    from emgmirror.server.acq import AcquisitionManager

    _slow_open(monkeypatch, acq_mod)
    from emgmirror.config import load_config

    mgr = AcquisitionManager(load_config(), tmp_path / "s", tmp_path / "raw")
    t = threading.Thread(target=lambda: mgr.start("emulator:emg,emg,emg,emg"))
    t.start()
    time.sleep(0.4)  # the handshake is sleeping inside start()
    mgr.stop()
    t.join(timeout=10)
    assert mgr.acq is None and not mgr.status().get("running", False)
