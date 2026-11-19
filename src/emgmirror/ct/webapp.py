"""Local page for loading a CT scan and looking at the result. Research use only.

Everything stays on this computer: the server is meant to be bound to 127.0.0.1,
requests from any other origin or host name are refused, and the pipeline runs in
a local subprocess. Uploaded scans and results live in ``results/ct_jobs/<id>/``
(git-ignored) until the user deletes them.

  GET    /ct                               the page
  GET    /api/ct/jobs                      list jobs
  POST   /api/ct/jobs                      new job  {"name": "..."} or {"path": "C:/folder/on/this/pc"}
  PUT    /api/ct/jobs/{id}/files?name=...  upload one file (raw body); .zip files are unpacked
  POST   /api/ct/jobs/{id}/start           queue the build
  GET    /api/ct/jobs/{id}                 status, and the plain-language summary when done
  GET    /api/ct/jobs/{id}/viewer          viewer.html
  GET    /api/ct/jobs/{id}/files/{path}    download an output file
  DELETE /api/ct/jobs/{id}                 delete the upload and all results
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import DISCLAIMER

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)
MAX_JOB_BYTES = 6 * 1024 ** 3
MAX_FILES = 20_000
ID_RE = re.compile(r"^[0-9a-f]{16}$")
Runner = Callable[[Path, Path, Path], int]  # (input_dir, out_dir, progress_file) -> exit code


def _host(value: str) -> str:
    return (urlsplit(value if "//" in value else f"//{value}").hostname or "").lower()


def local_guard(hosts: frozenset[str] = LOCAL_HOSTS) -> Callable[[Request], None]:
    """Dependency that refuses requests not made from a page served by this machine.

    Blocks other websites from driving the local server through the browser
    (cross-site requests and DNS rebinding)."""
    def check(request: Request) -> None:
        if _host(request.headers.get("host", "")) not in hosts:
            raise HTTPException(403, "this server only answers on localhost")
        origin = request.headers.get("origin")
        if origin and _host(origin) not in hosts:
            raise HTTPException(403, "cross-site requests are refused")
    return check


local_only = local_guard()


def safe_relpath(name: str) -> Path:
    """Turn a client-supplied file name into a harmless relative path."""
    parts = [re.sub(r"[^A-Za-z0-9._-]", "_", p)[:80] for p in re.split(r"[\\/]+", name)]
    parts = [p for p in parts if p and set(p) != {"."}][-6:]
    parts = [f"_{p}" if WINDOWS_RESERVED.match(p) else p for p in parts]  # CON, NUL ... are devices on Windows
    if not parts:
        raise HTTPException(400, "bad file name")
    return Path(*parts)


def safe_unzip(zip_path: Path, dest: Path, budget: int) -> int:
    """Extract without following paths outside `dest`; returns bytes written."""
    written = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > budget:
            raise HTTPException(413, "archive is too large")
        for i, info in enumerate(infos):
            try:
                target = dest / safe_relpath(info.filename)
            except HTTPException:
                continue
            target = target.with_name(f"{i:05d}_{target.name}")  # keeps same-named files apart
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            written += info.file_size
    return written


def _pid_alive(pid: object) -> bool:
    """Whether a process with this id is running (never signals it)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)  # POSIX: signal 0 only checks
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def subprocess_runner(input_dir: Path, out_dir: Path, progress_file: Path) -> int:
    """Run the pipeline in its own process so memory is returned when it ends."""
    with open(out_dir.parent / "log.txt", "w", encoding="utf-8") as log:
        return subprocess.run([sys.executable, "-m", "emgmirror.ct.pipeline", "--input", str(input_dir),
                               "--out", str(out_dir), "--status", str(progress_file)],
                              stdout=log, stderr=subprocess.STDOUT).returncode


def _read_json(path: Path) -> dict:
    """Read a small JSON file that another thread or process may be replacing right now."""
    for _ in range(20):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (PermissionError, json.JSONDecodeError):  # Windows refuses a read that overlaps a replace
            time.sleep(0.02)
        except FileNotFoundError:
            return {}
    return {}


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    for attempt in range(40):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # a reader has the target open (Windows)
            if attempt == 39:
                raise
            time.sleep(0.02)


class Jobs:
    """Job folders under `root`. status.json belongs to this class; the pipeline process
    reports through its own progress.json, so the two never write the same file."""

    def __init__(self, root: Path, runner: Runner):
        self.root, self.runner = Path(root), runner
        self.queue: queue.Queue[str] = queue.Queue()
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None

    def dir(self, jid: str) -> Path:
        if not ID_RE.match(jid) or not (self.root / jid / "status.json").exists():
            raise HTTPException(404, "job not found")
        return self.root / jid

    def read(self, jid: str) -> dict:
        folder = self.dir(jid)
        with self.lock:
            st = _read_json(folder / "status.json")
        if st.get("state") == "running":
            prog = _read_json(folder / "progress.json")
            st.update({k: prog[k] for k in ("step", "progress", "error") if prog.get(k) is not None})
        return st

    def update(self, jid: str, change: Callable[[dict], None]) -> dict:
        with self.lock:
            path = self.root / jid / "status.json"
            st = _read_json(path) if path.exists() else {}
            change(st)
            write_json_atomic(path, st)
            return st

    def write(self, jid: str, **changes) -> dict:
        return self.update(jid, lambda st: st.update(changes))

    @staticmethod
    def public(st: dict) -> dict:
        """What a browser may see: never the folder path on this computer."""
        return {k: v for k, v in st.items() if k not in ("source", "server_pid", "next_index")}

    def create(self, name: str, source: Path | None) -> dict:
        jid = secrets.token_hex(8)
        (self.root / jid / "upload").mkdir(parents=True)
        return self.write(jid, id=jid, name=name[:80], state="created", created=time.time(), bytes=0, files=0,
                          source=str(source) if source else None, step="", progress=0.0)

    def start(self, jid: str) -> dict:
        self.dir(jid)
        with self.lock:
            st = self.read(jid)
            if st["state"] not in ("created", "failed"):
                raise HTTPException(409, f"job is already {st['state']}")
            if not st.get("source") and not st.get("files"):
                raise HTTPException(400, "no files were uploaded")
            (self.root / jid / "progress.json").unlink(missing_ok=True)
            st = self.write(jid, state="queued", step="Waiting for its turn", progress=0.0, error=None,
                            server_pid=os.getpid())
            self.queue.put(jid)
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._work, daemon=True)
                self.worker.start()
        return st

    def _work(self) -> None:
        """One job at a time, forever. Nothing a job does may stop this thread."""
        while True:
            jid = self.queue.get()
            try:
                self._run(jid)
            except Exception as exc:
                try:
                    self.write(jid, state="failed", finished=time.time(), error=f"Internal error: {exc}")
                except Exception:
                    pass

    def _run(self, jid: str) -> None:
        folder = self.root / jid
        if not folder.exists():
            return
        st = self.write(jid, state="running", started=time.time(), server_pid=os.getpid())
        out = folder / "out"
        out.mkdir(exist_ok=True)
        error = None
        try:
            code = self.runner(Path(st["source"]) if st.get("source") else folder / "upload", out,
                               folder / "progress.json")
        except Exception as exc:  # the runner itself broke
            code, error = 1, str(exc)
        if not folder.exists():
            return
        if code == 0 and (out / "viewer.html").exists():
            self.write(jid, state="done", progress=1.0, step="Finished", finished=time.time(), error=None)
        else:
            error = error or _read_json(folder / "progress.json").get("error")
            self.write(jid, state="failed", finished=time.time(),
                       error=error or "The scan could not be processed. See log.txt in the job folder.")


def create_ct_router(jobs_dir: Path | str = "results/ct_jobs", runner: Runner = subprocess_runner,
                     extra_hosts: tuple[str, ...] = ()) -> APIRouter:
    """`extra_hosts` adds accepted Host names (tests use it; leave empty otherwise)."""
    jobs = Jobs(Path(jobs_dir), runner)
    jobs.root.mkdir(parents=True, exist_ok=True)
    for d in jobs.root.iterdir():  # a job still queued or running was cut off when its server stopped
        if ID_RE.match(d.name) and (d / "status.json").exists():
            st0 = _read_json(d / "status.json")
            if st0.get("state") in ("queued", "running") and not _pid_alive(st0.get("server_pid")):
                jobs.write(d.name, state="failed", finished=time.time(),
                           error="Interrupted: the server was stopped before this scan finished. Start it again.")
    r = APIRouter(dependencies=[Depends(local_guard(LOCAL_HOSTS | set(extra_hosts)))])

    @r.get("/ct", response_class=HTMLResponse)
    def page():
        return FileResponse(STATIC / "ct.html")

    @r.get("/api/ct/jobs")
    def list_jobs():
        out = []
        for d in jobs.root.iterdir():
            if ID_RE.match(d.name) and (d / "status.json").exists():
                st = jobs.read(d.name)
                out.append({k: st.get(k) for k in ("id", "name", "state", "created", "step", "progress")})
        return sorted(out, key=lambda j: j.get("created") or 0, reverse=True)

    @r.post("/api/ct/jobs")
    async def new_job(request: Request):
        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except ValueError:
                raise HTTPException(400, "the request body is not valid JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "the request body must be a JSON object")
        source = None
        if body.get("path"):
            source = Path(str(body["path"]).strip().strip('"')).expanduser()
            if not source.is_dir():
                raise HTTPException(400, "that folder does not exist on this computer")
        return jobs.public(jobs.create(str(body.get("name") or (source.name if source else "My scan")), source))

    @r.put("/api/ct/jobs/{jid}/files")
    async def upload(jid: str, name: str, request: Request):
        folder = jobs.dir(jid)
        rel = safe_relpath(name)
        slot: dict = {}

        def reserve(st: dict) -> None:  # runs under the lock, so parallel uploads get distinct numbers
            if st["state"] != "created":
                raise HTTPException(409, "this job no longer accepts files")
            if st.get("source"):
                raise HTTPException(409, "this job reads a folder on this computer and takes no uploads")
            if st["files"] >= MAX_FILES:
                raise HTTPException(413, "too many files")
            index = int(st.get("next_index", st["files"]))  # never reused, even after a failed upload
            slot.update(index=index, budget=MAX_JOB_BYTES - st["bytes"])
            st["files"] += 1
            st["next_index"] = index + 1
        jobs.update(jid, reserve)
        target = folder / "upload" / rel.with_name(f"{slot['index']:05d}_{rel.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        is_zip, unpacked = target.suffix.lower() == ".zip", target.with_suffix("")
        size, too_big = 0, HTTPException(413, "upload is larger than the 6 GB limit")
        with open(target, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > slot["budget"]:
                    break
                out.write(chunk)
        if size > slot["budget"]:
            target.unlink(missing_ok=True)
            raise too_big
        if is_zip and zipfile.is_zipfile(target):
            try:
                size = safe_unzip(target, unpacked, slot["budget"] - size)
            except Exception as exc:  # damaged, password-protected or too large: keep nothing of it
                shutil.rmtree(unpacked, ignore_errors=True)
                jobs.update(jid, lambda s_: s_.update(files=max(s_["files"] - 1, 0)))
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(400, "the zip file is damaged or password-protected") from None
            finally:
                target.unlink(missing_ok=True)
        else:
            is_zip = False

        def account(st: dict) -> None:
            st["bytes"] += size
        st = jobs.update(jid, account)
        if st["bytes"] > MAX_JOB_BYTES:  # parallel uploads together went over the limit
            if is_zip:
                shutil.rmtree(unpacked, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            jobs.update(jid, lambda s_: s_.update(bytes=s_["bytes"] - size))
            raise too_big
        return {"bytes": st["bytes"], "files": st["files"]}

    @r.post("/api/ct/jobs/{jid}/start")
    def start(jid: str):
        return jobs.public(jobs.start(jid))

    @r.get("/api/ct/jobs/{jid}")
    def status(jid: str):
        st = jobs.public(jobs.read(jid))
        summary = jobs.dir(jid) / "out" / "summary.json"
        if st.get("state") == "done" and summary.exists():
            st["summary"] = json.loads(summary.read_text(encoding="utf-8"))
            st["downloads"] = sorted(str(p.relative_to(summary.parent)).replace("\\", "/")
                                     for p in summary.parent.rglob("*") if p.is_file() and p.name != "summary.json")
        return st

    @r.get("/api/ct/jobs/{jid}/viewer", response_class=HTMLResponse)
    def viewer(jid: str):
        path = jobs.dir(jid) / "out" / "viewer.html"
        if not path.exists():
            raise HTTPException(404, "the viewer is not ready yet")
        return FileResponse(path, media_type="text/html")

    @r.get("/api/ct/jobs/{jid}/files/{rel:path}")
    def download(jid: str, rel: str):
        out = (jobs.dir(jid) / "out").resolve()
        path = (out / rel).resolve()
        if out not in path.parents or not path.is_file():
            raise HTTPException(404, "file not found")
        return FileResponse(path, filename=path.name)

    @r.delete("/api/ct/jobs/{jid}")
    def delete(jid: str):
        folder = jobs.dir(jid)
        if jobs.read(jid).get("state") in ("queued", "running"):
            raise HTTPException(409, "wait until processing has finished")
        shutil.rmtree(folder)
        return {"deleted": jid}

    return r


def create_ct_app(jobs_dir: Path | str = "results/ct_jobs", runner: Runner = subprocess_runner,
                  extra_hosts: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI(title="emgmirror ct", description=DISCLAIMER, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_ct_router(jobs_dir, runner, extra_hosts))

    @app.get("/", include_in_schema=False, dependencies=[Depends(local_guard(LOCAL_HOSTS | set(extra_hosts)))])
    def root() -> RedirectResponse:  # the plain address opens the page
        return RedirectResponse("/ct")

    return app
