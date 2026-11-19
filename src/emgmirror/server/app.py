"""HTTP + WebSocket backend for the dashboard.

REST
  GET  /api/config                    channels, protocol, subject
  GET  /api/sessions                  saved sessions
  POST /api/sessions/simulate         create a synthetic session
  GET  /api/sessions/{id}/analysis    bilateral analysis (cached)
  GET  /api/sessions/{id}/anatomy     3D forearm figure + trace metadata
  GET  /api/sessions/{id}/report      standalone HTML report
  GET  /api/ports                     serial ports (for the real kit)
WebSocket
  /ws/live?session=<id>               replay a session in real time
  /ws/live?port=COM5                  live from the EMG kit (protocol from config)
Acquisition (see server/acq.py): /api/acq/*, /api/raw/*, /ws/acq, and the page /diagnostics
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..acquisition.pipeline import Acquisition
from ..acquisition.registry import channels_from_config, open_source
from ..acquisition.sources import ReplaySource, list_serial_ports
from ..analysis.bilateral import analyze
from ..anatomy.figure import STOPS, cross_section_figure, forearm_figure
from ..config import DATA_DIR, load_config
from ..live import LiveMirror
from ..report import write_report
from ..simulate.synthetic import synthetic_session
from ..storage import Session, list_sessions
from .acq import AcquisitionManager, make_router

STATIC = Path(__file__).parent / "static"


class SimulateRequest(BaseModel):
    mode: str = "mirror"
    seed: int | None = None


def plotly_js() -> Path | None:
    """plotly.min.js from the installed plotly package, so the pages work without internet."""
    try:
        import plotly

        p = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
        return p if p.exists() else None
    except ImportError:
        return None


def create_app(config_path: str | None = None, data_dir: str | Path = DATA_DIR,
               raw_dir: str | Path | None = None) -> FastAPI:
    """raw_dir: where dashboard recordings go. Default: the config's raw_dir, the same folder the CLI
    (`raw list`, `raw inspect`) and the signal viewer read, whatever --data is."""
    cfg = load_config(config_path)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    from contextlib import asynccontextmanager

    from ..config import REPO_ROOT

    raw_dir = Path(raw_dir) if raw_dir is not None else Path(cfg.get("raw_dir", "data/raw"))
    raw_dir = raw_dir if raw_dir.is_absolute() else REPO_ROOT / raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    manager = AcquisitionManager(cfg, data_dir, raw_dir)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        manager.stop()  # closes an open raw recording cleanly on server shutdown

    app = FastAPI(title="emgmirror", version="0.1.0", lifespan=lifespan)
    app.state.acq = manager
    app.include_router(make_router(manager))

    def session_dir(sid: str) -> Path:
        p = (data_dir / sid).resolve()
        if p.parent != data_dir.resolve() or not (p / "session.json").exists():
            raise HTTPException(404, f"session {sid} not found")
        return p

    def get_analysis(sid: str) -> dict:
        folder = session_dir(sid)
        cache = folder / "analysis.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        result = analyze(Session.load(folder), cfg)
        cache.write_text(json.dumps(result), encoding="utf-8")
        return result

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/diagnostics", response_class=HTMLResponse)
    def diagnostics():
        return FileResponse(STATIC / "diagnostics.html")

    @app.get("/live-check", response_class=HTMLResponse)
    def live_check():
        return FileResponse(STATIC / "live_check.html")

    @app.get("/api/config")
    def config():
        return {"channels": cfg["channels"], "protocol": cfg["protocol"], "subject": cfg["subject"],
                "fs": cfg["acquisition"]["fs"], "color_stops": STOPS}

    @app.get("/api/sessions")
    def sessions():
        return list_sessions(data_dir)

    @app.post("/api/sessions/simulate")
    def simulate(req: SimulateRequest):
        c = cfg if req.seed is None else {**cfg, "simulation": {**cfg["simulation"], "seed": req.seed}}
        s = synthetic_session(c, mode=req.mode)
        s.save(data_dir)
        return {"session_id": s.session_id}

    @app.get("/api/sessions/{sid}/analysis")
    def analysis(sid: str):
        return get_analysis(sid)

    @app.get("/api/sessions/{sid}/anatomy")
    def anatomy(sid: str, metric: str = "activation_pct"):
        summary = get_analysis(sid).get("summary")
        fig, meta = forearm_figure(cfg, summary, metric)
        section = cross_section_figure(cfg, summary, metric)
        return {"figure": json.loads(fig.to_json()), "traces": meta, "section": json.loads(section.to_json())}

    @app.get("/api/anatomy")
    def anatomy_blank():
        fig, meta = forearm_figure(cfg, None)
        return {"figure": json.loads(fig.to_json()), "traces": meta,
                "section": json.loads(cross_section_figure(cfg, None).to_json())}

    @app.get("/api/sessions/{sid}/report", response_class=HTMLResponse)
    def report(sid: str):
        folder = session_dir(sid)
        out = write_report(Session.load(folder), cfg, get_analysis(sid), folder / "report.html")
        return HTMLResponse(out.read_text(encoding="utf-8"))

    @app.get("/api/ports")
    def ports():
        try:
            return list_serial_ports()
        except Exception as exc:  # pragma: no cover - platform dependent
            return {"error": str(exc)}

    @app.websocket("/ws/live")
    async def live(ws: WebSocket, session: str | None = None, port: str | None = None):
        await ws.accept()
        try:
            if port:
                # Through the common pipeline, so lost samples keep their place in time.
                src = Acquisition(open_source(port, cfg, data_dir, channels=channels_from_config(cfg)), cfg)
            else:
                if session is None:
                    found = list_sessions(data_dir)
                    if not found:
                        s = synthetic_session(cfg)
                        s.save(data_dir)
                        session = s.session_id
                    else:
                        session = found[0]["session_id"]
                src = ReplaySource(Session.load(session_dir(session)))
            # A device handshake can take seconds: run it off the event loop, or every client stalls.
            await asyncio.to_thread(src.start)
            pipe = LiveMirror(src, cfg)
            await ws.send_json({"type": "hello", "channels": [c.__dict__ for c in src.channels],
                                "fs": src.fs, "session": session, "port": port})
            while True:
                await asyncio.sleep(0.05)
                msg = pipe.step()
                if msg is None:
                    continue
                if isinstance(src, ReplaySource):
                    t = src.position_s
                    msg["task"] = next((tr.task for tr in src.trials if tr.start_s <= t < tr.end_s), "pause")
                    msg["t"] = round(t, 2)
                await ws.send_json({"type": "data", **msg})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await ws.send_json({"type": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            try:
                src.stop()
            except Exception:
                pass

    @app.get("/static/plotly.min.js")
    def plotly_bundle():  # registered before the /static mount, so it takes precedence
        p = plotly_js()
        if p is None:
            raise HTTPException(404, "plotly is not installed")
        return FileResponse(p, media_type="text/javascript")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
