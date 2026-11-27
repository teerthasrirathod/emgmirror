"""Upload a CT, get a 3D model.

One process: serves the built viewer, accepts one CT upload per job (NIfTI or a zip of DICOM files),
runs the segmentation engine (``emgmirror.ctseg``) and the mesh pipeline (``emgmirror.ctmesh``) in a
single background worker, and serves the result folder the viewer opens.

Privacy: the derived NIfTI contains image pixels and physical geometry, not the DICOM headers.
The original upload package is retained and may still contain identifying DICOM tags. Jobs older
than ``CTAPP_RETENTION_HOURS`` (default 24 hours) are removed when a later upload creates a job;
there is no periodic cleanup timer. Logs carry job ids only, never file names.

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
from emgmirror.ctseg.config import SegConfig

DATA = Path(os.environ.get("CTAPP_DATA", "/tmp/ctapp"))
WEB = Path(os.environ.get("CTAPP_WEB", Path(__file__).resolve().parents[3] / "web" / "ct-viewer" / "dist"))
MAX_UPLOAD = int(os.environ.get("CTAPP_MAX_UPLOAD_MB", "1500")) * 1024 * 1024
RETENTION_H = float(os.environ.get("CTAPP_RETENTION_HOURS", "24"))
FAST = os.environ.get("CTAPP_FAST", "0") == "1"
DEVICE = os.environ.get("CTAPP_DEVICE", "cpu")
SEGMENTATION_PROFILE = os.environ.get("CTAPP_SEGMENTATION_PROFILE", "skeletal")
ENABLE_MOOSE = os.environ.get("CTAPP_ENABLE_MOOSE", "1").lower() not in {"0", "false", "no"}
JOB_ID_CHARS = set("0123456789abcdef")

# Validate the app's fixed pipeline configuration before accepting scans. In skeletal-first mode, a fast
# model or disabled MOOSE would otherwise make the upload appear to succeed while skipping required work.
try:
    SegConfig(build="research", profile=SEGMENTATION_PROFILE, fast=FAST, moose=ENABLE_MOOSE)
except ValueError as exc:
    raise RuntimeError(f"Invalid CTAPP segmentation configuration: {exc}") from exc

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
    """Resolve the retained upload package to a canonical NIfTI volume."""
    from emgmirror.ctapp.intake import normalize_upload

    up = next(d.glob("upload.*"))
    out = d / "ct.nii.gz"
    normalize_upload(up, d, out, max_expanded_bytes=6 * MAX_UPLOAD)
    return out


def _grid(img) -> dict:
    return {
        "dimensions_xyz": [int(v) for v in img.GetSize()],
        "spacing_mm_xyz": [float(v) for v in img.GetSpacing()],
        "origin_mm": [float(v) for v in img.GetOrigin()],
        "direction": [float(v) for v in img.GetDirection()],
        "coordinate_frame": "LPS mm (SimpleITK)",
    }


def _same_grid(a, b) -> bool:
    import numpy as np

    return (a.GetSize() == b.GetSize()
            and np.allclose(a.GetSpacing(), b.GetSpacing(), atol=1e-4)
            and np.allclose(a.GetOrigin(), b.GetOrigin(), atol=1e-3)
            and np.allclose(a.GetDirection(), b.GetDirection(), atol=1e-5))


def _labels_to_reference(src: Path, reference: Path, dst: Path) -> dict:
    """Put labels back on the canonical CT grid for a faithful CT/overlay pair."""
    import SimpleITK as sitk

    labels = sitk.ReadImage(str(src))
    ref = sitk.ReadImage(str(reference))
    source_grid = _grid(labels)
    target_grid = _grid(ref)
    if _same_grid(labels, ref):
        shutil.copy2(src, dst)
        return {"operation": "none", "resampled": False, "source_grid": source_grid, "output_grid": target_grid}
    mapped = sitk.Resample(labels, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0, labels.GetPixelID())
    sitk.WriteImage(mapped, str(dst), True)
    return {
        "operation": "nearest-neighbour labels mapped to canonical CT grid",
        "resampled": True,
        "source_grid": source_grid,
        "output_grid": _grid(mapped),
    }


def _resample_labels(src: Path, dst: Path, mm: float = 1.5) -> dict:
    """Nearest-neighbour resample of the label map to ``mm`` isotropic.

    Fine axes are reduced to at most 1.5 mm while coarser native axes stay coarse. This avoids inventing
    through-plane detail in thick-slice scans. Geometry is recorded by the caller.
    """
    import SimpleITK as sitk

    img = sitk.ReadImage(str(src))
    source_grid = _grid(img)
    sp = img.GetSpacing()
    if min(sp) >= mm * 0.95:
        shutil.copy2(src, dst)
        return {"operation": "none", "resampled": False, "target_spacing_mm_xyz": list(sp),
                "interpolator": "nearest-neighbour labels", "source_grid": source_grid,
                "output_grid": source_grid}
    target_spacing = tuple(max(float(s), mm) for s in sp)
    size = [max(1, int(round(n * s / target))) for n, s, target in zip(img.GetSize(), sp, target_spacing)]
    out = sitk.Resample(img, size, sitk.Transform(), sitk.sitkNearestNeighbor, img.GetOrigin(), target_spacing,
                        img.GetDirection(), 0, img.GetPixelID())
    sitk.WriteImage(out, str(dst), True)
    return {"operation": "nearest-neighbour mesh-grid preparation", "resampled": True,
            "target_spacing_mm_xyz": list(target_spacing), "interpolator": "nearest-neighbour labels",
            "source_grid": source_grid, "output_grid": _grid(out)}


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
               "--build", "research", "--profile", SEGMENTATION_PROFILE, "--device", DEVICE]
        if not ENABLE_MOOSE:
            cmd.append("--no-moose")
        if FAST:
            cmd.append("--fast")
        _run(cmd, job, d)
        _status(d, step="building 3D")
        res = d / "result"
        labels_native = seg / "labels_native.nii.gz"
        labels_to_ct = _labels_to_reference(seg / "labels.nii.gz", ct, labels_native)
        mesh_grid = _resample_labels(labels_native, seg / "labels_mesh.nii.gz")
        mesh_py = ("import sys; from emgmirror.ctmesh.pipeline import run, PipelineParams; "
                   "run(sys.argv[1], sys.argv[2], sys.argv[3], "
                   "params=PipelineParams(dicom_seg=False, per_structure_files=False))")
        _run([sys.executable, "-c", mesh_py, str(seg / "labels_mesh.nii.gz"), str(res), str(seg / "labels.json")],
             job, d, ok_if=res / "manifest.json",
             # one thread per mesh worker: the pool already uses the CPUs
             env={"OMP_NUM_THREADS": "1", "VTK_SMP_MAX_THREADS": "1"})
        shutil.copy2(ct, res / "ct.nii.gz")
        if labels_to_ct["resampled"]:
            shutil.copy2(seg / "labels.nii.gz", res / "labels_model_grid.nii.gz")
        shutil.copy2(labels_native, res / "labels.nii.gz")
        model_input = seg / "image.nii.gz"
        if model_input.exists():
            shutil.copy2(model_input, res / "model_input.nii.gz")
        m = json.loads((res / "manifest.json").read_text())
        m.setdefault("volumes", {})
        m["volumes"].update({"ct": "ct.nii.gz", "labels": "labels.nii.gz"})
        if labels_to_ct["resampled"]:
            m["volumes"]["labels_model_grid"] = "labels_model_grid.nii.gz"
        if model_input.exists():
            m["volumes"]["model_input"] = "model_input.nii.gz"
        intake_file = d / "intake.json"
        if intake_file.exists():
            m["intake"] = json.loads(intake_file.read_text(encoding="utf-8"))
        labels_doc = json.loads((seg / "labels.json").read_text(encoding="utf-8"))
        provenance_doc = json.loads((seg / "provenance.json").read_text(encoding="utf-8"))
        plan = provenance_doc.get("plan", {})
        m["processing"] = {
            "profile": SEGMENTATION_PROFILE,
            "detected_regions": labels_doc.get("qc", {}).get("regions", {}),
            "planned_regions": plan.get("planning_regions", {}),
            "anatomy_coverage": labels_doc.get("qc", {}).get("anatomy_coverage", []),
            "planned_models": [plan.get("base"), *plan.get("subtasks", []), *plan.get("moose", [])],
            "model_runs": [
                {key: run[key] for key in ("source", "status", "seconds") if key in run}
                for run in provenance_doc.get("models", [])
            ],
            "warnings": labels_doc.get("qc", {}).get("warnings", []),
            "geometry": {
                "viewer_volume": "ct.nii.gz",
                "viewer_labels": "labels.nii.gz",
                "viewer_grid": "canonical intake CT grid",
                "labels_to_ct_grid": labels_to_ct,
                "mesh_grid_preparation": mesh_grid,
                "model_input_volume": "model_input.nii.gz" if model_input.exists() else "ct.nii.gz",
                "model_labels": "labels_model_grid.nii.gz" if labels_to_ct["resampled"] else "labels.nii.gz",
            },
        }
        analysis_file = res / "analysis.json"
        if not analysis_file.exists():
            analysis_file.write_text(json.dumps({
                "version": 1,
                "status": "not_scheduled",
                "runs": [{"task_id": "region_aware_findings", "status": "not_scheduled"}],
                "findings": [],
                "warnings": ["No finding-analysis adapter is configured for this job."],
            }, indent=2), encoding="utf-8")
        m["analysis"] = {"file": "analysis.json"}
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
    from emgmirror.ctapp.intake import upload_suffix

    suffix = upload_suffix(name)
    if suffix is None:
        raise HTTPException(400, "Upload a CT series ZIP or a supported 3D volume (.nii, .nii.gz, .nrrd, .mha, .nhdr, .mhd, .hdr).")
    _cleanup()
    job = secrets.token_hex(12)
    d = JOBS / job
    d.mkdir()
    ext = suffix
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
