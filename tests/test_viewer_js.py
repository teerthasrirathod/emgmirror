"""The viewer page (viewer.js) run in Node against the real HTTP server, with a minimal stand-in for the DOM.

Each test starts one or more viewer servers on a temporary raw folder, runs viewer.js in a scenario script and
checks the state it ends in (window.__viewer) or what it produced (dropdown, CSV)."""

import json
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from emgmirror.acquisition.base import Chunk, SourceInfo
from emgmirror.recording import RawRecorder
from emgmirror.storage import Channel
from emgmirror.viewer.server import STATIC, serve

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")
CH = [Channel("a", "right", "flexors"), Channel("b", "right", "extensors")]
CFG = {"acquisition": {"mains_hz": 50, "amp_gain": 2420.0},
       "room_noise_floor": {"pin_mv_rms": {"ch1": 12.3, "ch2": 18.4}, "measured": "test"}}


def info(fs=2000.0):
    return SourceInfo(kind="esp32", label="test", fs_nominal=fs, channels=CH, is_real_hardware=False, raw_dtype="<u2",
                      raw_units="sum of 4 codes", device={"os": "4", "cal_a": "0.8", "cal_b": "10", "pins": "35,36"},
                      calibration={"bias_mv": 1650.0, "gain": 2420.0})


def chunk(i0, n, gap=0, value=8000):
    """Channel 1 is flat (a constant ADC code), channel 2 a ramp."""
    raw = np.full((n, 2), value, dtype=np.uint16)
    raw[:, 1] = np.arange(i0, i0 + n) % 4000
    pin = 0.8 * raw / 4 + 10
    return Chunk(index=np.arange(i0, i0 + n), raw=raw, valid=np.ones_like(raw, bool), mv=pin, pin_mv=pin,
                 t_device_us=(i0 + n) * 500, run=1, seq=i0 // 10, gap_before=gap, gap_cause="device" if gap else "")


HARNESS = r"""
const fs = require("fs"), path = require("path");
const [STATIC, SCEN] = process.argv.slice(2);
const sc = JSON.parse(fs.readFileSync(SCEN, "utf8"));
const out = { errors: [] };
const done = () => { process.stdout.write(JSON.stringify(out) + "\n", () => process.exit(0)); };
process.on("unhandledRejection", (e) => out.errors.push("unhandled: " + (e && e.message)));
let BASE = sc.bases[0];
const realFetch = globalThis.fetch;
let metaFetches = 0;
globalThis.fetch = (u, o) => { if (u.endsWith("/meta")) metaFetches++; return realFetch(BASE + u, o); };
const realST = setTimeout;
globalThis.setTimeout = (f, ms, ...a) => realST(f, (ms || 0) * (sc.timeScale || 1), ...a);
const sleep = (ms) => new Promise((r) => realST(r, ms));
async function until(fn, ms = 10000) {
  const t = Date.now();
  while (Date.now() - t < ms) { try { if (fn()) return true; } catch (e) { /* not yet */ } await sleep(20); }
  return false;
}
// ---- minimal DOM
function ctxStub() {
  return new Proxy({}, { get(t, k) { if (k in t) return t[k]; if (k === "measureText") return () => ({ width: 5 }); return () => {}; },
                         set(t, k, v) { t[k] = v; return true; } });
}
const elements = {};
function makeEl(tag) {
  const el = { tag, children: [], value: "", checked: false, style: {}, dataset: {}, hidden: false, textContent: "", className: "",
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild(c) { this.children.push(c); return c; }, querySelectorAll() { return []; },
    addEventListener() {}, setPointerCapture() {}, click() {},
    clientWidth: 900, clientHeight: 600, width: 0, height: 0,
    getContext() { return ctxStub(); }, getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 600 }; },
    get rows() { return [{ cells: Array.from({ length: 10 }, () => ({})) }]; } };
  let html = "";
  Object.defineProperty(el, "innerHTML", { get() { return html; }, set(v) { html = v; if (v === "") el.children = []; } });
  return el;
}
globalThis.document = { getElementById: (id) => (elements[id] = elements[id] || makeEl("div")), createElement: makeEl,
                        querySelectorAll: () => [], addEventListener() {} };
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.location = { hash: "" };
globalThis.history = { replaceState() {} };
globalThis.devicePixelRatio = 1;
Object.defineProperty(globalThis, "localStorage", { value: { getItem: () => null, setItem() {} }, configurable: true, writable: true });
globalThis.ResizeObserver = class { observe() {} };
globalThis.ImageData = class { constructor(w, h) { this.width = w; this.height = h; this.data = new Uint8ClampedArray(w * h * 4); } };
globalThis.alert = (m) => out.errors.push("alert: " + m);
let rafCb = null;
globalThis.requestAnimationFrame = (cb) => { rafCb = cb; return 1; };
const RealBlob = Blob;
let lastBlob = null;
globalThis.Blob = class extends RealBlob { constructor(parts, o) { super(parts, o); lastBlob = parts.join(""); } };
function runFrame() { const cb = rafCb; rafCb = null; if (!cb) return "no frame scheduled"; try { cb(performance.now()); return null; } catch (e) { return String(e.message); } }
globalThis.EMG = require(path.join(STATIC, "emgcore.js"));
require(path.join(STATIC, "viewer.js"));
const S = () => globalThis.__viewer;

(async () => {
  if (sc.name === "flatspec") {
    await until(() => S().meta && S().n > 0 && S().pollTimer);
    S().specOn = true; S().specCh = 0; S().dirty = true;
    out.err1 = runFrame();
    S().dirty = true;
    out.err2 = runFrame();          // the animation loop must still be alive
    out.spec = !!S().specCache;
  } else if (sc.name === "restart") {
    await until(() => S().meta && S().n === sc.rows && S().pollTimer);
    out.before = S().n;
    BASE = sc.bases[1];             // the viewer server restarted: a fresh process with no readers
    await sleep(1500);
    out.after = S().n; out.pin = S().pin[0].length; out.status = S().status;
  } else if (sc.name === "nosamples") {
    // past the old limit of 30 tries (30 x 500 ms; timers are sped up here), or until the old code gave up
    await until(() => S().meta || metaFetches > 40, 60000);
    out.metaFetches = metaFetches;
    fs.writeFileSync(sc.marker, "ready");
    out.ok = await until(() => S().meta && S().meta.fs && S().n > 0, 15000);
    out.fs = S().meta && S().meta.fs; out.n = S().n;
  } else if (sc.name === "list") {
    await until(() => elements.rec && elements.rec.children.length > 0);
    out.options = elements.rec.children.map((c) => c.textContent);
  } else if (sc.name === "csv") {
    await until(() => S().clean && S().n > 0);
    S().sel = { a: 0, b: S().n / S().fs };
    elements.exportCsv.onclick();
    out.csv = lastBlob;
  }
  done();
})();
"""


@pytest.fixture
def servers(tmp_path):
    started = []

    def start(n=1):
        for _ in range(n):
            httpd = serve(tmp_path / "raw", CFG, dataset_root=tmp_path / "dataset", scene=None, port=0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            started.append(httpd)
        return [f"http://127.0.0.1:{h.server_address[1]}" for h in started]

    (tmp_path / "raw").mkdir()
    yield tmp_path / "raw", start
    for h in started:
        h.shutdown()
        h.server_close()


def scenario(tmp_path, **sc):
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    (tmp_path / "scenario.json").write_text(json.dumps(sc), encoding="utf-8")
    return [NODE, str(tmp_path / "harness.js"), str(STATIC), str(tmp_path / "scenario.json")]


def run(tmp_path, **sc):
    p = subprocess.run(scenario(tmp_path, **sc), capture_output=True, text=True, timeout=90)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_flat_channel_with_spectrogram_keeps_the_viewer_drawing(servers, tmp_path):
    """A channel stuck at one ADC code is exactly 0 after filtering: its spectrogram has no colour range."""
    raw, start = servers
    bases = start()
    rec = RawRecorder(raw, info(), recording_id="20990101-000000_flat")
    rec.write(chunk(0, 4000))
    rec.flush()
    out = run(tmp_path, name="flatspec", bases=bases)
    rec.close()
    assert out["err1"] is None and out["err2"] is None, out
    assert out["spec"]


def test_open_live_tab_does_not_duplicate_data_after_a_server_restart(servers, tmp_path):
    raw, start = servers
    bases = start(2)
    rec = RawRecorder(raw, info(), recording_id="20990101-000000_live")
    rec.write(chunk(0, 4000))
    rec.flush()
    out = run(tmp_path, name="restart", bases=bases, rows=4000)
    rec.close()
    assert out["before"] == 4000
    assert out["after"] == 4000 and out["pin"] == 4000, out


def test_recording_without_samples_for_a_while_is_picked_up_when_they_arrive(servers, tmp_path):
    raw, start = servers
    bases = start()
    rec = RawRecorder(raw, info(), recording_id="20990101-000000_late")
    marker = tmp_path / "ready.txt"
    p = subprocess.Popen(scenario(tmp_path, name="nosamples", bases=bases, timeScale=0.01, marker=str(marker)),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        t = time.time()
        while not marker.exists() and time.time() - t < 30 and p.poll() is None:
            time.sleep(0.05)
        rec.write(chunk(0, 2000))
        rec.flush()
        stdout, stderr = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
        rec.close()
    out = json.loads(stdout.strip().splitlines()[-1])
    assert out["ok"] and out["fs"] == 2000 and out["n"] == 2000, (out, stderr)
    assert not out["errors"], out


def test_recording_list_shows_the_duration_at_the_recording_rate(servers, tmp_path):
    raw, start = servers
    bases = start()
    rec = RawRecorder(raw, info(fs=1000.0), recording_id="20990101-000000_slow")
    rec.write(chunk(0, 4000))
    rec.close()
    out = run(tmp_path, name="list", bases=bases)
    assert out["options"] == ["20990101-000000_slow  (4 s)"], out


def test_csv_export_writes_missing_values_as_numbers(servers, tmp_path):
    raw, start = servers
    bases = start()
    rec = RawRecorder(raw, info(), recording_id="20990101-000000_gap")
    rec.write(chunk(0, 2000))
    rec.write(chunk(2010, 2000, gap=10))  # 10 samples lost: NaN in the export
    rec.close()
    out = run(tmp_path, name="csv", bases=bases)
    lines = out["csv"].split("\n")
    assert lines[0].startswith("t_s,ch1_pin_mv")
    rows = [line.split(",") for line in lines[1:]]
    assert len(rows) == 4010
    vals = np.array([[float(v) for v in r] for r in rows])  # every cell is a number (NaN included)
    assert np.isnan(vals[2000:2010, 1]).all() and np.isfinite(vals[:2000, 1]).all()
    assert "–" not in out["csv"]
