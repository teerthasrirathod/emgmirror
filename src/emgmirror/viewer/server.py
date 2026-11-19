"""Local HTTP server for the signal viewer (standard library only, bound to 127.0.0.1).

  GET /                              viewer page
  GET /static/<file>                 viewer code (and plotly.min.js from the installed plotly package)
  GET /api/recordings                raw recordings, newest first
  GET /api/rec/<id>/meta             channels, device, integrity, floor, cues, trials, events
  GET /api/rec/<id>/data?row=N       pin mV from row N on (binary, see below); poll it to follow a
                                     recording that is still being written
  GET /api/rec/<id>/clean            offline cleaned signal of a closed recording (band-pass 75-450 Hz +
                                     mains regression, zero-phase: the same as the dataset metrics)
  GET /api/scene                     CT forearm scene (from `emgmirror ct emg-overlay`), if built

Binary data block: 16-byte header (int64 first position, int32 positions, int32 channels), then
float32 pin mV, positions x channels, NaN where a sample is missing. Response headers: X-Next-Row,
X-Status (recording / complete / interrupted), X-Last-Host-Ns (host clock at the last chunk).

The viewer reads the raw recording files directly, so a live view never touches the acquisition
process: the recorder writes, the viewer tails the file.
"""

from __future__ import annotations

import json
import mimetypes
import re
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

import numpy as np

from ..recording import RawRecording, list_recordings
from .analysis import TailReader, recording_meta

STATIC = Path(__file__).parent / "static"
ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def plotly_js() -> Path | None:
    try:
        import plotly

        p = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
        return p if p.exists() else None
    except ImportError:
        return None


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _hostname(value: str) -> str:
    return (urlsplit(value if "//" in value else f"//{value}").hostname or "").lower()


def _local_request(host: str, origin: str | None) -> bool:
    """Binding to 127.0.0.1 stops other machines, not other websites: a page on a domain that resolves to
    127.0.0.1 (DNS rebinding) is same-origin for the browser. So the Host header must name this machine, and an
    Origin, when the browser sends one, must too."""
    if _hostname(host) not in LOCAL_HOSTS:
        return False
    return not origin or _hostname(origin) in LOCAL_HOSTS


def _dataset_sig(dataset_root: Path | None) -> tuple:
    """Paths and mtimes of the dataset session files that recording_meta reads."""
    if not dataset_root:
        return ()
    out = []
    for pat in ("*/*/session.json", "*/*/floor.json", "*/*/trials.csv"):
        for p in sorted(Path(dataset_root).glob(pat)):
            try:
                out.append((str(p), p.stat().st_mtime_ns))
            except OSError:
                continue
    return tuple(out)


class ViewerState:
    def __init__(self, raw_root: Path, cfg: dict, dataset_root: Path | None, scene: Path | None):
        self.raw_root = Path(raw_root)
        self.cfg = cfg
        self.dataset_root = dataset_root
        self.scene = scene
        self.readers: dict[tuple[str, str], TailReader] = {}  # (client, recording) -> reader
        self.clean_cache: dict[str, bytes] = {}
        self.meta_cache: dict[str, tuple[tuple, dict]] = {}  # recording -> (file signature, meta)
        self.lock = threading.Lock()

    def rec_dir(self, rid: str) -> Path:
        if not ID_RE.match(rid):
            raise KeyError(rid)
        d = self.raw_root / rid
        if not (d / "manifest.json").exists():
            raise KeyError(rid)
        return d


def _status(folder: Path) -> str:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("status", "?")
    except (OSError, json.JSONDecodeError):
        return "?"


def _with_fs(raw_root: Path, item: dict) -> dict:
    """Adds the recording's nominal sample rate ('fs', None if unknown) so the list can show durations in seconds."""
    rid = str(item.get("recording_id") or "")
    fs = None
    if ID_RE.match(rid):
        try:
            m = json.loads((Path(raw_root) / rid / "manifest.json").read_text(encoding="utf-8"))
            fs = float(m["source"]["fs_nominal"]) or None
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            fs = None
    return {**item, "fs": fs}


def make_handler(state: ViewerState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, default=float).encode("utf-8"), "application/json")

        def do_GET(self):  # noqa: N802
            if not _local_request(self.headers.get("Host", ""), self.headers.get("Origin")):
                return self._json({"error": "this server only answers pages served from this machine"}, 403)
            u = urlparse(self.path)
            q = parse_qs(u.query)
            parts = [p for p in u.path.split("/") if p]
            try:
                if not parts:
                    return self._file(STATIC / "index.html")
                if parts[0] == "static" and len(parts) == 2:
                    if parts[1] == "plotly.min.js":
                        p = plotly_js()
                        if p is None:
                            return self._json({"error": "plotly not installed"}, 404)
                        return self._file(p)
                    if not ID_RE.match(parts[1].replace(".", "_")):
                        return self._json({"error": "bad name"}, 400)
                    return self._file(STATIC / parts[1])
                if parts[:2] == ["api", "recordings"]:
                    return self._json([_with_fs(state.raw_root, x) for x in list_recordings(state.raw_root)])
                if parts[:2] == ["api", "scene"]:
                    if state.scene and Path(state.scene).exists():
                        return self._send(200, Path(state.scene).read_bytes(), "application/json")
                    return self._json({"error": "no CT scene built (run: emgmirror ct emg-overlay)"}, 404)
                if parts[:2] == ["api", "rec"] and len(parts) == 4:
                    folder = state.rec_dir(parts[2])
                    if parts[3] == "meta":
                        return self._meta(folder, parts[2])
                    if parts[3] == "data":
                        return self._data(folder, parts[2], int(q.get("row", ["0"])[0]), q.get("client", ["-"])[0])
                    if parts[3] == "clean":
                        return self._clean(folder, parts[2])
                return self._json({"error": "not found"}, 404)
            except KeyError:
                return self._json({"error": "unknown recording"}, 404)
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc)}, 400)

        def _file(self, p: Path):
            if not p.exists() or not p.is_file():
                return self._json({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if p.suffix == ".js":
                ctype = "text/javascript"
            self._send(200, p.read_bytes(), ctype)

        def _meta(self, folder: Path, rid: str):
            # Signature of the files the meta is built from: a closed recording never changes, so its meta
            # is computed once; a live one is rebuilt when it grew. frames.csv is not needed and is slow.
            # The dataset session files (session.json, floor.json, trials.csv) feed the floor and trial labels and
            # can appear while the viewer runs, so their state is part of the signature too (stat only, no reads).
            sig = tuple((folder / f).stat().st_mtime_ns if (folder / f).exists() else 0
                        for f in ("manifest.json", "samples.bin", "events.jsonl")) + _dataset_sig(state.dataset_root)
            with state.lock:
                hit = state.meta_cache.get(rid)
            if hit and hit[0] == sig:
                return self._json(hit[1])
            rec = RawRecording.open(folder, frames=False)
            idx = rec.records["index"]
            if len(idx) > 1 and bool(np.any(idx[1:] <= idx[:-1])):
                # a device restart: markers then need the run of every record, which only frames.csv has
                rec = RawRecording.open(folder)
            if len(rec.records) == 0:
                return self._json({"error": "no samples yet", "status": rec.manifest.get("status")}, 409)
            meta = recording_meta(rec, state.cfg, state.dataset_root)
            with state.lock:
                state.meta_cache[rid] = (sig, meta)
            return self._json(meta)

        def _data(self, folder: Path, rid: str, row: int, client: str):
            key = (client[:40], rid)
            # Status before samples: the recorder writes its last rows before it marks the manifest closed, so a
            # 'complete' read here guarantees the rows read below are all of them (the browser stops polling on it).
            status = _status(folder)
            with state.lock:
                r = state.readers.get(key)
                if r is None or row == 0:
                    r = state.readers[key] = TailReader(folder)
                if row != r.rows:  # the client lost track (reload): start again from the top
                    r = state.readers[key] = TailReader(folder)
                    row = 0
                first, block, nxt = r.read(row)
                host_ns = r.last_host_ns()
            body = struct.pack("<qii", first, block.shape[0], block.shape[1]) + block.astype("<f4").tobytes()
            self._send(200, body, "application/octet-stream",
                       {"X-Next-Row": nxt, "X-Status": status,"X-Last-Host-Ns": host_ns or "",
                        "X-Server-Ns": time.time_ns()})

        def _clean(self, folder: Path, rid: str):
            from ..dataset import clean_pin

            if _status(folder) == "recording":
                return self._json({"error": "recording still open: the live view filters in the browser"}, 409)
            with state.lock:
                body = state.clean_cache.get(rid)
            if body is None:
                r = TailReader(folder)
                first, pin, _ = r.read(0)
                mains = float(state.cfg.get("acquisition", {}).get("mains_hz", 50))
                fs = float(json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["source"]["fs_nominal"])
                y = clean_pin(pin.astype(np.float64), fs, mains)
                y[~np.isfinite(pin)] = np.nan
                body = struct.pack("<qii", first, y.shape[0], y.shape[1]) + y.astype("<f4").tobytes()
                with state.lock:
                    state.clean_cache[rid] = body
            self._send(200, body, "application/octet-stream")

    return H


def serve(raw_root: Path, cfg: dict, dataset_root: Path | None = None, scene: Path | None = None,
          port: int = 8030, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    state = ViewerState(raw_root, cfg, dataset_root, scene)
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    httpd.daemon_threads = True
    return httpd
