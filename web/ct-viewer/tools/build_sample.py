#!/usr/bin/env python3
"""Build a sample result folder for the viewer from one TotalSegmentator subject.

This is a stand-in for the S2 mesher (src/emgmirror/ctmesh/) so the viewer can be
built and tested before S2 lands. Meshing is deliberately simple: Gaussian-
smoothed mask, marching cubes, quadric decimation. The output follows the S2
manifest contract (manifest.json version 1).

    python3 web/ct-viewer/tools/fetch_sample.py --subject s0024 --out data/ct_sample/raw
    python3 web/ct-viewer/tools/build_sample.py data/ct_sample/raw/s0024 web/ct-viewer/public/sample

Needs: numpy, scipy, nibabel, scikit-image, trimesh, fast_simplification
(all BSD/MIT). The web GLB is then compressed with glTF-Transform (MIT):
    npx gltf-transform meshopt combined_web_raw.glb combined_web.glb
which `npm run sample` does for you.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys

import nibabel as nib
import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage import measure

# ---------------------------------------------------------------- terminology
# Display colours follow the 3D Slicer GenericAnatomyColors family (muted,
# anatomical). System grouping drives the material in the viewer.
SKIP = {"face"}  # never render a face surface (re-identification risk)


def classify(name: str) -> tuple[str, str, tuple[int, int, int]]:
    """Return (system, material, rgb) for a TotalSegmentator structure name."""
    n = name
    if re.match(r"(vertebrae_|rib_|sacrum|hip_|femur_|humerus_|scapula_|clavicula_|skull|sternum|costal)", n):
        return "skeleton", "bone", (241, 233, 214)
    if re.search(r"(aorta|artery|iliac_artery|pulmonary_artery|brachiocephalic_trunk|carotid|subclavian)", n):
        return "vessels", "artery", (200, 52, 52)
    if re.search(r"(vena|vein|inferior_vena_cava|portal)", n):
        return "vessels", "vein", (58, 98, 196)
    if re.match(r"(gluteus|autochthon|iliopsoas)", n):
        return "muscles", "muscle", (183, 92, 80)
    if n.startswith("heart"):
        return "organs", "organ", (196, 106, 98) if "myocardium" in n else (206, 128, 124)
    organ_colours = {
        "liver": (157, 96, 72), "spleen": (157, 108, 162), "kidney": (185, 102, 83),
        "gallbladder": (120, 150, 80), "stomach": (216, 160, 132), "pancreas": (224, 194, 134),
        "adrenal": (220, 180, 90), "lung": (197, 165, 145), "esophagus": (211, 171, 143),
        "trachea": (182, 228, 255), "small_bowel": (205, 167, 142), "duodenum": (255, 253, 229),
        "colon": (204, 168, 143), "urinary_bladder": (222, 154, 132), "brain": (250, 250, 225),
    }
    for k, rgb in organ_colours.items():
        if n.startswith(k):
            return "organs", "organ", rgb
    return "other", "organ", (180, 180, 180)


def pretty(name: str) -> str:
    """Plain-language display name, e.g. 'rib_left_3' -> 'Rib, left 3'."""
    special = {
        "inferior_vena_cava": "Inferior vena cava",
        "portal_vein_and_splenic_vein": "Portal and splenic vein",
        "autochthon_left": "Back muscles (erector spinae), left",
        "autochthon_right": "Back muscles (erector spinae), right",
        "clavicula_left": "Clavicle, left", "clavicula_right": "Clavicle, right",
        "hip_left": "Hip bone, left", "hip_right": "Hip bone, right",
        "iliac_vena_left": "Iliac vein, left", "iliac_vena_right": "Iliac vein, right",
    }
    if name in special:
        return special[name]
    m = re.match(r"vertebrae_([CTL]\d+)", name)
    if m:
        return f"Vertebra {m.group(1)}"
    m = re.match(r"rib_(left|right)_(\d+)", name)
    if m:
        return f"Rib {m.group(2)}, {m.group(1)}"
    words = name.split("_")
    side = ""
    if words[-1] in ("left", "right"):
        side = ", " + words.pop()
    return (" ".join(words)).capitalize() + side


# ---------------------------------------------------------------- geometry
RAS_TO_LPS = np.diag([-1.0, -1.0, 1.0, 1.0])
# LPS millimetres -> glTF metres: X = L, Y = S, Z = A (= -P).
LPS_TO_GLTF = np.array(
    [[0.001, 0, 0, 0], [0, 0, 0.001, 0], [0, -0.001, 0, 0], [0, 0, 0, 1]], dtype=float
)


def mesh_from_mask(mask: np.ndarray, vox2lps: np.ndarray, sigma: float) -> trimesh.Trimesh | None:
    idx = np.argwhere(mask)
    if len(idx) < 8:
        return None
    lo = np.maximum(idx.min(0) - 3, 0)
    hi = np.minimum(idx.max(0) + 4, mask.shape)
    sub = mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]].astype(np.float32)
    sub = np.pad(sub, 2)  # closes surfaces cut by the scan edge
    if sigma > 0:
        sub = ndimage.gaussian_filter(sub, sigma)
    try:
        v, f, _, _ = measure.marching_cubes(sub, 0.5)
    except (ValueError, RuntimeError):
        return None
    v = v + lo - 2
    v = (vox2lps[:3, :3] @ v.T).T + vox2lps[:3, 3]
    m = trimesh.Trimesh(v, f[:, ::-1] if np.linalg.det(vox2lps[:3, :3]) < 0 else f, process=True)
    # keep the largest few components; drop specks
    parts = m.split(only_watertight=False)
    if len(parts) > 1:
        parts = sorted(parts, key=lambda p: len(p.faces), reverse=True)
        big = [p for p in parts if len(p.faces) > max(60, 0.02 * len(parts[0].faces))]
        m = trimesh.util.concatenate(big)
    trimesh.repair.fix_normals(m)
    return m


def decimate(m: trimesh.Trimesh, target: int) -> trimesh.Trimesh:
    if len(m.faces) <= target:
        return m
    try:
        d = m.simplify_quadric_decimation(face_count=target)
    except TypeError:  # older trimesh signature
        d = m.simplify_quadric_decimation(target)
    trimesh.repair.fix_normals(d)
    return d


def surface_distance(a: trimesh.Trimesh, b: trimesh.Trimesh) -> tuple[float, float]:
    """Symmetric HD95 and mean vertex-to-vertex distance (mm, approximate)."""
    pa = a.sample(min(20000, max(2000, len(a.faces))))
    pb = b.sample(min(20000, max(2000, len(b.faces))))
    da, _ = cKDTree(pb).query(pa)
    db, _ = cKDTree(pa).query(pb)
    d = np.concatenate([da, db])
    return float(np.percentile(d, 95)), float(d.mean())


def touches_edge(mask: np.ndarray) -> bool:
    return bool(
        mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any()
        or mask[:, :, 0].any() or mask[:, :, -1].any()
    )


def web_budget(material: str, nvox: int) -> int:
    base = {"bone": 6000, "organ": 10000, "muscle": 6000, "artery": 7000, "vein": 5500}[material]
    return int(np.clip(base * (nvox / 40000) ** 0.4, 1500, 40000))


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("subject_dir", type=pathlib.Path)
    ap.add_argument("out_dir", type=pathlib.Path)
    ap.add_argument("--limit", type=int, default=0, help="only the first N structures (debug)")
    args = ap.parse_args()

    src: pathlib.Path = args.subject_dir
    out: pathlib.Path = args.out_dir
    for sub in ("glb", "stl", "3mf"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    ct = nib.load(src / "ct.nii.gz")
    shape = ct.shape
    vox2lps = RAS_TO_LPS @ ct.affine
    ct_data = np.asarray(ct.dataobj).astype(np.int16)
    ct_img = nib.Nifti1Image(ct_data, ct.affine)
    ct_img.header.set_xyzt_units("mm", "sec")
    nib.save(ct_img, out / "ct.nii.gz")
    # Small preview volume (about 2x coarser) for the CT plane inside the 3D view,
    # so the model view never waits for the full-resolution CT.
    step = max(1, int(np.ceil(max(shape) / 256)))
    prev = ct_data[::step, ::step, ::step]
    prev_aff = ct.affine.copy()
    prev_aff[:3, :3] = prev_aff[:3, :3] * step
    prev_img = nib.Nifti1Image(prev, prev_aff)
    prev_img.header.set_xyzt_units("mm", "sec")
    nib.save(prev_img, out / "ct_preview.nii.gz")

    seg_files = sorted((src / "segmentations").glob("*.nii.gz"))
    names = [p.name[: -len(".nii.gz")] for p in seg_files]
    order = sorted(
        [n for n in names if n not in SKIP],
        key=lambda n: (["skeleton", "organs", "vessels", "muscles", "other"].index(classify(n)[0]), n),
    )
    if args.limit:
        order = order[: args.limit]

    labels = np.zeros(shape, np.uint8)
    voxel_ml = float(abs(np.linalg.det(ct.affine[:3, :3]))) / 1000.0
    structures = []
    web_scene = trimesh.Scene()
    print_scene = trimesh.Scene()

    for label_value, name in enumerate(order, start=1):
        mask = np.asarray(nib.load(src / "segmentations" / f"{name}.nii.gz").dataobj) > 0
        nvox = int(mask.sum())
        if nvox < 20:
            continue
        labels[mask & (labels == 0)] = label_value
        system, material, rgb = classify(name)
        sigma = 0.6 if material in ("bone", "artery", "vein") else 0.9
        raw = mesh_from_mask(mask, vox2lps, 0.0)
        smooth = mesh_from_mask(mask, vox2lps, sigma)
        if smooth is None or raw is None:
            continue
        printable = decimate(smooth, max(4000, min(150000, len(smooth.faces) // 2)))
        web = decimate(smooth, web_budget(material, nvox))
        hd95, mean_d = surface_distance(web, raw)
        voxel_volume = nvox * voxel_ml
        mesh_volume = abs(float(printable.volume)) / 1000.0 if printable.is_watertight else float("nan")
        vol_change = (mesh_volume - voxel_volume) / voxel_volume * 100.0 if voxel_volume else 0.0

        # to glTF metres (+Y up)
        web_g = web.copy()
        web_g.apply_transform(LPS_TO_GLTF)
        print_g = printable.copy()
        print_g.apply_transform(LPS_TO_GLTF)
        colour = np.array([*rgb, 255], np.uint8)
        for g in (web_g, print_g):
            g.visual = trimesh.visual.ColorVisuals(g, face_colors=np.tile(colour, (len(g.faces), 1)))
            g.metadata["name"] = name
        web_g.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(
                name=name, baseColorFactor=colour, roughnessFactor=0.6, metallicFactor=0.0
            )
        )
        web_scene.add_geometry(web_g, node_name=name, geom_name=name)
        print_scene.add_geometry(print_g, node_name=name, geom_name=name)

        # per-structure files (printing units: STL/3MF in millimetres)
        printable.export(out / "stl" / f"{name}.stl")
        print_mm = printable.copy()
        print_mm.visual = trimesh.visual.ColorVisuals(
            print_mm, face_colors=np.tile(colour, (len(print_mm.faces), 1))
        )
        print_mm.export(out / "3mf" / f"{name}.3mf")
        trimesh.Scene({name: web_g}).export(out / "glb" / f"{name}.glb")

        structures.append(
            {
                "id": name,
                "name": pretty(name),
                "label": label_value,
                "display": {"system": system, "material": material},
                "rgb": list(rgb),
                "files": {"glb": f"glb/{name}.glb", "stl": f"stl/{name}.stl", "3mf": f"3mf/{name}.3mf"},
                "volume_ml": round(voxel_volume, 1),
                "surface_area_cm2": round(float(printable.area) / 100.0, 1),
                "qc": {
                    "watertight": bool(printable.is_watertight),
                    "self_intersections": None,
                    "hd95_mm": round(hd95, 2),
                    "mean_dist_mm": round(mean_d, 2),
                    "volume_change_pct": None if np.isnan(vol_change) else round(vol_change, 1),
                    "cut_by_scan_edge": touches_edge(mask),
                },
            }
        )
        print(f"{label_value:3d} {name:32s} {len(web.faces):6d} web tris  hd95 {hd95:.2f} mm", flush=True)

    lab_img = nib.Nifti1Image(labels, ct.affine)
    lab_img.header.set_xyzt_units("mm", "sec")
    nib.save(lab_img, out / "labels.nii.gz")
    web_scene.export(out / "combined_web_raw.glb")
    print_scene.export(out / "combined_print.glb")

    manifest = {
        "version": 1,
        "units": "m",
        "axes": "glTF (+Y up, from patient LPS)",
        "lps_to_gltf": LPS_TO_GLTF.tolist(),
        "source": {
            "dataset": "TotalSegmentator dataset v1 (CC BY 4.0)",
            "doi": "10.5281/zenodo.6802613",
            "subject": src.name,
            "mesher": "sample builder (marching cubes + quadric decimation), not the S2 mesher",
        },
        # De-identified study identifiers shown on screen at all times (proposed for S2).
        # The public dataset has no study date or series text.
        "study": {
            "study_date": None,
            "series_description": f"TotalSegmentator {src.name} (public dataset)",
            "study_hash": hashlib.sha256((out / "ct.nii.gz").read_bytes()).hexdigest(),
        },
        "structures": structures,
        "combined": {"web": "combined_web.glb", "print": "combined_print.glb"},
        # Optional keys the viewer understands (proposed for S2):
        "volumes": {"ct": "ct.nii.gz", "labels": "labels.nii.gz", "ct_preview": "ct_preview.nii.gz"},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    shutil.copy(src / "SOURCE.txt", out / "SOURCE.txt") if (src / "SOURCE.txt").exists() else None
    print(f"{len(structures)} structures written to {out}")


if __name__ == "__main__":
    sys.exit(main())
