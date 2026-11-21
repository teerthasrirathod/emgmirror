"""Label map → meshes → exports → manifest.json (the S2 → S3 contract).

    python -m emgmirror.ctmesh LABELS.nii.gz OUT_DIR [--labels-json labels.json] [--ids 1 2 3]

Output folder::

    manifest.json            S3 contract (version 1)
    labels.nii.gz            copied through
    labels.json              copied through (normalised)
    segmentation.seg.dcm     DICOM-SEG (highdicom)
    combined_web.glb         web LOD, meshopt + quantised, one node per structure
    combined_print.glb       print LOD, float32
    combined_print.3mf       print LOD, one coloured object per structure (mm)
    structures/<id>_<name>.{glb,stl,3mf}   print LOD per structure
    qc.json                  full per-structure QC (every LOD, method, timings)

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from . import DISCLAIMER, __version__
from . import decimate as dec
from . import export, gltf, qc, surface
from .contract import LabelMap, load_labelmap, save_labelmap, snomed_triplet
from .geometry import AXES_NOTE, LPS_TO_GLTF


@dataclass
class PipelineParams:
    surface: surface.SurfaceParams = field(default_factory=surface.SurfaceParams)
    print_lod: dec.LodSpec = field(default_factory=lambda: dec.LodSpec(**asdict(dec.PRINT)))
    web_lod: dec.LodSpec = field(default_factory=lambda: dec.LodSpec(**asdict(dec.WEB)))
    web_total_faces: int = 1_500_000  # budget for combined_web.glb; per-structure budgets scale down to fit
    lod_retries: int = 3
    limits: qc.GateLimits = field(default_factory=qc.GateLimits)
    dicom_seg: bool = True
    per_structure_files: bool = True


def _stem(lid: int, name: str) -> str:
    return f"{lid:03d}_{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}"[:80]


def evaluate(mesh: trimesh.Trimesh, boundary, voxel_volume_mm3: float, spacing, limits: qc.GateLimits) -> dict:
    """QC numbers for one mesh against its label's voxel boundary."""
    vmm = qc.voxel_mm(spacing)
    if not len(mesh.faces):
        r = {"watertight": False, "winding_consistent": False, "self_intersections": 0, "n_faces": 0,
             "n_vertices": 0, "volume_mm3": 0.0, "area_mm2": 0.0, "volume_change_pct": None, "hd95_mm": None,
             "mean_dist_mm": None, "hausdorff_mm": None, "manifold3d_ok": False}
    else:
        d = qc.surface_distance(mesh.vertices, mesh.faces, boundary[0], boundary[1], vmm)
        vol = float(mesh.volume)
        r = {"watertight": bool(mesh.is_watertight), "winding_consistent": bool(mesh.is_winding_consistent),
             "self_intersections": int(qc.self_intersections(mesh)), "n_faces": int(len(mesh.faces)),
             "n_vertices": int(len(mesh.vertices)), "volume_mm3": vol, "area_mm2": float(mesh.area),
             "volume_change_pct": (vol - voxel_volume_mm3) / voxel_volume_mm3 * 100 if voxel_volume_mm3 else None,
             "manifold3d_ok": dec.manifold_ok(mesh), **{k: d[k] for k in ("hd95_mm", "mean_dist_mm", "hausdorff_mm")}}
    ok, why = qc.gate(r, spacing, limits)
    if r["n_faces"] and not r["manifold3d_ok"]:
        ok, why = False, why + ["manifold3d rejects the mesh"]
    r["passed"], r["issues"] = ok, why
    return r


def _choose_native(lm: LabelMap, lid: int, sn: surface.Surface | None, boundary, p: PipelineParams) -> tuple:
    """Pick the native surface: SurfaceNets unless it fails the gate or the label is thin, in which case flying
    edges is tried too and the passing (then lower-HD95) candidate wins."""
    cands = []
    if sn is not None:
        cands.append((sn, evaluate(sn.mesh, boundary, sn.voxel_volume_mm3, lm.spacing, p.limits)))
    thin = surface.label_depth_voxels(_crop(lm.array == lid)) <= p.surface.thin_depth_voxels
    if not cands or not cands[0][1]["passed"] or thin:
        fe = surface.flyingedges_one(lm, lid, p.surface)
        if fe is not None:
            if sn is not None:
                fe.cut_by_scan_edge = sn.cut_by_scan_edge
            cands.append((fe, evaluate(fe.mesh, boundary, fe.voxel_volume_mm3, lm.spacing, p.limits)))
    for s, _ in cands:
        s.thin = thin

    def rank(c):
        r = c[1]
        return (not r["passed"], len(r["issues"]), r["hd95_mm"] if r["hd95_mm"] is not None else 1e9)
    best = min(cands, key=rank)
    return best[0], best[1], [c[0].method for c in cands]


def _crop(mask: np.ndarray) -> np.ndarray:
    b = surface._bbox(mask)
    if b is None:
        return mask[:0, :0, :0]
    lo, hi = b
    return mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]


def _lod(native: trimesh.Trimesh, spec: dec.LodSpec, budget: int, boundary, vvol: float, lm: LabelMap,
         p: PipelineParams) -> tuple[trimesh.Trimesh, dict]:
    vmm = qc.voxel_mm(lm.spacing)
    err, tgt = spec.error_voxels * vmm, budget
    last = None
    for attempt in range(p.lod_retries + 1):
        m, rep = dec.repair(dec.simplify(native, tgt, err))
        r = evaluate(m, boundary, vvol, lm.spacing, p.limits)
        r.update({"target_faces": int(tgt), "error_cap_mm": float(err), "attempts": attempt + 1, "repair": rep,
                  "source": "decimated" if len(m.faces) < len(native.faces) else "native"})
        last = (m, r)
        if r["passed"] or len(m.faces) >= len(native.faces):
            return last
        err, tgt = err / 2, tgt * 2
    m = native.copy()
    r = evaluate(m, boundary, vvol, lm.spacing, p.limits)
    r.update({"target_faces": int(budget), "error_cap_mm": 0.0, "attempts": p.lod_retries + 2,
              "source": "native (decimation kept failing QC)"})
    return m, r


def _structure_extras(lab, r_print: dict, s: surface.Surface) -> dict:
    return {"id": lab.id, "name": lab.name, "display": lab.display, "rgb": list(lab.rgb),
            "snomed": snomed_triplet(lab.snomed), "source_model": lab.source_model, "flags": lab.flags,
            "volume_ml": r_print["volume_mm3"] / 1000.0, "cut_by_scan_edge": s.cut_by_scan_edge,
            "disclaimer": DISCLAIMER}


def run(labels_nii: str | Path, out_dir: str | Path, labels_json: str | Path | None = None,
        ids: list[int] | None = None, params: PipelineParams | None = None, source_images=None,
        log=print) -> dict:
    """Run the whole S2 pipeline. Returns the manifest dict (also written to ``out_dir/manifest.json``)."""
    p = params or PipelineParams()
    t0 = time.perf_counter()
    out = Path(out_dir)
    (out / "structures").mkdir(parents=True, exist_ok=True)
    lm = load_labelmap(labels_nii, labels_json)
    present = set(np.unique(lm.array).tolist()) - {0}
    wanted = [lab.id for lab in lm.labels if (ids is None or lab.id in ids)]
    todo = [i for i in wanted if i in present]
    skipped = [i for i in wanted if i not in present]
    timings = {"load": time.perf_counter() - t0}

    t = time.perf_counter()
    sn = surface.surfacenets_all(lm, todo, p.surface)
    timings["surfacenets_all"] = time.perf_counter() - t

    natives, reports = {}, {}
    t = time.perf_counter()
    for lid in todo:
        lab = lm.label(lid)
        tl = time.perf_counter()
        boundary = qc.label_boundary(lm.array, lid, lm.index_to_lps)
        s, r_nat, tried = _choose_native(lm, lid, sn.get(lid), boundary, p)
        natives[lid] = (s, boundary)
        reports[lid] = {"id": lid, "name": lab.name, "method": s.method, "methods_tried": tried, "thin": s.thin,
                        "cut_by_scan_edge": s.cut_by_scan_edge, "split_vertices": s.split_vertices,
                        "voxel_count": s.voxel_count, "voxel_volume_mm3": s.voxel_volume_mm3,
                        "native": r_nat, "timings_s": {"native": time.perf_counter() - tl}}
    timings["native_qc"] = time.perf_counter() - t

    # web budgets: per-structure cap, then scale down so the whole scene fits web_total_faces
    nf = {lid: len(natives[lid][0].mesh.faces) for lid in todo}
    want = {lid: min(p.web_lod.max_faces, nf[lid]) for lid in todo}
    tot = sum(want.values())
    scale = min(1.0, p.web_total_faces / tot) if tot else 1.0
    web_budget = {lid: max(500, int(want[lid] * scale)) for lid in todo}

    t = time.perf_counter()
    lods = {}
    for lid in todo:
        s, boundary = natives[lid]
        tl = time.perf_counter()
        mp, rp = _lod(s.mesh, p.print_lod, p.print_lod.max_faces, boundary, s.voxel_volume_mm3, lm, p)
        mw, rw = _lod(s.mesh, p.web_lod, web_budget[lid], boundary, s.voxel_volume_mm3, lm, p)
        lods[lid] = (mp, mw)
        reports[lid]["print"], reports[lid]["web"] = rp, rw
        reports[lid]["timings_s"]["lods"] = time.perf_counter() - tl
        log(f"  {lm.label(lid).name:<28} {s.method:<12} native {nf[lid]:>7} print {rp['n_faces']:>7} "
            f"web {rw['n_faces']:>6}  HD95 {rp['hd95_mm'] or 0:.2f} mm  dV {rp['volume_change_pct'] or 0:+.1f}%  "
            f"{'ok' if rp['passed'] else 'FAIL ' + '; '.join(rp['issues'])}")
    timings["lods_qc"] = time.perf_counter() - t

    # exports
    t = time.perf_counter()
    structures, web_nodes, print_nodes, print3mf = [], [], [], {}
    for lid in todo:
        lab = lm.label(lid)
        s = natives[lid][0]
        mp, mw = lods[lid]
        rp = reports[lid]["print"]
        extras = _structure_extras(lab, rp, s)
        extras["qc"] = {k: rp[k] for k in ("watertight", "self_intersections", "hd95_mm", "mean_dist_mm",
                                           "volume_change_pct")}
        stem = _stem(lid, lab.name)
        files = {}
        if p.per_structure_files:
            gltf.write_glb(out / "structures" / f"{stem}.glb", [gltf.GltfNode(lab.name, mp, lab.rgb, extras)])
            export.write_stl(out / "structures" / f"{stem}.stl", mp)
            export.write_3mf(out / "structures" / f"{stem}.3mf", {lab.name: (mp, lab.rgb)})
            files = {k: f"structures/{stem}.{k}" for k in ("glb", "stl", "3mf")}
        web_nodes.append(gltf.GltfNode(lab.name, mw, lab.rgb, extras))
        print_nodes.append(gltf.GltfNode(lab.name, mp, lab.rgb, extras))
        print3mf[lab.name] = (mp, lab.rgb)
        structures.append({
            "id": lid, "name": lab.name, "display": lab.display, "rgb": list(lab.rgb),
            "snomed": snomed_triplet(lab.snomed), "files": files,
            "volume_ml": rp["volume_mm3"] / 1000.0, "surface_area_cm2": rp["area_mm2"] / 100.0,
            "qc": {"watertight": rp["watertight"], "self_intersections": rp["self_intersections"],
                   "hd95_mm": rp["hd95_mm"], "mean_dist_mm": rp["mean_dist_mm"],
                   "volume_change_pct": rp["volume_change_pct"], "cut_by_scan_edge": s.cut_by_scan_edge,
                   "passed": rp["passed"], "issues": rp["issues"], "method": s.method,
                   "triangles": {"print": rp["n_faces"], "web": reports[lid]["web"]["n_faces"]},
                   "web_hd95_mm": reports[lid]["web"]["hd95_mm"],
                   "volume_warning": rp["volume_change_pct"] is not None and
                   abs(rp["volume_change_pct"]) > p.limits.volume_warn_pct}})
    frame = {"source_affine_index_to_lps": lm.index_to_lps.tolist(), "label_map_shape_zyx": list(lm.array.shape)}
    gltf.write_glb(out / "combined_web.glb", web_nodes, frame, compress=True)
    gltf.write_glb(out / "combined_print.glb", print_nodes, frame)
    export.write_3mf(out / "combined_print.3mf", print3mf)
    timings["export_meshes"] = time.perf_counter() - t

    save_labelmap(lm, out)
    seg_info = None
    if p.dicom_seg and todo:
        t = time.perf_counter()
        seg_info = export.write_dicom_seg(out / "segmentation.seg.dcm", lm, todo, source_images)
        timings["dicom_seg"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t0

    manifest = {
        "version": 1, "units": "m", "axes": AXES_NOTE, "lps_to_gltf": LPS_TO_GLTF.tolist(),
        "disclaimer": DISCLAIMER, "generator": f"emgmirror.ctmesh {__version__}",
        "structures": structures,
        "combined": {"web": "combined_web.glb", "print": "combined_print.glb", "print_3mf": "combined_print.3mf"},
        "labels": {"nifti": "labels.nii.gz", "json": "labels.json"},
        "dicom_seg": "segmentation.seg.dcm" if seg_info else None,
        "notes": {
            "stl_3mf_units": "mm, patient LPS (not glTF axes)",
            "glb_units": "m, glTF axes; LPS mm = inverse(lps_to_gltf) applied to glTF positions",
            "qc_reference": "label voxel boundary; HD95 limit = "
                            f"{p.limits.hd95_voxels} x voxel size {qc.voxel_mm(lm.spacing):.3f} mm "
                            "(geometric mean of the spacing)",
            "volume_change_pct": "print mesh volume vs voxel count x voxel volume",
            "dicom_seg_references": seg_info["references"] if seg_info else None},
        "skipped_absent_ids": skipped,
        "summary": {"structures": len(structures), "passed": sum(s_["qc"]["passed"] for s_ in structures),
                    "triangles_web": sum(s_["qc"]["triangles"]["web"] for s_ in structures),
                    "triangles_print": sum(s_["qc"]["triangles"]["print"] for s_ in structures),
                    "runtime_s": timings},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (out / "qc.json").write_text(json.dumps({"params": _params_json(p), "structures": list(reports.values())},
                                            indent=1, default=float), encoding="utf-8")
    return manifest


def _params_json(p: PipelineParams) -> dict:
    return {"surface": asdict(p.surface), "print_lod": asdict(p.print_lod), "web_lod": asdict(p.web_lod),
            "web_total_faces": p.web_total_faces, "lod_retries": p.lod_retries, "limits": asdict(p.limits)}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m emgmirror.ctmesh", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labels", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--labels-json", type=Path)
    ap.add_argument("--ids", type=int, nargs="*")
    ap.add_argument("--no-dicom-seg", action="store_true")
    a = ap.parse_args(argv)
    pp = PipelineParams(dicom_seg=not a.no_dicom_seg)
    m = run(a.labels, a.out, a.labels_json, a.ids, pp)
    s = m["summary"]
    print(f"{s['passed']}/{s['structures']} structures passed QC; {s['triangles_web']} web / "
          f"{s['triangles_print']} print triangles; {s['runtime_s']['total']:.1f} s. {DISCLAIMER}")
    return 0 if s["passed"] == s["structures"] else 1
