"""S2 benchmark: the new mesh pipeline vs the old ``emgmirror.ct.mesh`` on the same public label maps.

    python scripts/ctmesh_fetch_totalseg.py DATA s0040 s1641 ...        # public TotalSegmentator subjects
    python scripts/ctmesh_benchmark.py DATA WORK --subjects s0040 ... \
        --render-root JSTOOLS --report docs/ct_platform/results/S2_mesh.md

For every subject: runtime, triangle counts and QC numbers of both pipelines against the label voxel boundary,
the bone accuracy acceptance proposal (mean surface distance <= 1 mm, HD95 <= 2 mm, laterality consistent; from
docs/ct_platform/REGULATORY_PATH.md section 4) for every bone, and before/after renders (three.js in headless
Chromium, ``scripts/ctmesh_render``). Writes the markdown report, a per-structure CSV next to it, and images.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing
import re
import subprocess
import time
from pathlib import Path

import numpy as np

BONE = re.compile(r"^(vertebrae_|rib_|humerus|scapula|clavicula|femur|hip_|sacrum|skull|sternum)")
ACCEPT_MEAN_MM, ACCEPT_HD95_MM = 1.0, 2.0

_G: dict = {}


def _init(g):
    global _G
    _G = g


def _old_one(lid):
    """Old pipeline (marching cubes + Taubin 20 + decimation above 300k faces) and the same QC as the new one."""
    from emgmirror.ct.mesh import MeshParams, mask_to_mesh
    from emgmirror.ctmesh import qc
    from emgmirror.ctmesh.decimate import manifold_ok
    lm = _G["lm"]
    t = time.perf_counter()
    m, _ = mask_to_mesh(lm.array == lid, lm.to_sitk(), MeshParams())
    dt = time.perf_counter() - t
    B = qc.label_boundary(lm.array, lid, lm.index_to_lps)
    d = qc.surface_distance(m.vertices, m.faces, *B, qc.voxel_mm(lm.spacing))
    vox = np.count_nonzero(lm.array == lid) * lm.voxel_volume_mm3
    return lid, m, {"time_s": dt, "n_faces": int(len(m.faces)), "watertight": bool(m.is_watertight),
                    "manifold3d_ok": manifold_ok(m), "self_intersections": int(qc.self_intersections(m)),
                    "hd95_mm": d["hd95_mm"], "mean_dist_mm": d["mean_dist_mm"],
                    "volume_change_pct": (m.volume - vox) / vox * 100 if vox else None}


def run_subject(data: Path, work: Path, sid: str, workers: int) -> dict:
    from emgmirror.ctmesh import load_labelmap
    from emgmirror.ctmesh.pipeline import PipelineParams, run
    out = work / sid / "new"
    t = time.perf_counter()
    man = run(data / sid / "labels.nii.gz", out, params=PipelineParams(workers=workers), log=lambda *a: None)
    t_new = time.perf_counter() - t
    lm = load_labelmap(data / sid / "labels.nii.gz")
    ids = [s["id"] for s in man["structures"]]
    t = time.perf_counter()
    with multiprocessing.get_context("spawn").Pool(workers, initializer=_init, initargs=({"lm": lm},)) as pool:
        old = {lid: (m, r) for lid, m, r in pool.imap(_old_one, ids)}
    t_old_wall = time.perf_counter() - t
    meta = json.loads((data / sid / "source.json").read_text()) if (data / sid / "source.json").exists() else {}
    return {"sid": sid, "shape": list(lm.array.shape), "spacing": list(lm.spacing), "manifest": man,
            "run_log": json.loads((out / "run_log.json").read_text()), "t_new": t_new, "t_old_wall": t_old_wall,
            "old": {lid: r for lid, (m, r) in old.items()}, "old_meshes": {lid: m for lid, (m, r) in old.items()},
            "lm": lm, "meta": meta,
            "web_bytes": (out / "combined_web.glb").stat().st_size,
            "print_bytes": (out / "combined_print.glb").stat().st_size}


def render(render_root: Path, renderer: Path, glb: Path, png: Path, **q) -> dict:
    import shutil
    dst = render_root / "bench" / glb.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(glb, dst)
    shutil.copyfile(renderer.with_name("render.html"), render_root / "render.html")
    args = ["node", str(renderer), str(render_root), str(png), f"bench/{glb.name}"] + [f"{k}={v}" for k, v in q.items()]
    r = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def side_by_side(a: Path, b: Path, out: Path):
    from PIL import Image
    A, B = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    w = Image.new("RGB", (A.width + B.width + 8, max(A.height, B.height)), (255, 255, 255))
    w.paste(A, (0, 0))
    w.paste(B, (A.width + 8, 0))
    w = w.resize((w.width // 2, w.height // 2), Image.LANCZOS)
    w.save(out, quality=82)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("data", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("--subjects", nargs="+", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--render-root", type=Path, help="folder with node_modules/three (renders skipped if absent)")
    ap.add_argument("--render", nargs="*", default=[], help="SID:structure[,structure] close-ups to render")
    a = ap.parse_args(argv)
    from emgmirror.ctmesh import gltf
    img_dir = a.report.parent / "S2_img"
    img_dir.mkdir(parents=True, exist_ok=True)
    renderer = Path(__file__).with_name("ctmesh_render") / "render.mjs"
    results, rows, renders = [], [], []
    closeups = {}
    for r in a.render:
        sid, names = r.split(":")
        closeups.setdefault(sid, []).append(names)
    for sid in a.subjects:
        print("subject", sid, flush=True)
        R = run_subject(a.data, a.work, sid, a.workers)
        man, lm = R["manifest"], R["lm"]
        for s in man["structures"]:
            o = R["old"][s["id"]]
            q = s["qc"]
            bone = bool(BONE.match(s["name"]))
            acc = None
            if bone:
                acc = (q["mean_dist_mm"] <= ACCEPT_MEAN_MM and q["hd95_mm"] <= ACCEPT_HD95_MM
                       and q["laterality"] != "inconsistent")
            rows.append({"subject": sid, "id": s["id"], "name": s["name"], "bone": bone,
                         "method": q["method"], "tri_print": q["triangles"]["print"], "tri_web": q["triangles"]["web"],
                         "watertight": q["watertight"], "self_intersections": q["self_intersections"],
                         "hd95_mm": round(q["hd95_mm"], 3), "mean_dist_mm": round(q["mean_dist_mm"], 3),
                         "web_hd95_mm": round(q["web_hd95_mm"], 3), "volume_change_pct": round(q["volume_change_pct"], 2),
                         "volume_ml": round(s["volume_ml"], 3), "cut_by_scan_edge": q["cut_by_scan_edge"],
                         "laterality": q["laterality"], "qc_passed": q["passed"],
                         "bone_acceptance": "" if acc is None else ("pass" if acc else "FAIL"),
                         "old_tri": o["n_faces"], "old_watertight": o["watertight"],
                         "old_self_intersections": o["self_intersections"], "old_hd95_mm": round(o["hd95_mm"], 3),
                         "old_mean_dist_mm": round(o["mean_dist_mm"], 3),
                         "old_volume_change_pct": round(o["volume_change_pct"], 2)})
        if a.render_root and (a.render_root / "node_modules" / "three").exists():
            names = {lm.label(lid).name: lid for lid in R["old_meshes"]}
            old_glb = a.work / sid / "old_combined.glb"
            gltf.write_glb(old_glb, [gltf.GltfNode(lm.label(lid).name, m, lm.label(lid).rgb)
                                     for lid, m in R["old_meshes"].items()])
            views = [("all", None, "anterior", 1.0)] + [(c.split(",")[0], c, v, 1.0)
                                                        for c in closeups.get(sid, []) for v in ("anterior", "left")]
            for tag, only, view, zoom in views:
                pics = []
                for which, glb in (("old", old_glb), ("new", a.work / sid / "new" / "combined_web.glb")):
                    png = a.work / sid / f"{which}_{tag}_{view}.png"
                    lab = f"{'before: ct/mesh.py (MC + Taubin)' if which == 'old' else 'after: ctmesh web LOD'} - {sid}"
                    q = {"view": view, "label": lab, "zoom": zoom}
                    if only:
                        q["only"] = only
                    info = render(a.render_root, renderer, glb, png, **q)
                    pics.append((png, info))
                jpg = img_dir / f"{sid}_{tag}_{view}.jpg"
                side_by_side(pics[0][0], pics[1][0], jpg)
                renders.append({"sid": sid, "tag": tag, "view": view, "file": jpg.name,
                                "old_tris": pics[0][1].get("tris"), "new_tris": pics[1][1].get("tris")})
        R.pop("old_meshes")
        R.pop("lm")
        results.append(R)
        (a.work / "bench_partial.json").write_text(json.dumps({"rows": rows, "renders": renders}, default=str))
    csv_path = a.report.with_name("S2_mesh_structures.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    a.report.write_text(report(results, rows, renders, csv_path.name), encoding="utf-8")
    print("wrote", a.report, csv_path)


def _med(x):
    return float(np.median(x)) if len(x) else float("nan")


def report(results, rows, renders, csv_name) -> str:
    from emgmirror.ctmesh.provenance import software_versions
    L = []
    L.append("# S2 mesh pipeline: benchmark on public CT label maps\n")
    L.append("Research use, not for diagnosis or surgical planning.\n")
    sv = software_versions()
    L.append(f"Generated by `scripts/ctmesh_benchmark.py`. Software: " + ", ".join(f"{k} {v}" for k, v in sv.items())
             + ". Machine: 4-core cloud container, no GPU.\n")
    L.append("Data: ground-truth label maps of public TotalSegmentator subjects (Zenodo 6802613, v3.0.0, "
             "CC BY 4.0), converted to the S1 contract by `emgmirror.ctmesh.fixtures.totalseg_to_contract`. The "
             "dataset is resampled to 1.5 mm isotropic, so anisotropic grids are covered by the phantom tests "
             "and the tuning table, not here. Every number is measured against the label's exact voxel boundary "
             "(HD95 and mean surface distance both directions; volume vs voxel count).\n")
    L.append("## Per subject\n")
    L.append("| subject | study (meta.csv) | grid (z,y,x) | structures | QC passed | new runtime (s) | old meshing (s, 4 proc) "
             "| print / web triangles | old triangles | combined_web.glb | bones meeting acceptance |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for R in results:
        m = R["manifest"]
        rs = [r for r in rows if r["subject"] == R["sid"]]
        bones = [r for r in rs if r["bone"]]
        L.append(f"| {R['sid']} | {R['meta'].get('study', '')} | {'x'.join(map(str, R['shape']))} | {len(rs)} "
                 f"| {m['summary']['passed']}/{len(rs)} | {R['run_log']['timings_s']['total']:.0f} "
                 f"| {R['t_old_wall']:.0f} | {m['summary']['triangles_print']:,} / {m['summary']['triangles_web']:,} "
                 f"| {sum(r['old_tri'] for r in rs):,} | {R['web_bytes'] / 1e6:.1f} MB "
                 f"| {sum(r['bone_acceptance'] == 'pass' for r in bones)}/{len(bones)} |")
    L.append("")
    tot = {k: sum(R["run_log"]["timings_s"].get(k, 0) for R in results)
           for k in ("surfacenets_all", "meshes_lods_qc", "export_meshes", "dicom_seg", "total")}
    L.append("New pipeline time by stage, all subjects together (s): " + ", ".join(f"{k} {v:.0f}" for k, v in tot.items())
             + ". `meshes_lods_qc` includes the full QC of every LOD (distance, self-intersection, manifold checks).\n")
    L.append("## Old vs new, all structures\n")
    n = len(rows)

    def agg(prefix):
        hd = [r[prefix + "hd95_mm"] for r in rows]
        md = [r[prefix + "mean_dist_mm"] for r in rows]
        dv = [abs(r[prefix + "volume_change_pct"]) for r in rows]
        wt = sum(r[prefix + "watertight"] for r in rows)
        si = sum(r[prefix + "self_intersections"] > 0 for r in rows)
        tri = sum(r["old_tri" if prefix else "tri_print"] for r in rows)
        lim = sum(r[prefix + "hd95_mm"] <= 1.5 for r in rows)
        return (f"{_med(hd):.2f} / {max(hd):.2f}", f"{_med(md):.2f}", f"{_med(dv):.2f} / {max(dv):.1f}",
                f"{wt}/{n}", f"{si}/{n}", f"{lim}/{n}", f"{tri:,}")
    L.append("| pipeline | HD95 median / max (mm) | mean distance median (mm) | abs volume change median / max (%) "
             "| watertight | with self-intersections | HD95 <= 1 voxel (1.5 mm) | triangles |")
    L.append("|---|---|---|---|---|---|---|---|")
    L.append("| old `ct/mesh.py` | " + " | ".join(agg("old_")) + " |")
    L.append("| new `ctmesh` (print LOD) | " + " | ".join(agg("")) + " |")
    web = [r["web_hd95_mm"] for r in rows]
    L.append(f"\nWeb LOD: HD95 median {_med(web):.2f} mm, max {max(web):.2f} mm (display gate 1.5 voxel = 2.25 mm).\n")
    fe = [r for r in rows if r["method"] == "flyingedges"]
    L.append(f"Surfaces from the flying-edges fallback: {len(fe)}/{n} "
             f"({', '.join(sorted({r['name'] for r in fe})) or 'none'}).\n")
    bad = [r for r in rows if not r["qc_passed"]]
    L.append(f"Structures failing the QC gate: {len(bad)}" + (": " + ", ".join(f"{r['subject']}/{r['name']}" for r in bad)
                                                           if bad else "") + ".\n")
    L.append("## Bone accuracy acceptance (proposal)\n")
    L.append(f"Criteria (REGULATORY_PATH.md section 4, proposal): mean surface distance <= {ACCEPT_MEAN_MM} mm and "
             f"HD95 <= {ACCEPT_HD95_MM} mm against the label boundary, laterality consistent (left/right pairs on "
             "the correct side in LPS). Scale is exact by construction and checked on every run "
             "(`self_checks.glb_units`; hazard tests HAZ02). This measures **mesh fidelity to the label map**, not "
             "segmentation accuracy against the patient: that needs S1 and an independent clinical dataset.\n")
    bones = [r for r in rows if r["bone"]]
    fails = [r for r in bones if r["bone_acceptance"] != "pass"]
    L.append(f"Bones evaluated: {len(bones)} across {len(results)} subjects. Passed: {len(bones) - len(fails)}. "
             f"Failed: {len(fails)}" + (": " + ", ".join(f"{r['subject']}/{r['name']}" for r in fails) if fails else "") + ".\n")
    if bones:
        L.append(f"Worst bone mean distance {max(r['mean_dist_mm'] for r in bones):.2f} mm, worst bone HD95 "
                 f"{max(r['hd95_mm'] for r in bones):.2f} mm. Laterality: "
                 f"{sum(r['laterality'] == 'consistent' for r in bones)} paired bones consistent, "
                 f"{sum(r['laterality'] == 'inconsistent' for r in bones)} inconsistent.\n")
    L.append(f"Every structure, with pass/fail: [{csv_name}]({csv_name}).\n")
    if renders:
        L.append("## Before / after renders\n")
        L.append("Left: old `ct/mesh.py` output on the same labels (binary marching cubes, Taubin 20, decimation), "
                 "converted to the same frame and colours. Right: new `combined_web.glb` as a viewer receives it "
                 "(meshopt + quantised), loaded by three.js GLTFLoader + MeshoptDecoder in headless Chromium. Same "
                 "lights and camera.\n")
        for r in renders:
            L.append(f"**{r['sid']} - {r['tag']} - {r['view']}** (triangles shown: old {r['old_tris']:,}, "
                     f"new {r['new_tris']:,})\n")
            L.append(f"![{r['sid']} {r['tag']}](S2_img/{r['file']})\n")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
