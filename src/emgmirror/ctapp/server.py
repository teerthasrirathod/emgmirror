"""Upload a CT, get a 3D model.

One process: serves the built viewer, accepts one CT upload per job (NIfTI or a zip of DICOM files),
runs the segmentation engine (``emgmirror.ctseg``) and the mesh pipeline (``emgmirror.ctmesh``) in a
single background worker, and serves the result folder the viewer opens.

Privacy: uploads are converted to NIfTI (pixels + geometry only; DICOM headers are dropped) and the
original upload is deleted straight away. Jobs older than ``CTAPP_RETENTION_HOURS`` are deleted.
Logs carry job ids only, never file names.

Research use, not for diagnosis or surgical planning.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DATA = Path(os.environ.get("CTAPP_DATA", "/tmp/ctapp"))
WEB = Path(os.environ.get("CTAPP_WEB", Path(__file__).resolve().parents[3] / "web" / "ct-viewer" / "dist"))
MAX_UPLOAD = int(os.environ.get("CTAPP_MAX_UPLOAD_MB", "1500")) * 1024 * 1024
RETENTION_H = float(os.environ.get("CTAPP_RETENTION_HOURS", "24"))
FAST = os.environ.get("CTAPP_FAST", "0") == "1"
DEVICE = os.environ.get("CTAPP_DEVICE", "cpu")
JOB_ID_CHARS = set("0123456789abcdef")

JOBS = DATA / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
work: "queue.Queue[str]" = queue.Queue()
app = FastAPI(title="CT to 3D", docs_url=None, redoc_url=None)

STEPS = ["received", "reading", "segmenting", "building 3D", "done"]


def _job_dir(job: str) -> Path:
    if len(job) != 24 or not set(job) <= JOB_ID_CHARS:
        raise HTTPException(404, "not found")
    d = JOBS / job
    if not d.is_dir():
        raise HTTPException(404, "not found")
    return d


def _status(d: Path, **kw) -> dict:
    f = d / "status.json"
    st = json.loads(f.read_text()) if f.exists() else {}
    st.update(kw, updated=time.time())
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(f)
    return st


def _log(job: str, msg: str) -> None:
    print(f"[ctapp] job={job} {msg}", flush=True)


def _cleanup() -> None:
    cutoff = time.time() - RETENTION_H * 3600
    for d in JOBS.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


# ------------------------------------------------------------------ conversion
def _to_nifti(job: str, d: Path) -> Path:
    """Turn the upload into ``ct.nii.gz`` and delete the upload."""
    import SimpleITK as sitk

    up = next(d.glob("upload.*"))
    out = d / "ct.nii.gz"
    try:
        if up.name.endswith((".nii", ".nii.gz")):
            img = sitk.ReadImage(str(up))
            sitk.WriteImage(img, str(out), True)
        else:  # zip of DICOM files
            from emgmirror.ct.dicom import choose_primary, load_series, scan_series, write_nifti
            from emgmirror.ct.webapp import safe_unzip

            src = d / "dicom"
            src.mkdir()
            safe_unzip(up, src, budget=6 * MAX_UPLOAD)
            series = scan_series(src)
            if not series:
                raise ValueError("No CT images were found in the zip. Zip the folder that holds the scan's DICOM files.")
            info, _why = choose_primary(series)
            write_nifti(load_series(info), out)
            shutil.rmtree(src, ignore_errors=True)
    finally:
        up.unlink(missing_ok=True)
    return out


def _resample_labels(src: Path, dst: Path, mm: float = 1.5) -> None:
    """Nearest-neighbour resample of the label map to ``mm`` isotropic.

    The segmentation model works at 1.5 mm, so meshing its output at the scan's native (finer) grid adds
    time but no information. Geometry (origin, direction) is kept exactly.
    """
    import SimpleITK as sitk

    img = sitk.ReadImage(str(src))
    sp = img.GetSpacing()
    if min(sp) >= mm * 0.95:
        shutil.copy2(src, dst)
        return
    size = [max(1, int(round(n * s / mm))) for n, s in zip(img.GetSize(), sp)]
    out = sitk.Resample(img, size, sitk.Transform(), sitk.sitkNearestNeighbor, img.GetOrigin(), (mm, mm, mm),
                        img.GetDirection(), 0, img.GetPixelID())
    sitk.WriteImage(out, str(dst), True)


def _run(cmd: list[str], job: str, d: Path, ok_if: Path | None = None, env: dict | None = None) -> None:
    log = d / "log.txt"
    with log.open("a") as fh:
        p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=3 * 3600,
                           env={**os.environ, **(env or {})})
    if p.returncode != 0 and ok_if is not None and ok_if.exists():
        return  # finished with QC warnings on some structures: the result is still usable
    if p.returncode != 0:
        lines = log.read_text(errors="replace").strip().splitlines()
        # engine output names no patient data (the input is a de-identified NIfTI); keep it for operators
        for ln in lines[-40:]:
            print(f"[ctapp] job={job} | {ln[:300]}", flush=True)
        tail = lines[-3:]
        msg = next((t for t in reversed(tail) if t.startswith("refused:")), None)
        raise RuntimeError(msg.replace("refused:", "").strip() if msg else "Processing failed. Try a thinner-slice CT series.")


def _process(job: str) -> None:
    d = JOBS / job
    t0 = time.time()
    try:
        _status(d, state="running", step="reading")
        ct = _to_nifti(job, d)
        _status(d, step="segmenting")
        seg = d / "seg"
        cmd = [sys.executable, "-m", "emgmirror.ctseg", "run", "--input", str(ct), "--out", str(seg),
               "--build", "research", "--tasks", "", "--no-moose", "--device", DEVICE]
        if FAST:
            cmd.append("--fast")
        _run(cmd, job, d)
        _status(d, step="building 3D")
        res = d / "result"
        _resample_labels(seg / "labels.nii.gz", seg / "labels_mesh.nii.gz")
        mesh_py = ("import sys; from emgmirror.ctmesh.pipeline import run, PipelineParams; "
                   "run(sys.argv[1], sys.argv[2], sys.argv[3], "
                   "params=PipelineParams(dicom_seg=False, per_structure_files=False))")
        _run([sys.executable, "-c", mesh_py, str(seg / "labels_mesh.nii.gz"), str(res), str(seg / "labels.json")],
             job, d, ok_if=res / "manifest.json",
             # one thread per mesh worker: the pool already uses the CPUs
             env={"OMP_NUM_THREADS": "1", "VTK_SMP_MAX_THREADS": "1"})
        shutil.copy2(ct, res / "ct.nii.gz")
        m = json.loads((res / "manifest.json").read_text())
        m.setdefault("volumes", {})
        m["volumes"].update({"ct": "ct.nii.gz", "labels": "labels.nii.gz"})
        if not (res / "labels.nii.gz").exists():
            shutil.copy2(seg / "labels.nii.gz", res / "labels.nii.gz")
        m.setdefault("study", {"study_date": None, "series_description": "Uploaded CT", "study_hash": job[:12]})
        (res / "manifest.json").write_text(json.dumps(m))
        shutil.rmtree(seg, ignore_errors=True)
        _status(d, state="done", step="done", seconds=round(time.time() - t0))
        _log(job, f"done in {time.time() - t0:.0f}s")
    except Exception as e:  # noqa: BLE001 - report every failure to the user plainly
        _status(d, state="failed", error=str(e)[:400] or "Processing failed.")
        _log(job, f"failed: {type(e).__name__}")


def _worker() -> None:
    while True:
        job = work.get()
        try:
            _process(job)
        finally:
            work.task_done()


threading.Thread(target=_worker, daemon=True).start()


# ------------------------------------------------------------------ API
# Cloud Run limits one HTTP/1 request to 32 MB, so the browser sends the scan in parts:
# create -> part 0..n (in order) -> start.
@app.post("/api/jobs")
def create_job(name: str = "scan.zip"):
    low = name.lower()
    if not low.endswith((".zip", ".nii", ".nii.gz")):
        raise HTTPException(400, "Upload a .zip of the DICOM folder, or a .nii / .nii.gz file.")
    _cleanup()
    job = secrets.token_hex(12)
    d = JOBS / job
    d.mkdir()
    ext = ".nii.gz" if low.endswith(".nii.gz") else Path(low).suffix
    (d / f"upload{ext}").touch()
    _status(d, state="uploading", step="received", created=time.time(), parts=0, size=0)
    return {"job": job}


@app.put("/api/jobs/{job}/part")
async def upload_part(job: str, n: int, request: Request):
    d = _job_dir(job)
    st = json.loads((d / "status.json").read_text())
    if st.get("state") != "uploading":
        raise HTTPException(409, "This upload is already complete.")
    if n != st["parts"]:
        raise HTTPException(409, f"Expected part {st['parts']}.")
    up = next(d.glob("upload.*"))
    size = st["size"]
    with up.open("ab") as fh:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD:
                fh.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, f"The scan is larger than {MAX_UPLOAD // 1024 // 1024} MB.")
            fh.write(chunk)
    _status(d, parts=n + 1, size=size)
    return {"received": n + 1, "size": size}


@app.post("/api/jobs/{job}/start")
def start_job(job: str):
    d = _job_dir(job)
    st = json.loads((d / "status.json").read_text())
    if st.get("state") != "uploading":
        return {"job": job, "state": st.get("state")}
    if not st.get("size"):
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, "The upload was empty.")
    _status(d, state="queued", step="received")
    work.put(job)
    _log(job, f"queued ({st['size'] // 1024 // 1024} MB)")
    return {"job": job, "position": work.qsize()}


@app.get("/api/jobs/{job}")
def job_status(job: str):
    d = _job_dir(job)
    st = json.loads((d / "status.json").read_text())
    st["steps"] = STEPS
    return st


@app.delete("/api/jobs/{job}")
def delete_job(job: str):
    d = _job_dir(job)
    shutil.rmtree(d, ignore_errors=True)
    return {"deleted": job}


@app.get("/results/{job}/{path:path}")
def result_file(job: str, path: str):
    root = (_job_dir(job) / "result").resolve()
    f = (root / path).resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f)


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True, "queue": work.qsize()})


if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
