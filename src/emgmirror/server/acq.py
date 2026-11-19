"""Server-side acquisition manager and its HTTP/WebSocket routes.

One acquisition at a time. A background thread pumps the source, so integrity accounting and raw
recording continue even when no browser is connected; browsers only read snapshots.

REST
  GET  /api/acq/status                 diagnostic report (stages, rates, counts, channels, recording)
  POST /api/acq/start   {source}       open a source (esp32:COM9, emulator[:modes], virtual, replay:<id>, text:COM9)
  POST /api/acq/stop                   close it (stops a recording first, marked complete)
  POST /api/acq/record/start {label, arm}
  POST /api/acq/record/stop
  POST /api/acq/mark    {label}        marker in the raw recording
  GET  /api/raw                        raw recordings
  GET  /api/raw/{id}/integrity         integrity of a raw recording (re-derived from its indices)
WebSocket
  /ws/acq                              {status, waveform} about 10 times per second
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..acquisition import safety
from ..acquisition.livecheck import Cue, CueRunner, LiveFeed
from ..acquisition.pipeline import Acquisition
from ..acquisition.registry import open_source
from ..recording import RawRecorder, RawRecording, list_recordings


class StartRequest(BaseModel):
    source: str


class RecordRequest(BaseModel):
    label: str = ""
    arm: str | None = None
    notes: str = ""


class MarkRequest(BaseModel):
    label: str


class CheckRequest(BaseModel):
    label: str = "live-check"
    arm: str | None = None
    cues: list[dict] | None = None  # [{task, say, seconds, level}]; default: livecheck.DEFAULT_CUES


class AcquisitionManager:
    def __init__(self, cfg: dict, data_dir: Path, raw_dir: Path):
        self.cfg = cfg
        self.data_dir = data_dir
        self.raw_dir = raw_dir
        self.acq: Acquisition | None = None
        self.spec: str | None = None
        self.error: str | None = None
        self.last_recording: dict | None = None
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()  # one start at a time, without holding _lock through a handshake
        self._stops = 0  # counts stop() calls, so a Stop pressed during a handshake is not lost
        self._thread: threading.Thread | None = None
        self._run = False
        self.feed: LiveFeed | None = None
        self.check: CueRunner | None = None
        self.last_check: dict | None = None

    def start(self, spec: str) -> dict:
        with self._start_lock:
            self.stop()
            stops = self._stops
            self.error = None
            # The device handshake (up to handshake_timeout_s) runs outside _lock, so status() and the
            # WebSocket snapshots keep answering meanwhile.
            try:
                src = open_source(spec, self.cfg, self.data_dir)
                acq = Acquisition(src, self.cfg)
                acq.start()
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                raise
            with self._lock:
                if self._stops != stops:  # Stop was pressed while the source was connecting
                    acq.stop()
                    return self.status()
                self.acq, self.spec = acq, spec
                self.feed = LiveFeed(acq.fs, len(acq.channels), self.cfg)
                self._run = True
                self._thread = threading.Thread(target=self._pump, name="acq-pump", daemon=True)
                self._thread.start()
                return self.status()

    def _pump(self) -> None:
        while self._run:
            acq = self.acq
            if acq is None:
                break
            try:
                feed = self.feed
                for c in acq.poll():
                    if feed is not None:
                        feed.update(c)
            except Exception as exc:  # keep the reason visible in the dashboard
                self.error = f"{type(exc).__name__}: {exc}"
                with self._lock:
                    if acq.recorder is not None:
                        self.last_recording = acq.stop_recording(f"pipeline error: {exc}", status="interrupted").summary()
            time.sleep(0.005)  # 5 ms poll: the live check wants the least possible delay

    def check_start(self, req: CheckRequest) -> dict:
        with self._lock:
            if self.acq is None or self.feed is None:
                raise RuntimeError("connect a source first")
            if self.check is not None and not self.check.done:
                raise RuntimeError("a live check is already running")
            guard = None
            if self.acq.info.is_real_hardware:
                problems = safety.check_before_person_recording()
                if problems:
                    raise RuntimeError("BLOCKED: " + " ".join(problems))
                guard = lambda: ("mains (AC) power connected" if safety.ac_power_connected() else None)  # noqa: E731
            own = self.acq.recorder is None
            if own:
                self.record_start(RecordRequest(label=req.label, arm=req.arm, notes="live check with cues"))
            self.feed.rest_env = None

            def done(runner: CueRunner) -> None:
                lat = self.feed.latency() if self.feed else None
                if self.acq is not None:
                    self.acq.mark("check_summary", aborted=runner.aborted, latency=lat)
                rec = None
                if own and self.acq is not None and self.acq.recorder is not None:
                    status = "interrupted" if runner.aborted else "complete"
                    rec = self.acq.stop_recording(runner.aborted or "live check finished", status=status).summary()
                    self.last_recording = rec
                self.last_check = {"aborted": runner.aborted, "latency": lat, "recording": rec,
                                   "rest_env": self.feed.rest_env if self.feed else None}

            cues = [Cue(**c) for c in req.cues] if req.cues else None
            self.check = CueRunner(self.acq, self.feed, cues=cues, on_done=done, guard=guard)
            self.check.start()
            return {"started": True, "total_s": self.check.total_s}

    def check_stop(self) -> None:
        if self.check is not None and not self.check.done:
            self.check.stop("stopped from the page")

    def stop(self) -> None:
        self._stops += 1
        if self.check is not None and not self.check.done:
            self.check.stop("source stopped")
            t_end = time.time() + 2
            while not self.check.done and time.time() < t_end:
                time.sleep(0.02)
        with self._lock:
            self._run = False
            if self._thread is not None:
                self._thread.join(timeout=1.0)
                self._thread = None
            if self.acq is not None:
                if self.acq.recorder is not None:
                    self.last_recording = self.acq.stop_recording("source stopped").summary()
                try:
                    self.acq.stop()
                except Exception:
                    pass
            self.acq = None
            self.spec = None

    def record_start(self, req: RecordRequest) -> dict:
        with self._lock:
            if self.acq is None:
                raise RuntimeError("no source running")
            if self.acq.recorder is not None:  # check before creating a folder, or a refusal leaves an orphan
                raise RuntimeError("already recording")
            rec = RawRecorder(self.raw_dir, self.acq.info, label=req.label or "dashboard",
                              metadata={"label": req.label, "arm": req.arm, "notes": req.notes,
                                        "mode": "dashboard free run", "source_spec": self.spec},
                              config_snapshot={"acquisition": self.cfg["acquisition"],
                                               "hardware": self.cfg.get("hardware")})
            self.acq.start_recording(rec)
            return rec.summary()

    def record_stop(self) -> dict:
        with self._lock:
            if self.acq is None or self.acq.recorder is None:
                raise RuntimeError("not recording")
            rec = self.acq.stop_recording("stopped from the dashboard")
            self.last_recording = rec.summary()
            return self.last_recording

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self.acq is None:
                return {"type": "status", "running": False, "spec": None, "error": self.error,
                        "last_recording": self.last_recording}
            r = self.acq.report()
            r.update(running=True, spec=self.spec, error=self.error, last_recording=self.last_recording,
                     channel_names=[c.name for c in self.acq.channels])
            return r

    def waveform(self) -> dict[str, Any]:
        with self._lock:
            if self.acq is None:
                return {"seconds": 0, "series": []}
            return self.acq.monitor.waveform(seconds=2.0, points=500)


def make_router(mgr: AcquisitionManager) -> APIRouter:
    r = APIRouter()

    @r.get("/api/acq/status")
    def status():
        return mgr.status()

    @r.post("/api/acq/start")
    def start(req: StartRequest):
        try:
            return mgr.start(req.source)
        except Exception as exc:
            raise HTTPException(409, f"{type(exc).__name__}: {exc}")

    @r.post("/api/acq/stop")
    def stop():
        mgr.stop()
        return mgr.status()

    @r.post("/api/acq/record/start")
    def record_start(req: RecordRequest):
        try:
            return mgr.record_start(req)
        except Exception as exc:
            raise HTTPException(409, str(exc))

    @r.post("/api/acq/record/stop")
    def record_stop():
        try:
            return mgr.record_stop()
        except Exception as exc:
            raise HTTPException(409, str(exc))

    @r.post("/api/acq/mark")
    def mark(req: MarkRequest):
        if mgr.acq is None or mgr.acq.recorder is None:
            raise HTTPException(409, "not recording")
        mgr.acq.mark(req.label)
        return {"ok": True}

    @r.get("/api/debug/threads")
    def debug_threads():
        """Where every thread of this server is right now (local diagnostics)."""
        import sys
        import traceback

        names = {t.ident: t.name for t in threading.enumerate()}
        return {names.get(tid, str(tid)): traceback.format_stack(frame)[-6:]
                for tid, frame in sys._current_frames().items()}

    @r.get("/api/raw")
    def raw_list():
        return list_recordings(mgr.raw_dir)

    @r.get("/api/raw/{rid}/integrity")
    def raw_integrity(rid: str):
        p = (mgr.raw_dir / rid).resolve()
        if p.parent != mgr.raw_dir.resolve() or not (p / "manifest.json").exists():
            raise HTTPException(404, f"raw recording {rid} not found")
        integ = RawRecording.open(p).integrity()
        integ.pop("markers", None)
        return integ

    @r.post("/api/acq/check/start")
    def check_start(req: CheckRequest):
        try:
            return mgr.check_start(req)
        except Exception as exc:
            raise HTTPException(409, str(exc))

    @r.post("/api/acq/check/stop")
    def check_stop():
        mgr.check_stop()
        return {"ok": True}

    @r.websocket("/ws/live-check")
    async def ws_live_check(ws: WebSocket):
        """Pushes new 20 ms items as soon as they exist; the page reports its render times back."""
        await ws.accept()
        seq = -1

        async def receive_acks():
            while True:
                msg = await ws.receive_json()
                feed = mgr.feed
                if feed is not None and "t_host" in msg and "rendered" in msg:
                    feed.note_screen((float(msg["rendered"]) - float(msg["t_host"])) * 1000.0)

        acks = asyncio.create_task(receive_acks())
        try:
            n = 0
            while not acks.done():
                feed = mgr.feed
                items = feed.since(seq) if feed is not None else []
                if items:
                    seq = items[-1]["seq"]
                msg = {"t_send": time.time(), "items": items}
                if n % 5 == 0:
                    acq = mgr.acq
                    msg.update(cue=mgr.check.state() if mgr.check is not None else {"running": False},
                               running=acq is not None, last_check=mgr.last_check,
                               rest=feed.rest_env if feed else None,
                               latency=feed.latency() if feed else None,
                               channels=[c.name for c in acq.channels] if acq else [],
                               source=acq.info.label if acq else None,
                               real=acq.info.is_real_hardware if acq else None,
                               recording=acq.recorder.summary() if acq and acq.recorder else None)
                await ws.send_json(msg)
                n += 1
                await asyncio.sleep(0.01)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            acks.cancel()

    @r.websocket("/ws/acq")
    async def ws_acq(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                # status()/waveform() take the manager lock, which stop() holds while joining threads:
                # wait for it in a worker thread, never on the event loop.
                status = await asyncio.to_thread(mgr.status)
                waveform = await asyncio.to_thread(mgr.waveform)
                await ws.send_json({"status": status, "waveform": waveform})
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    return r
