"""Signal viewer: reference maths, incremental reading of a recording that is still being written, the
HTTP API, and the browser maths (emgcore.js, run in Node) against the Python reference."""

import json
import shutil
import struct
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from emgmirror.acquisition.base import Chunk, SourceInfo
from emgmirror.recording import RawRecorder
from emgmirror.storage import Channel
from emgmirror.viewer import analysis as va
from emgmirror.viewer.server import STATIC, serve

FS = 2000.0
CH = [Channel("a", "right", "flexors"), Channel("b", "right", "extensors")]


def info():
    return SourceInfo(kind="esp32", label="test", fs_nominal=FS, channels=CH, is_real_hardware=False, raw_dtype="<u2",
                      raw_units="sum of 4 codes", device={"os": "4", "cal_a": "0.8", "cal_b": "10", "pins": "35,36"},
                      calibration={"bias_mv": 1650.0, "gain": 2420.0})


def chunk(i0, n, gap=0, value=8000):
    raw = np.full((n, 2), value, dtype=np.uint16)
    raw[:, 1] = np.arange(i0, i0 + n) % 4000
    pin = 0.8 * raw / 4 + 10
    return Chunk(index=np.arange(i0, i0 + n), raw=raw, valid=np.ones_like(raw, bool), mv=pin, pin_mv=pin,
                 t_device_us=(i0 + n) * 500, run=1, seq=i0 // 10, gap_before=gap, gap_cause="device" if gap else "")


# ---------------------------------------------------------------- reference maths
def test_envelope_of_a_sine_is_its_rms():
    t = np.arange(int(2 * FS)) / FS
    y = 10 * np.sin(2 * np.pi * 100 * t)
    env = va.envelope_rms(y, FS, 0.1)
    assert env[400:-400] == pytest.approx(10 / np.sqrt(2), rel=0.01)
    y[1000:1010] = np.nan  # NaNs are left out, not counted as zero
    assert va.envelope_rms(y, FS, 0.1)[1005] == pytest.approx(10 / np.sqrt(2), rel=0.03)


def test_activations_hysteresis_merge_and_min_length():
    r = np.ones(int(10 * FS))
    r[2000:4000] = 5.0          # 1.0 s burst
    r[4000:4200] = 2.5          # dips under on_x but stays above off_x: same period
    r[4200:5000] = 5.0
    r[5000:5300] = 1.0          # 0.15 s gap: merged
    r[5300:6000] = 5.0
    r[10000:10200] = 9.0        # 0.1 s blip: dropped
    acts = va.activations(r, FS, on_x=3, off_x=2, min_s=0.3, merge_s=0.25)
    assert acts == [(2000, 6000)]


def test_stats_frequency_and_snr():
    t = np.arange(int(4 * FS)) / FS
    y = 2 * np.sin(2 * np.pi * 120 * t)
    s = va.stats(y, FS, floor=np.sqrt(2) / 10)
    assert s["rms"] == pytest.approx(np.sqrt(2), rel=1e-3)
    assert abs(s["median_hz"] - 120) <= 8 and abs(s["mean_hz"] - 120) <= 8
    assert s["snr_db"] == pytest.approx(20.0, abs=0.05)


# ---------------------------------------------------------------- incremental reading
def test_tail_reader_follows_a_growing_recording_with_gaps(tmp_path):
    rec = RawRecorder(tmp_path, info(), recording_id="live")
    rec.write(chunk(1000, 100))
    rec.flush()
    r = va.TailReader(rec.dir)
    first, block, nxt = r.read(0)
    assert (first, block.shape, nxt) == (0, (100, 2), 100)
    assert block[0, 0] == pytest.approx(0.8 * 2000 + 10)
    first, block, nxt = r.read(nxt)  # nothing new yet
    assert block.shape[0] == 0 and nxt == 100
    rec.write(chunk(1110, 50, gap=10))  # 10 samples lost
    rec.flush()
    first, block, nxt = r.read(100)
    assert first == 100 and block.shape == (60, 2) and nxt == 150
    assert np.isnan(block[:10]).all() and np.isfinite(block[10:]).all()
    rec.write(chunk(0, 20))  # device restarted: index went back to 0
    rec.flush()
    first, block, nxt = r.read(150)
    assert first == 160 and block.shape == (20, 2)  # continues right after the last position
    with pytest.raises(ValueError):
        r.read(5)
    rec.close()


def test_tail_reader_restart_inside_one_read(tmp_path):
    rec = RawRecorder(tmp_path, info(), recording_id="r2")
    rec.write(chunk(500, 30))
    rec.write(chunk(540, 10, gap=10))   # 10 lost: positions 30..39 NaN, then 40..49
    rec.write(chunk(0, 20))             # restart: continues at 50..69
    rec.write(chunk(0, 5))              # second restart: 70..74
    rec.close()
    first, block, nxt = va.TailReader(rec.dir).read(0)
    assert (first, block.shape, nxt) == (0, (75, 2), 65)
    assert np.isnan(block[30:40]).all() and np.isfinite(block[:30]).all() and np.isfinite(block[40:]).all()
    assert block[50, 1] == pytest.approx(0.8 * 0 / 4 + 10) and block[70, 1] == pytest.approx(10.0)


# ---------------------------------------------------------------- HTTP API
@pytest.fixture
def server(tmp_path):
    httpd = serve(tmp_path, {"acquisition": {"mains_hz": 50, "amp_gain": 2420.0},
                             "room_noise_floor": {"pin_mv_rms": {"ch1": 12.3, "ch2": 18.4}, "measured": "test"}},
                  dataset_root=tmp_path / "dataset", scene=None, port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield tmp_path, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, dict(r.headers), r.read()


def block_of(body):
    first, n, nc = struct.unpack("<qii", body[:16])
    return first, np.frombuffer(body[16:], "<f4").reshape(n, nc)


def test_api_serves_a_live_recording_then_its_offline_cleaning(server):
    root, base = server
    rec = RawRecorder(root, info(), recording_id="20990101-000000_live", label="live")
    rec.write(chunk(0, 4000))
    rec.flush()
    assert json.loads(get(base + "/api/recordings")[2])[0]["status"] == "recording"
    meta = json.loads(get(base + "/api/rec/20990101-000000_live/meta")[2])
    assert meta["fs"] == FS and meta["floor_pin_mv"] == [12.3, 18.4] and "config" in meta["floor_source"]
    assert meta["channels"][0]["gpio"] == "35"
    st, h, body = get(base + "/api/rec/20990101-000000_live/data?row=0&client=t")
    first, b = block_of(body)
    assert first == 0 and b.shape == (4000, 2) and h["X-Status"] == "recording" and h["X-Next-Row"] == "4000"
    assert h["X-Last-Host-Ns"].isdigit()
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/api/rec/20990101-000000_live/clean")
    assert e.value.code == 409  # still open: the browser filters it
    rec.write(chunk(4000, 2000))
    rec.close()
    st, h, body = get(base + "/api/rec/20990101-000000_live/data?row=4000&client=t")
    first, b = block_of(body)
    assert first == 4000 and b.shape == (2000, 2) and h["X-Status"] == "complete"
    st, h, body = get(base + "/api/rec/20990101-000000_live/clean")
    first, y = block_of(body)
    assert y.shape == (6000, 2) and np.isfinite(y).all()
    assert abs(float(np.median(y[:, 0]))) < 1.0  # constant input -> ~0 after the band-pass


def test_complete_status_is_never_sent_without_the_last_rows(server, monkeypatch):
    """The recorder may write its final rows and mark the manifest complete while a data request is being served.
    A response that says 'complete' (after which the browser stops polling) must already hold every row."""
    root, base = server
    rid = "20990101-000000_race"
    rec = RawRecorder(root, info(), recording_id=rid, label="race")
    rec.write(chunk(0, 4000))
    rec.flush()
    url = base + f"/api/rec/{rid}/data?client=race&row="
    st, h, body = get(url + "0")
    assert h["X-Status"] == "recording" and h["X-Next-Row"] == "4000"
    orig = va.TailReader.read

    def read_then_finish(self, from_row, *a, **k):
        out = orig(self, from_row, *a, **k)
        if not getattr(rec, "_finished_in_test", False):  # recorder finishes right after the samples were read
            rec._finished_in_test = True
            rec.write(chunk(4000, 1000))
            rec.close()
        return out

    monkeypatch.setattr(va.TailReader, "read", read_then_finish)
    row, got = 4000, 4000
    for _ in range(5):
        st, h, body = get(url + str(row))
        got += block_of(body)[1].shape[0]
        row = int(h["X-Next-Row"])
        if h["X-Status"] != "recording":
            break
    assert h["X-Status"] == "complete"
    assert got == 5000  # the final 1000 rows reached the client before it was told the recording closed


def test_meta_follows_a_dataset_floor_check_written_while_the_viewer_runs(server):
    root, base = server
    rid = "20990101-000000_closed"
    rec = RawRecorder(root, info(), recording_id=rid, label="closed")
    rec.write(chunk(0, 2000))
    rec.close()
    url = base + f"/api/rec/{rid}/meta"
    assert "config" in json.loads(get(url)[2])["floor_source"]
    sess = root / "dataset" / "subj" / "s1"
    sess.mkdir(parents=True)
    (sess / "session.json").write_text(json.dumps({"recordings": [rid]}), encoding="utf-8")
    (sess / "floor.json").write_text(json.dumps({"recording_id": rid, "channels": [{"floor_pin_mv": 1.5},
                                                                                   {"floor_pin_mv": 2.5}]}),
                                     encoding="utf-8")
    meta = json.loads(get(url)[2])  # the recording files did not change; the cached meta must not be served
    assert meta["floor_pin_mv"] == [1.5, 2.5] and "dataset session" in meta["floor_source"]


def test_foreign_host_or_origin_is_refused(server):
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1 must not read recordings or the CT scene."""
    root, base = server
    for hdr in ({"Host": "evil.example"}, {"Origin": "http://evil.example"}):
        req = urllib.request.Request(base + "/api/recordings", headers=hdr)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 403
    ok = urllib.request.Request(base + "/api/recordings", headers={"Origin": base})
    assert urllib.request.urlopen(ok, timeout=10).status == 200


def test_api_rejects_unknown_and_unsafe_names(server):
    root, base = server
    for path in ("/api/rec/..%2F..%2Fetc/meta", "/api/rec/nope/meta", "/static/..%2Fserver.py"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base + path)
        assert e.value.code in (400, 404)
    assert get(base + "/static/emgcore.js")[0] == 200
    assert get(base + "/")[0] == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/api/scene")
    assert e.value.code == 404


# ---------------------------------------------------------------- browser maths == Python reference
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_browser_maths_match_python(tmp_path):
    rng = np.random.default_rng(3)
    n = int(6 * FS)
    t = np.arange(n) / FS
    y = rng.normal(0, 1.0, n) + np.where((t > 2) & (t < 3.5), 8 * np.sin(2 * np.pi * 110 * t), 0)
    y[5000:5020] = np.nan
    opts = {"on_x": 3.0, "off_x": 2.0, "min_s": 0.3, "merge_s": 0.25}
    (tmp_path / "in.json").write_text(json.dumps({"y": [None if not np.isfinite(v) else float(v) for v in y],
                                                  "fs": FS, "win": 200, "opts": opts}))
    script = tmp_path / "run.js"
    script.write_text(f"""
const EMG = require({json.dumps(str(STATIC / "emgcore.js"))});
const inp = JSON.parse(require("fs").readFileSync({json.dumps(str(tmp_path / "in.json"))}, "utf8"));
const y = Float64Array.from(inp.y.map(v => v === null ? NaN : v));
const envC = EMG.envelope(y, inp.win, true), envT = EMG.envelope(y, inp.win, false);
const ratio = Float32Array.from(envC, v => v / 1.0);
const acts = EMG.activations(ratio, inp.fs, inp.opts);
const st = EMG.stats(y, 4000, 7000, inp.fs, 1.0);
const lf = EMG.liveFilter(2000, [75, 450], 50, 30);
const g = f => EMG.gainAt(lf.sections, f, 2000);
console.log(JSON.stringify({{envC: Array.from(envC), envT: Array.from(envT), acts, st,
  gain: {{f20: g(20), f50: g(50), f100: g(100), f120: g(120), f225: g(225), f325: g(325), f700: g(700)}}}}));
""")
    out = json.loads(subprocess.run([NODE, str(script)], capture_output=True, text=True, check=True, timeout=60).stdout)
    env_c = va.envelope_rms(y, FS, 0.1, centered=True)
    env_t = va.envelope_rms(y, FS, 0.1, centered=False)
    assert np.allclose(np.array(out["envC"], float), env_c, rtol=1e-4, atol=1e-5, equal_nan=True)
    assert np.allclose(np.array(out["envT"], float), env_t, rtol=1e-4, atol=1e-5, equal_nan=True)
    assert [tuple(a) for a in out["acts"]] == va.activations(env_c, FS, **opts)
    ref = va.stats(y[4000:7000], FS, floor=1.0)
    assert out["st"]["rms"] == pytest.approx(ref["rms"], rel=1e-6)
    assert out["st"]["median_hz"] == pytest.approx(ref["median_hz"], abs=8)
    g = out["gain"]
    assert g["f20"] < 0.05 and g["f700"] < 0.1           # band edges
    assert g["f50"] < 0.01 and g["f100"] < 0.01          # mains notches
    assert 0.85 < g["f120"] < 1.1 and 0.9 < g["f225"] < 1.1 and 0.9 < g["f325"] < 1.1  # pass band (between harmonics)
