"""Orchestration: one call from a CT to ``labels.nii.gz`` + ``labels.json`` + ``provenance.json``.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from . import CONTRACT_VERSION, DISCLAIMER, ENGINE_VERSION, allowlist, device, inputs, ontology, regions
from .config import SegConfig
from .fusion import fuse
from .qc import structure_qc
from .runners import DefaultRunner, ModelResult, Runner, disable_totalseg_usage_stats, usage_stats_disabled


@dataclass
class SegResult:
    out_dir: Path
    labels_path: Path
    labels_json: dict
    provenance: dict


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _scrub(d):
    """Drop file paths from TotalSegmentator's run report (scan file names can identify a patient)."""
    if isinstance(d, dict):
        return {k: _scrub(v) for k, v in d.items() if k not in ("input", "output", "input_path", "output_path")}
    if isinstance(d, list):
        return [_scrub(v) for v in d]
    return d


def _v(pkg: str) -> str | None:
    try:
        return importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return None


def _write_label_nifti(img: sitk.Image, path: Path) -> None:
    from emgmirror.ct.dicom import write_nifti  # reuses the disclaimer-in-header writer
    write_nifti(sitk.Cast(img, sitk.sitkUInt16), path)


def segment(input_path: Path | str, out_dir: Path | str, cfg: SegConfig | None = None, *,
            runner: Runner | None = None, dicom: bool = False, progress=None) -> SegResult:
    """Segment one scan. ``input_path`` is a NIfTI file, or a DICOM folder with ``dicom=True``.

    Raises :class:`emgmirror.ctseg.inputs.InputRefused` (plain-language reason) for unusable input and
    :class:`emgmirror.ctseg.allowlist.LicenceError` if anything outside the allowlist is configured."""
    cfg = cfg or SegConfig()
    say = progress or (lambda msg: None)
    t_start = time.time()
    timings: dict[str, float] = {}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_path)

    # 0. licences first: nothing runs if the configuration itself is not allowed in this build
    is_device = cfg.build == "device"
    allowlist.check_config(cfg.licence_view())
    allowlist.check_weights_dir(cfg.weights_dir, cfg.build)

    # 1. input
    t = time.time()
    if dicom:
        img, scan = inputs.load_dicom_folder(input_path, cfg.max_voxels, cfg.refuse_voxels)
    else:
        img, scan = inputs.load_nifti(input_path, cfg.max_voxels, cfg.refuse_voxels)
    scan = inputs.describe(img, scan, modality=cfg.modality, warn_slice_mm=cfg.warn_slice_mm,
                           refuse_slice_mm=cfg.refuse_slice_mm, metal_hu=cfg.metal_hu, metal_min_ml=cfg.metal_min_ml)
    timings["input_s"] = round(time.time() - t, 2)
    if is_device:
        if scan.modality != "CT":
            raise inputs.InputRefused("the device build accepts CT only (intended use excludes MR)")
        if "implausible_scale" in scan.flags or "possibly_unrescaled_ct" in scan.flags:
            raise inputs.InputRefused("the device build refuses scans whose scale or HU calibration looks wrong: "
                                      + "; ".join(scan.warnings))
    say(f"input: {scan.modality}, {' x '.join(map(str, scan.size))} voxels, {scan.slice_mm:.2f} mm coarsest spacing")

    work = Path(tempfile.mkdtemp(prefix=".ctseg_work_", dir=out))
    try:
        if scan.resampled or dicom:
            model_input = out / "image.nii.gz" if scan.resampled else work / "image.nii.gz"
            from emgmirror.ct.dicom import write_nifti
            write_nifti(img, model_input)
            image_ref = "image.nii.gz" if scan.resampled else "input"
        else:
            model_input, image_ref = input_path, "input"

        disable_totalseg_usage_stats(cfg.totalseg_home)
        runner = runner or DefaultRunner(cfg.fast, cfg.device, cfg.totalseg_home, cfg.weights_dir, work)
        is_mr = scan.modality == "MR"
        base = "totalsegmentator:total_mr" if is_mr else "totalsegmentator:total"
        body = "totalsegmentator:body_mr" if is_mr else "totalsegmentator:body"
        allowlist.check_models([base] if is_device else [base, body], cfg.build)

        # model registry: verify pinned weights before running (device), fingerprint them (both builds)
        wdir = device.weights_dir_default(cfg.weights_dir, cfg.totalseg_home)
        ts_version = runner.versions().get("TotalSegmentator")
        ds_ids = device.total_dataset_ids(cfg.fast) if not is_mr else []
        if is_device:
            allowlist.check_weights_dir(wdir, "device")
            model_fp = device.verify(wdir, ds_ids, ts_version, device.load_manifest(cfg.weights_manifest))
            model_fp["verified_against_manifest"] = True
        else:
            model_fp = {"verified_against_manifest": False, "note": "research build: weights not pinned"}

        results: list[ModelResult] = []
        runs: list[dict] = []

        def run(source: str, required: bool) -> ModelResult | None:
            a = allowlist.allowed(source, cfg.build)
            say(f"running {source}")
            t0 = time.time()
            entry = {"source": source, "licence": a.licence, "licence_url": a.licence_url,
                     "weight_ids": list(a.weight_ids), "note": a.note}
            try:
                r = runner.run(source, Path(model_input), img, work)
            except Exception as e:  # noqa: BLE001 - a failed optional model must not sink the job
                entry.update(status="failed", error=f"{type(e).__name__}: {str(e)[:300]}",
                             seconds=round(time.time() - t0, 1))
                runs.append(entry)
                if required:
                    raise
                say(f"{source} failed: {entry['error']}")
                return None
            entry.update(status="ok", seconds=round(r.seconds or (time.time() - t0), 1), meta=r.meta)
            runs.append(entry)
            results.append(r)
            return r

        # 2. base model + body, then region detection
        t = time.time()
        r_base = run(base, required=True)
        r_body = None if is_device else run(body, required=False)
        voxel_ml = float(np.prod(img.GetSpacing())) / 1000.0
        vols = regions.volumes_ml(sitk.GetArrayViewFromImage(r_base.labels), r_base.classes, voxel_ml)
        if r_body is not None:
            vols.update(regions.volumes_ml(sitk.GetArrayViewFromImage(r_body.labels), r_body.classes, voxel_ml))
        plan = regions.make_plan(base, vols, cfg, scan.modality)
        if is_device:
            plan.subtasks, plan.moose = [], []
            plan.reasons = ["device build: TotalSegmentator total only"]
        allowlist.check_models(plan.sources, cfg.build)  # region-selected models are checked too
        say(f"regions: {', '.join(plan.regions) or 'none'}; extra models: {', '.join(plan.sources) or 'none'}")

        # 3. subtasks and MOOSE
        for s in plan.sources:
            run(s, required=False)
        timings["models_s"] = round(time.time() - t, 2)

        # 4. fusion + QC
        t = time.time()
        fused = fuse(results)
        removed: list[str] = []
        if is_device:  # only intended-use structures leave a device build
            keep = np.zeros(65536, bool)
            keep[[ontology.label(n).id for n in device.device_structures()]] = True
            arr = sitk.GetArrayFromImage(fused.labels)
            present = set(np.unique(arr).tolist()) - {0}
            removed = sorted(ontology.by_id()[i].name for i in present if not keep[i])
            arr[~keep[arr]] = 0
            lab_img = sitk.GetImageFromArray(arr)
            lab_img.CopyInformation(fused.labels)
            fused.labels = lab_img
        if not is_mr:
            try:
                model_fp.update(device.fingerprint(wdir, ds_ids, ts_version))
            except OSError:
                pass
        timings["fusion_s"] = round(time.time() - t, 2)
        t = time.time()
        summary: dict = {}
        sq = structure_qc(fused.labels, img, scan, label_sources=fused.label_sources, part_of=fused.part_of,
                          warn_slice_mm=cfg.warn_slice_mm, metal_hu=cfg.metal_hu, summary=summary)
        timings["qc_s"] = round(time.time() - t, 2)

        # 5. outputs
        labels_path = out / "labels.nii.gz"
        _write_label_nifti(fused.labels, labels_path)
        entries = []
        for cid in sorted(sq):
            q = sq[cid]
            lab = ontology.by_id()[cid]
            srcs = q["stats"]["sources"]
            entries.append({"id": cid, "name": lab.name, "display": lab.display, "rgb": list(lab.rgb),
                            "snomed": lab.snomed, "source_model": max(srcs, key=srcs.get) if srcs else None,
                            "flags": q["flags"]})
        failed = [r["source"] for r in runs if r["status"] != "ok"]
        warnings = list(scan.warnings) + [f"model {s} failed; its structures are missing" for s in failed]
        grid = {"size": list(fused.labels.GetSize()), "spacing": list(fused.labels.GetSpacing()),
                "origin": list(fused.labels.GetOrigin()), "direction": list(fused.labels.GetDirection()),
                "frame": "LPS mm (SimpleITK); NIfTI header stores RAS"}
        labels_json = {
            "version": CONTRACT_VERSION,
            "labels": entries,
            "qc": {
                "disclaimer": DISCLAIMER,
                "image": image_ref,
                "grid": grid,
                "scan": {k: v for k, v in scan.to_dict().items() if k not in ("warnings",)},
                "regions": {k: round(v, 1) for k, v in plan.regions.items()},
                "warnings": warnings,
                "failed_models": failed,
                "structures": {sq[c]["name"]: sq[c]["stats"] for c in sorted(sq)},
                "fusion": {"discarded_voxels": fused.discarded,
                           "rule": "rank-based, see emgmirror/ctseg/fusion.py and docs/ct_platform/S1_segmentation.md"},
                "build": cfg.build,
                "laterality": summary.get("laterality"),
            },
        }
        if scan.series:
            labels_json["qc"]["series"] = scan.series
        if is_device:
            names = {e["name"]: e for e in entries}
            labels_json["qc"]["device"] = {
                "intended_use": device.INTENDED_USE_TEXT,
                "structures": {g: [n for n in ns if n in names] for g, ns in device.INTENDED_USE.items()},
                "cut_by_scan_edge": sorted(n for n, e in names.items() if "cut_by_scan_edge" in e["flags"]),
                "laterality_failed": summary.get("laterality", {}).get("failed", []),
                "removed_non_intended_use": removed,
                "approval": {"status": "not_approved", "note": device.APPROVAL_NOTE},
            }
            if labels_json["qc"]["device"]["laterality_failed"]:
                warnings.append("LATERALITY CHECK FAILED for " + ", ".join(labels_json["qc"]["device"]["laterality_failed"])
                                + ": check for a mirrored image or a left/right header error before any use")
        (out / "labels.json").write_text(json.dumps(labels_json, indent=1))

        prov = {
            "disclaimer": DISCLAIMER,
            "engine": {"name": "emgmirror.ctseg", "version": ENGINE_VERSION, "contract_version": CONTRACT_VERSION},
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # file name deliberately omitted: names of exported scans often contain patient identifiers
            "input": {"kind": "dicom_folder" if dicom else "nifti",
                      "sha256": None if dicom else _sha256(input_path),
                      "size": list(scan.original_size or scan.size),
                      "spacing": list(scan.original_spacing or scan.spacing)},
            "settings": cfg.to_dict(),
            "plan": plan.to_dict(),
            "models": runs,
            "build": cfg.build,
            "model_fingerprint": model_fp,
            "software": {"python": platform.python_version(), "emgmirror": _v("emgmirror"), "numpy": _v("numpy"),
                         "SimpleITK": _v("SimpleITK"), **runner.versions()},
            "privacy": {"totalsegmentator_send_usage_stats": False if usage_stats_disabled(cfg.totalseg_home)
                        else "unknown"},
            "timings": {**timings, "total_s": round(time.time() - t_start, 2)},
            "outputs": {"labels.nii.gz": _sha256(labels_path), "labels.json": _sha256(out / "labels.json")},
        }
        if scan.resampled:
            prov["outputs"]["image.nii.gz"] = _sha256(out / "image.nii.gz")
        for rep in work.glob("totalsegmentator_*.json"):
            try:
                prov.setdefault("totalsegmentator_reports", {})[rep.stem] = _scrub(json.loads(rep.read_text()))
            except (OSError, json.JSONDecodeError):
                pass
        (out / "provenance.json").write_text(json.dumps(prov, indent=1, default=str))
        say(f"done: {len(entries)} structures in {prov['timings']['total_s']:.0f} s")
        return SegResult(out, labels_path, labels_json, prov)
    finally:
        if cfg.keep_intermediate:
            say(f"intermediate files kept in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

