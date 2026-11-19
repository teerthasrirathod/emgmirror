"""DICOM folder in, results folder out. Research use, not for diagnosis or surgical planning.

    python -m emgmirror.ct.pipeline --input 01_clinical_CT --out results/ct

Steps: read the series, pick the primary one, segment it, mesh every structure,
validate (coarse reconstructions, threshold sweep, HU and bone sanity, mesh vs
label map), write the viewer, the plain-language summary and the report.
Everything runs in this process on this machine; nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

import numpy as np
import SimpleITK as sitk

from . import DISCLAIMER, dicom, explain, mesh as M, report, validate as V, viewer
from . import labels as L
from .segment import SegParams, bone_mask, segment

# (key, what the user sees, share of the total run time used for the progress bar)
STEPS = [("scan", "Reading the scan files", 0.04), ("load", "Loading the pictures", 0.06),
         ("segment", "Finding bone, muscle, fat and metal", 0.40), ("mesh", "Building the 3D surfaces", 0.15),
         ("validate", "Checking the result", 0.25), ("viewer", "Preparing the viewer", 0.05),
         ("report", "Writing the report", 0.05)]
MIN_COARSE_SLICES = 8  # a coarse stack thinner than this cannot be segmented in 3D
SOFT_MESH_MM = 1.0  # soft-tissue surfaces are meshed on a 1 mm grid: they carry no sub-millimetre detail worth
#                     millions of triangles, and full-resolution marching cubes on them does not fit in 7.6 GB RAM
SWEEP_HU = (-150, -100, -50, 0, 50, 100, 150)
VIEWER_VOXELS = 6_000_000
MESH_SOFT = dict(max_faces=250_000, min_component_voxels=100)  # on the SOFT_MESH_MM grid; skin islands < 2 cm3 dropped
MESH_HARD = dict(max_faces=400_000, min_component_voxels=30)  # bone and metal at the scan's own resolution
VIEWER_FACES = {"bone": 120_000, "medullary": 50_000, "metal": 80_000, "skin": 80_000, "muscle": 100_000, "fat": 100_000}


class Progress:
    """Prints steps, records timings and keeps progress.json current for the local web page."""

    def __init__(self, status_file: Path | None):
        self.file, self.t0, self.timings, self.key, self.t_step = status_file, time.time(), {}, None, time.time()
        self.start = {k: sum(w for _, _, w in STEPS[:i]) for i, (k, _, _) in enumerate(STEPS)}
        self.weight = {k: w for k, _, w in STEPS}
        self.text = {k: t for k, t, _ in STEPS}

    def step(self, key: str) -> None:
        self._close()
        self.key, self.t_step = key, time.time()
        print(f"[{time.time() - self.t0:7.0f} s] {self.text[key]}", flush=True)
        self.sub(0.0)

    def sub(self, fraction: float, detail: str = "") -> None:
        if self.file is None or self.key is None:
            return
        data = {"step": self.text[self.key] + (f": {detail}" if detail else ""),
                "progress": round(self.start[self.key] + self.weight[self.key] * min(max(fraction, 0.0), 1.0), 3)}
        _write_json(self.file, data)

    def fail(self, message: str) -> None:
        if self.file is not None:
            _write_json(self.file, {"step": "Stopped", "progress": 0.0, "error": message})

    def _close(self) -> None:
        if self.key is not None:
            self.timings[self.key] = round(self.timings.get(self.key, 0.0) + time.time() - self.t_step, 1)

    def done(self) -> dict:
        self._close()
        self.key = None
        self.timings["total"] = round(time.time() - self.t0, 1)
        return self.timings


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, default=_jsonable), encoding="utf-8")
    for attempt in range(40):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # the web server is reading it right now (Windows)
            if attempt == 39:
                return
            time.sleep(0.02)


def _jsonable(o):
    if isinstance(o, (np.floating, np.integer, np.bool_)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    return str(o)


def _clean(o):
    """NaN/inf -> None so the JSON files are valid everywhere."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, (np.integer, np.bool_)):
        return o.item()
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    return o


def _mask_image(mask: np.ndarray, ref: sitk.Image, margin: int = 2) -> tuple[sitk.Image, np.ndarray] | None:
    """Binary image of `mask` cropped to its bounding box, with the right origin. Returns (image, crop)."""
    if not mask.any():
        return None
    sl = []
    for ax in range(3):
        hit = np.flatnonzero(mask.any(axis=tuple(a for a in range(3) if a != ax)))
        sl.append(slice(max(int(hit[0]) - margin, 0), min(int(hit[-1]) + 1 + margin, mask.shape[ax])))
    crop = np.ascontiguousarray(mask[tuple(sl)])
    img = sitk.GetImageFromArray(crop.astype(np.uint8))
    img.SetSpacing(ref.GetSpacing())
    img.SetDirection(ref.GetDirection())
    img.SetOrigin(ref.TransformIndexToPhysicalPoint((sl[2].start, sl[1].start, sl[0].start)))
    return img, crop


def _grid(ref: sitk.Image, spacing_mm: float, lo_idx=None, hi_idx=None) -> sitk.Image:
    """Empty image covering `ref` (or an index box of it) with isotropic spacing, same orientation."""
    size, sp = np.array(ref.GetSize()), np.array(ref.GetSpacing())
    lo = np.zeros(3, int) if lo_idx is None else np.asarray(lo_idx)
    hi = size if hi_idx is None else np.asarray(hi_idx)
    n = np.maximum(np.ceil((hi - lo) * sp / spacing_mm).astype(int), 1)
    g = sitk.Image([int(v) for v in n], sitk.sitkUInt8)
    g.SetSpacing((spacing_mm,) * 3)
    g.SetDirection(ref.GetDirection())
    # first voxel centre sits half a new voxel inside the box corner
    corner = (lo - 0.5) + 0.5 * spacing_mm / sp
    g.SetOrigin(ref.TransformContinuousIndexToPhysicalPoint([float(v) for v in corner]))
    return g


def _resample(img: sitk.Image, grid: sitk.Image, interp, default) -> sitk.Image:
    return sitk.Resample(img, grid, sitk.Transform(), interp, default, img.GetPixelID())


def _structures(lab: np.ndarray, names: dict[int, str]) -> list[dict]:
    """What gets meshed: each bone (outer surface and inner cavity), metal, and the soft-tissue envelopes."""
    present = set(np.flatnonzero(np.bincount(lab.ravel(), minlength=256)).tolist())
    out, k = [], 0
    while L.cortical(k) in present or L.medullary(k) in present:
        base = names.get(L.cortical(k), names.get(L.medullary(k), f"bone_{k + 1}"))
        base = base.replace("_cortical", "").replace("_medullary", "")
        generic = base.startswith("bone_")
        out.append({"name": base, "kind": "bone", "ids": [L.cortical(k), L.medullary(k)], "group": "Bones",
                    "label": base.replace("_", " ").capitalize(), "bone": k,
                    "named_by": "generic" if generic else "anatomy", "color": L.COLORS["cortical"], "res": "full"})
        if L.medullary(k) in present:
            out.append({"name": f"{base}_medullary", "kind": "bone", "part": "medullary", "ids": [L.medullary(k)],
                        "group": "Bones", "label": f"{base.replace('_', ' ').capitalize()}, inner part", "bone": k,
                        "named_by": "generic" if generic else "anatomy", "color": L.COLORS["medullary"],
                        "res": "full", "visible": False})
        k += 1
    if L.METAL in present:
        out.append({"name": "metal", "kind": "metal", "ids": [L.METAL], "group": "Hardware", "label": "Metal hardware",
                    "color": L.COLORS["metal"], "res": "full"})
    soft = [("skin", "skin", sorted(present - {0}), "Skin surface", 0.18, True),
            ("muscle", "muscle", [L.MUSCLE], "Muscle and lean tissue", 0.45, False),
            ("fat", "fat", [L.FAT], "Fat", 0.35, False)]
    for name, kind, ids, label, opacity, visible in soft:
        if ids and (set(ids) & present):
            out.append({"name": name, "kind": kind, "ids": ids, "group": "Soft tissue", "label": label,
                        "color": L.COLORS[name], "res": "soft", "opacity": opacity, "visible": visible})
    return out


def _mesh_all(structs: list[dict], labels: sitk.Image, lab: np.ndarray, out: Path, prog: Progress) -> tuple[dict, dict]:
    """Mesh every structure, check it, export it. Returns (meshes, per-structure numbers)."""
    vox = float(np.prod(labels.GetSpacing()))
    counts = np.bincount(lab.ravel(), minlength=256)
    meshes, stats, mvm = {}, {}, {}
    soft_grid = None
    for i, s in enumerate(structs):
        prog.sub(i / len(structs), s["label"])
        t0 = time.time()
        mask = np.isin(lab, s["ids"])
        if s["kind"] == "skin":
            mask = lab > 0
        item = _mask_image(mask, labels)
        del mask
        if item is None:
            continue
        img, crop = item
        if s["res"] == "soft" and min(labels.GetSpacing()) < SOFT_MESH_MM:
            grid = _grid(img, SOFT_MESH_MM)
            frac = V.volume_fraction_resample(img, grid)
            crop = sitk.GetArrayFromImage(frac) >= 0.5
            img = sitk.GetImageFromArray(crop.astype(np.uint8))
            img.CopyInformation(grid)
            soft_grid = SOFT_MESH_MM
            params = M.MeshParams(**{**MESH_SOFT, **({"min_component_voxels": 2000} if s["kind"] == "skin" else {})})
        else:
            params = M.MeshParams(**MESH_HARD)
        m, info = M.mask_to_mesh(crop, img, params)
        if len(m.faces) == 0:
            continue
        qc = M.mesh_qc(m, info["voxel_volume_mm3"])
        vm = V.mesh_vs_mask(np.asarray(m.vertices), np.asarray(m.faces), img)
        meshes[s["name"]] = m
        mvm[s["name"]] = vm
        stats[s["name"]] = {**{k: s.get(k) for k in ("kind", "label", "group", "named_by", "part", "res")},
                            "volume_cm3": float(sum(counts[j] for j in s["ids"]) * vox / 1000.0),
                            "mesh_grid_mm": list(img.GetSpacing()), "mesh": info, "qc": qc,
                            "seconds": round(time.time() - t0, 1)}
        del img, crop
        gc.collect()
    if not meshes:
        raise ValueError("No body was found in this scan: nothing denser than air could be turned into a surface.")
    exported = M.export_meshes(meshes, out / "meshes", {s["name"]: s["color"] for s in structs})
    written = [Path(p) for v in exported.values() for p in (v.values() if isinstance(v, dict) else [v])]
    files = sorted(str(f.relative_to(out)).replace("\\", "/") for f in [*written, out / "meshes" / "README.txt"])
    del exported
    return meshes, {"structures": stats, "mesh_vs_mask": mvm, "soft_mesh_mm": soft_grid, "files": files}


def _match_bones(labels: sitk.Image, coarse_labels: sitk.Image, lc: np.ndarray, coarse_names: dict) -> dict[int, list]:
    """Primary cortical label id -> coarse cortical label ids of the bone pieces that mostly overlap it.

    Names are not used: a coarse run can split one bone into two pieces (a gap that the finer grid closes) or name
    a piece generically, and comparing by name would then measure part of a bone against the whole. Each coarse
    bone piece goes to the primary bone holding most of its voxels, if that is at least half of them."""
    on_c = sitk.GetArrayFromImage(sitk.Resample(labels, coarse_labels, sitk.Transform(),  # a copy: a view of the
                                                sitk.sitkNearestNeighbor, 0, labels.GetPixelID()))  # temporary dies
    out: dict[int, list] = {}
    for j in sorted(int(i) for i in coarse_names if L.is_bone(int(i)) and (int(i) - L.BONE_BASE) % 2 == 0):
        hit = on_c[(lc == j) | (lc == j + 1)].astype(np.int64)
        if not hit.size:
            continue
        hit = hit[(hit >= L.BONE_BASE)]
        hit = hit - (hit - L.BONE_BASE) % 2  # medullary -> its bone's cortical id
        if not hit.size:
            continue
        ids, cnt = np.unique(hit, return_counts=True)
        k = int(np.argmax(cnt))
        if cnt[k] >= 0.5 * np.count_nonzero((lc == j) | (lc == j + 1)):
            out.setdefault(int(ids[k]), []).append(j)
    return out


def _coarse_checks(coarse: list, labels: sitk.Image, lab: np.ndarray, names: dict, params: SegParams, out: Path,
                   prog: Progress, load=dicom.load_series) -> tuple[dict, list[dict]]:
    """Segment every usable coarse stack on its own and compare with the primary result."""
    overlap, runs = {}, []
    tilted = lambda s, k: k in getattr(s, "tilted_stacks", [])  # noqa: E731
    stacks = [(s, k) for s in coarse for k, idx in enumerate(s.stacks)
              if len(idx) >= MIN_COARSE_SLICES and not tilted(s, k)]
    runs += [{"series": s.number, "stack": k, "orientation": s.orientation, "slices": len(idx), "skipped":
              f"only {len(idx)} slices; at least {MIN_COARSE_SLICES} are needed to segment a stack in 3D"}
             for s in coarse for k, idx in enumerate(s.stacks) if len(idx) < MIN_COARSE_SLICES]
    runs += [{"series": s.number, "stack": k, "orientation": s.orientation, "slices": len(idx), "skipped":
              "gantry tilt (sheared slices); tilted stacks are not resampled"}
             for s in coarse for k, idx in enumerate(s.stacks) if len(idx) >= MIN_COARSE_SLICES and tilted(s, k)]
    by_name = {v: k for k, v in names.items()}
    for n, (s, k) in enumerate(stacks):
        tag = f"series {s.number} ({s.orientation}, {s.slice_spacing:.1f} mm) stack {k}"
        prog.sub(0.35 + 0.6 * n / max(len(stacks), 1), f"coarse {s.orientation} reconstruction")
        t0 = time.time()
        try:
            img = load(s, k)
            res = segment(img, None, params)
            dicom.write_nifti(res.labels, out / "validation" / f"labels_coarse_s{s.number}_k{k}.nii.gz")
            lc = sitk.GetArrayViewFromImage(res.labels)
            names_c = {v: i for i, v in res.names.items()}
            pairs = [("all bone", lab >= L.BONE_BASE, lc >= L.BONE_BASE), ("metal", lab == L.METAL, lc == L.METAL),
                     ("body", lab > 0, lc > 0)]
            notes: dict[str, str] = {}
            match = _match_bones(labels, res.labels, lc, res.names)
            for nm, i_ref in by_name.items():  # each named primary bone against the coarse pieces that overlap it
                if nm.endswith("_cortical") and not nm.startswith("bone_") and match.get(i_ref):
                    base = nm[:-9]
                    ids = match[i_ref]
                    pairs.append((base, (lab == i_ref) | (lab == i_ref + 1), np.isin(lc, ids + [i + 1 for i in ids])))
                    got = [res.names.get(i, str(i)).removesuffix("_cortical") for i in ids]
                    if got != [base]:
                        notes[base] = (f"the coarse run splits or names this bone differently ({' + '.join(got)}); "
                                       "its pieces are matched by overlap")
            for what, a, b in pairs:
                if not a.any() and not b.any():
                    continue
                ra = sitk.GetImageFromArray(a.astype(np.uint8))
                ra.CopyInformation(labels)
                rb = sitk.GetImageFromArray(b.astype(np.uint8))
                rb.CopyInformation(res.labels)
                om = V.overlap_metrics(ra, rb)
                if what in notes:
                    om["note"] = "; ".join(x for x in (om.get("note"), notes[what]) if x)
                overlap[f"{what}: {tag}"] = om
                del ra, rb, a, b
                gc.collect()
            runs.append({"series": s.number, "stack": k, "orientation": s.orientation, "slices": len(s.stacks[k]),
                         "spacing_mm": list(img.GetSpacing()), "names": res.names, "seconds": round(time.time() - t0, 1),
                         "flags": res.info.get("flags", [])})
            del img, res, lc
        except Exception as exc:  # a failed check is reported, it does not stop the build
            runs.append({"series": s.number, "stack": k, "orientation": s.orientation, "slices": len(s.stacks[k]),
                         "seconds": round(time.time() - t0, 1), "error": f"{type(exc).__name__}: {exc}"})
        gc.collect()
    return overlap, runs


def _sweep(image: sitk.Image, lab: np.ndarray, params: SegParams, t_ref: float, area_mm2: float) -> dict:
    """Bone volume against the bone threshold, with everything else held fixed."""
    body = lab > 0
    vox = float(np.prod(image.GetSpacing()))

    def volumes(t: float) -> dict[str, float]:
        m = bone_mask(image, body, params, threshold_hu=float(t))
        v = float(np.count_nonzero(m)) * vox
        del m
        gc.collect()
        return {"bone above threshold": v}
    return V.threshold_sweep(volumes, [t_ref + d for d in SWEEP_HU], t_ref,
                             areas_mm2={"bone above threshold": area_mm2} if area_mm2 else None)


def _edge_sensitivity(seg_info: dict, area_mm2: float) -> dict | None:
    """Shift of the delivered outer bone surface when its edge level moves from the local half-maximum (0.5) to
    another fraction of the way from lean tissue to the local maximum. Volume change over the outer bone area."""
    ch = ((seg_info.get("surface") or {}).get("edge_fraction_volume_change_cm3")) or {}
    if not ch or not area_mm2:
        return None
    rows = {fr: {"volume_change_cm3": dv, "shift_mm": dv * 1000.0 / area_mm2} for fr, dv in ch.items()}
    shifts = [abs(r["shift_mm"]) for r in rows.values()]
    return {"reference_fraction": 0.5, "fractions": rows, "outer_area_mm2": area_mm2,
            "max_abs_shift_mm": max(shifts),
            "note": "delivered bone labels: the outer surface is set by the local half-maximum, so this is the edge "
                    "decision that moves it. Volume change over the summed outer area of the bone meshes, so it is a "
                    "mean shift; local shifts can be larger."}


def _ref_threshold(info: dict) -> float | None:
    bt = info.get("bone_thresholds") or {}
    for k in ("low_hu", "t_low", "low", "bone_low_hu", "threshold_hu"):
        if isinstance(bt.get(k), (int, float)):
            return float(bt[k])
    return None


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("emgmirror")
    except Exception:
        return "unknown"


def _viewer_volume(image: sitk.Image, labels: sitk.Image, lab: np.ndarray) -> tuple[sitk.Image, sitk.Image]:
    """Volume and labels cropped to the body and resampled to at most VIEWER_VOXELS voxels."""
    body = lab > 0
    if body.any():
        lo, hi = [], []
        for ax in (2, 1, 0):  # x, y, z index order
            hit = np.flatnonzero(body.any(axis=tuple(a for a in range(3) if a != ax)))
            lo.append(max(int(hit[0]) - 8, 0))
            hi.append(min(int(hit[-1]) + 9, body.shape[ax]))
    else:
        lo, hi = [0, 0, 0], list(image.GetSize())
    extent = (np.array(hi) - np.array(lo)) * np.array(image.GetSpacing())
    s = max(float(np.cbrt(np.prod(extent) / VIEWER_VOXELS)), float(min(image.GetSpacing())))
    grid = _grid(image, s, lo, hi)
    vol = _resample(sitk.Clamp(image, sitk.sitkInt16, -1024, 32767), grid, sitk.sitkLinear, -1024)
    return vol, _resample(labels, grid, sitk.sitkNearestNeighbor, 0)


def _viewer_meshes(structs: list[dict], meshes: dict, stats: dict, sentences: dict, bones: dict) -> dict:
    out = {}
    for s in structs:
        m = meshes.get(s["name"])
        if m is None:
            continue
        cap = VIEWER_FACES["medullary" if s.get("part") else s["kind"]]
        if len(m.faces) > cap:
            try:
                m = M._decimate(m, cap)
            except Exception:
                pass
        st = stats[s["name"]]
        info = {"Volume": explain.format_cm3(st["volume_cm3"])}
        b = bones.get(s["name"])
        if b and not s.get("part"):
            if b.get("length_mm"):
                info["Length"] = explain.format_mm(b["length_mm"]) + (" or more (cut off by the scan edge)"
                                                                        if b.get("touches_border") else "")
            if b.get("cortical_fraction") is not None:
                info["Dense outer shell"] = f"{100 * b['cortical_fraction']:.0f} % of the bone"
        out[s["name"]] = {"vertices": np.asarray(m.vertices, np.float32), "faces": np.asarray(m.faces, np.int32),
                          "color": s["color"], "label": s["label"], "group": s["group"],
                          "opacity": s.get("opacity", 1.0), "visible": s.get("visible", True), "info": info,
                          "explain": sentences.get(s["name"], "")}
    return out


def _measurements(primary, n_series: int, reason: str, image: sitk.Image, lab: np.ndarray, seg_info: dict,
                  stats: dict, bones: dict, val: dict) -> dict:
    """The numbers the plain-language summary is written from."""
    sp, size = np.array(image.GetSpacing()), np.array(image.GetSize())
    vox = float(np.prod(sp)) / 1000.0
    counts = np.bincount(lab.ravel(), minlength=256)
    fat, mus, metal = counts[L.FAT] * vox, counts[L.MUSCLE] * vox, counts[L.METAL] * vox
    bone = float(counts[L.BONE_BASE:].sum()) * vox
    total = fat + mus + bone + metal
    structures = []
    for name, st in stats.items():
        b = bones.get(name, {}) if not st.get("part") else {}
        structures.append({"name": name, "kind": st["kind"], "label": st["label"], "volume_cm3": st["volume_cm3"],
                           "length_mm": b.get("length_mm"), "named_by": st.get("named_by"),
                           "touches_edge": bool(b.get("touches_border")) if st["kind"] == "bone" else False,
                           "cortical_fraction": b.get("cortical_fraction"), "n_pieces": b.get("n_components")})
    naming = seg_info.get("naming") or {}
    named = any(s.get("named_by") == "anatomy" for s in structures)
    guess = "forearm" if named and any(s["name"] in ("radius", "ulna") for s in structures) else "unknown"
    metal_info = seg_info.get("metal") or {}
    hu = val.get("hu_sanity") or {}
    ov = [r for k, r in (val.get("overlap") or {}).items() if k.startswith("all bone") and r.get("msd_mm") is not None
          and np.isfinite(r.get("msd_mm", np.nan))]
    sw = (val.get("threshold_sweep") or {}).get("structures", {}).get("bone above threshold", {})
    per_bone = [(k.split(":")[0], r["dice"]) for k, r in (val.get("overlap") or {}).items()
                if k.split(":")[0] in bones and isinstance(r, dict) and r.get("dice") is not None
                and np.isfinite(r["dice"])]  # whole bones only: not 'all bone', 'metal' or 'body'
    weakest = min(per_bone, key=lambda t: t[1]) if per_bone else None
    edge = val.get("edge_sensitivity") or {}
    bone_mesh = [st["mesh"].get("volume_change_pct") for n, st in stats.items() if st["kind"] == "bone"
                 and not st.get("part")]
    floor = val.get("accuracy_floor") or {}
    return {
        "scan": {"description": primary.description, "n_slices": int(size[2]), "voxel_mm": sp.tolist(),
                 "extent_mm": (sp * size).tolist(), "kernel": primary.kernel, "body_part_guess": guess,
                 "n_series": n_series, "series_reason": reason},
        "structures": structures,
        "tissue": {"fat_cm3": float(fat), "muscle_cm3": float(mus), "bone_cm3": float(bone), "metal_cm3": float(metal),
                   "fat_pct": float(100 * fat / total) if total else None,
                   "muscle_pct": float(100 * mus / total) if total else None},
        "hardware": {"present": bool(metal > 0), "volume_cm3": float(metal), "max_hu": metal_info.get("max_hu"),
                     "n_pieces": metal_info.get("n_components"), "scale_clipped": bool((hu.get("max") or {}).get("clipped"))},
        "validation": {"voxel_floor_mm": floor.get("half_diagonal_mm"),
                       "coarse_msd_mm": float(np.mean([r["msd_mm"] for r in ov])) if ov else None,
                       "coarse_hd95_mm": float(np.max([r["hd95_mm"] for r in ov])) if ov else None,
                       "coarse_dice": float(np.mean([r["dice"] for r in ov])) if ov else None,
                       "threshold_shift_mm_per_100hu": abs(sw["shift_mm_per_100hu"]) if sw.get("shift_mm_per_100hu")
                       is not None else None,
                       "edge_shift_mm": edge.get("max_abs_shift_mm"),
                       "coarse_weakest": {"name": weakest[0], "dice": float(weakest[1])} if weakest else None,
                       "coarse_volume_diff_pct": float(np.mean([r["volume_diff_pct"] for r in ov])) if ov else None,
                       "mesh_volume_change_pct": float(np.max(np.abs(bone_mesh))) if bone_mesh else None,
                       "hu_ok": all(v.get("ok", True) for k, v in hu.items() if isinstance(v, dict) and "ok" in v
                                    and k in ("air", "fat", "muscle")) if hu else None,
                       "air_hu": (hu.get("air") or {}).get("value"), "fat_hu": (hu.get("fat") or {}).get("value")},
        "flags": [str(f) for f in seg_info.get("flags", [])],
        "naming": naming,
    }


def _remove_old_outputs(out: Path) -> None:
    """Delete the meshes and coarse label maps an earlier build wrote to `out`, so they are not mistaken for
    this build's results. Only files that build listed in its results.json are removed (plus the summary files
    that point at them), so meshes a person saved there themselves are left alone."""
    old: list[str] = []
    rj = out / "results.json"
    try:
        old = [str(f) for f in json.loads(rj.read_text(encoding="utf-8")).get("files", [])]
    except (OSError, ValueError, AttributeError):
        pass
    meshes = (out / "meshes").resolve()
    for rel in old:
        f = (out / rel).resolve()
        if f.parent == meshes and f.is_file():  # never outside out/meshes, whatever results.json says
            f.unlink()
    for f in (out / "validation").glob("labels_coarse_s*_k*.nii.gz"):
        f.unlink()
    for name in ("results.json", "summary.json"):  # rewritten at the end; stale until then
        (out / name).unlink(missing_ok=True)


def build(input_dir: Path | str, out_dir: Path | str, status_file: Path | str | None = None,
          params: SegParams | None = None, coarse_checks: bool = True, sweep: bool = True,
          log: Callable[[str], None] = print) -> dict:
    """Run the whole pipeline. Returns the results dict that is also written to results.json."""
    input_dir, out = Path(input_dir), Path(out_dir)
    if input_dir.is_file() and input_dir.name.upper() == "DICOMDIR":  # the images sit beside (or below) it
        input_dir = input_dir.parent
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation").mkdir(exist_ok=True)
    params = params or SegParams()
    prog = Progress(Path(status_file) if status_file else None)

    prog.step("scan")
    series = dicom.scan_series(input_dir)
    if not series:
        raise ValueError("No DICOM image files were found in that folder.")
    primary, reason = dicom.choose_primary(series)
    soft = dicom.choose_soft(series, primary)
    coarse = dicom.choose_coarse(series, primary) if coarse_checks else []
    inventory = dicom.inventory_markdown(series)
    (out / "inventory.md").write_text(f"# Series in this study\n\n{DISCLAIMER}\n\n{inventory}", encoding="utf-8")
    log(reason)

    prog.step("load")
    image = dicom.load_series(primary)
    dicom.write_nifti(image, out / "volume.nii.gz")
    prog.sub(0.5)
    soft_img = dicom.load_series(soft) if soft is not None else None

    prog.step("segment")
    seg = segment(image, soft_img, params, progress=lambda text, f: prog.sub(f, ""))
    labels, names, seg_info = seg.labels, {int(k): v for k, v in seg.names.items()}, seg.info
    hu_source = soft_img if soft_img is not None else image
    lab = sitk.GetArrayViewFromImage(labels)
    dicom.write_nifti(labels, out / "labels.nii.gz")
    (out / "labels.json").write_text(json.dumps(_clean({"disclaimer": DISCLAIMER, "names": names, "info": seg_info}),
                                                indent=1, default=_jsonable),
                                     encoding="utf-8")

    val: dict = {"accuracy_floor": V.accuracy_floor(image.GetSpacing())}
    val["hu_sanity"] = V.hu_sanity(hu_source, labels, body_mask=lab > 0)
    hu_series = soft if soft is not None else primary
    del soft_img, hu_source
    gc.collect()

    prog.step("mesh")
    _remove_old_outputs(out)  # only now: a scan that cannot be read or segmented leaves the last build intact
    structs = _structures(lab, names)
    meshes, mesh_res = _mesh_all(structs, labels, lab, out, prog)
    stats = mesh_res["structures"]
    val["mesh_vs_mask"] = mesh_res["mesh_vs_mask"]

    prog.step("validate")
    val["bone_sanity"] = V.bone_sanity(labels, names)
    prog.sub(0.02, "tissue enclosed by bone")
    try:
        val["enclosed_tissue"] = V.enclosed_tissue(labels, names)
    except Exception as exc:  # a failed check is reported, it does not stop the build
        val["enclosed_tissue_error"] = f"{type(exc).__name__}: {exc}"
    bones = {}
    for s in structs:  # bone_sanity is keyed by bone name or index, depending on what was found
        if s["kind"] == "bone" and not s.get("part"):
            b = val["bone_sanity"].get(s["name"])
            if isinstance(b, dict):
                bones[s["name"]] = b
    t_ref = _ref_threshold(seg_info)
    notes: dict = {}
    if not sweep:
        notes["threshold_sweep"] = "skipped on request"
    elif t_ref is None:
        notes["threshold_sweep"] = "skipped: the segmentation reported no bone threshold"
    elif not bones:
        notes["threshold_sweep"] = "skipped: no bone was found"
    if sweep and t_ref is not None and bones:
        prog.sub(0.05, "bone threshold sweep")
        area = float(sum(stats[n]["qc"]["area_mm2"] for n in bones if n in stats))
        notes["threshold_sweep"] = (
            "The sweep measures the thresholded bone mask, an intermediate step, not the delivered bone labels or "
            "meshes: those are closed, filled and then cut at the local half-maximum (see edge_sensitivity). Volume is "
            "all voxels above the threshold inside the body; the area used to turn a volume change into a surface "
            "shift is the summed outer surface of the bone meshes. Inner (endosteal) surfaces also move with the "
            "threshold and are not in that area, so the shift in mm is an upper bound for that mask's outer surface.")
        try:
            val["threshold_sweep"] = _sweep(image, lab, params, t_ref, area)
        except Exception as exc:
            val["threshold_sweep_error"] = f"{type(exc).__name__}: {exc}"
    if bones:  # needs only the segmentation's own record and the bone mesh area, so it runs without the sweep too
        val["edge_sensitivity"] = _edge_sensitivity(
            seg_info, float(sum(stats[n]["qc"]["area_mm2"] for n in bones if n in stats)))
    coarse_runs: list[dict] = []
    if not coarse_checks:
        notes["coarse"] = "skipped on request"
    elif not coarse:
        notes["coarse"] = ("no independent coarse reconstruction exists in this study (a series with slice spacing at "
                           "least 3 times the primary's), so agreement between reconstructions could not be measured")
    if coarse:
        val["overlap"], coarse_runs = _coarse_checks(coarse, labels, lab, names, params, out, prog)

    prog.step("viewer")
    m = _measurements(primary, len(series), reason, image, lab, seg_info, stats, bones, val)
    summary = explain.build_summary(m)
    sentences = explain.structure_explanations(m)
    vol_small, lab_small = _viewer_volume(image, labels, lab)
    meta = {"title": summary.get("title", "Your scan"), "subtitle": DISCLAIMER,
            "summary": [summary.get("headline", "")] + [w for w in summary.get("warnings", [])],
            "voxel_mm": list(image.GetSpacing()),
            "label_names": {i: n.replace("_", " ") for i, n in names.items()},
            "notes": ["The slices shown here are a reduced copy of the scan "
                      f"({vol_small.GetSpacing()[0]:.2f} mm voxels). Measurements in the report use the full scan."]}
    viewer.write_viewer(out / "viewer.html", _viewer_meshes(structs, meshes, stats, sentences, bones),
                        vol_small, lab_small, meta)
    del vol_small, lab_small, meshes
    gc.collect()

    prog.step("report")
    timings = prog.done()
    results = _clean({
        "disclaimer": DISCLAIMER, "input": input_dir.name, "primary": {"number": primary.number,
        "description": primary.description, "kernel": primary.kernel, "reason": reason, "orientation": primary.orientation,
        "slice_thickness_tag_mm": primary.slice_thickness_tag},
        "soft": {"number": soft.number, "description": soft.description, "kernel": soft.kernel} if soft else None,
        "created": time.strftime("%Y-%m-%d %H:%M"), "version": _version(), "notes": notes, "files": mesh_res["files"],
        "hu_series": {"number": hu_series.number, "description": hu_series.description, "kernel": hu_series.kernel},
        "coarse": coarse_runs, "geometry": {"size": list(image.GetSize()), "spacing_mm": list(image.GetSpacing()),
                                            "origin_mm": list(image.GetOrigin()), "direction": list(image.GetDirection())},
        "parameters": dataclasses.asdict(params), "pipeline": {"soft_mesh_mm": mesh_res["soft_mesh_mm"],
        "sweep_offsets_hu": list(SWEEP_HU), "viewer_voxels": VIEWER_VOXELS, "min_coarse_slices": MIN_COARSE_SLICES,
        "mesh_bone_metal": dataclasses.asdict(M.MeshParams(**MESH_HARD)),
        "mesh_soft_tissue": dataclasses.asdict(M.MeshParams(**MESH_SOFT))},
        "names": names, "segmentation": seg_info, "structures": stats, "validation": val, "measurements": m,
        "timings_s": timings})
    (out / "results.json").write_text(json.dumps(results, indent=1, default=_jsonable), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(_clean(summary), indent=1), encoding="utf-8")
    (out / "report.md").write_text(report.build_report(results, inventory, summary), encoding="utf-8")
    if prog.file is not None:
        _write_json(prog.file, {"step": "Finished", "progress": 1.0})
    return results


def headline(results: dict) -> str:
    """The short text printed at the end of `emgmirror ct build`."""
    return report.headline(results)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--input", required=True, help="folder with the DICOM files (or a DICOMDIR)")
    p.add_argument("--out", required=True, help="results folder")
    p.add_argument("--status", default=None, help="progress file for the local web page")
    p.add_argument("--no-coarse", action="store_true", help="skip the coarse-reconstruction comparison")
    p.add_argument("--no-sweep", action="store_true", help="skip the bone threshold sweep")
    a = p.parse_args(argv)
    try:
        res = build(a.input, a.out, a.status, coarse_checks=not a.no_coarse, sweep=not a.no_sweep)
    except Exception as exc:
        traceback.print_exc()
        msg = str(exc) if isinstance(exc, ValueError) else "The scan could not be processed (see log.txt)."
        Progress(Path(a.status) if a.status else None).fail(msg)
        return 1
    print(headline(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
