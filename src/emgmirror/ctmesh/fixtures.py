"""Test and benchmark inputs in the S1 contract, until S1 lands.

* :func:`oblique_phantom`: analytic shapes defined in LPS mm (ellipsoid, small sphere at a known point, tilted
  thin-walled tube, thin plate, two touching blocks) voxelised on an arbitrary oblique, anisotropic grid by
  supersampled majority vote. The analytic truth (centres, volumes) is returned so geometry can be checked.
* :func:`forearm_phantom`: the analytic forearm phantom of ``emgmirror.ct.phantom`` as a contract label map.
* :func:`totalseg_to_contract`: one subject of the public TotalSegmentator dataset (Zenodo 6802613, CC BY 4.0)
  - a folder of per-structure ``segmentations/*.nii.gz`` masks - merged into ``labels.nii.gz`` + ``labels.json``,
  with names, SNOMED codes and colours from SlicerTotalSegmentator's mapping table (Apache-2.0, vendored in
  ``data/``).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from .contract import Label, LabelMap, save_labelmap
from .geometry import index_to_lps_matrix

DATA = Path(__file__).with_name("data")


def rotation(axis, angle_deg: float) -> np.ndarray:
    a = np.asarray(axis, float)
    a /= np.linalg.norm(a)
    t = np.radians(angle_deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(t) * K + (1 - np.cos(t)) * K @ K


@dataclass
class Phantom:
    labelmap: LabelMap
    truth: dict = field(default_factory=dict)


def _voxelise(shape, spacing, origin, direction, inside, f: int = 3) -> np.ndarray:
    """Label map [z, y, x] where each voxel takes the label covering most of its f^3 sub-samples. ``inside(P)``
    maps (n, 3) LPS points to uint16 labels (0 = background); later shapes override earlier ones."""
    nz, ny, nx = shape
    M = index_to_lps_matrix(origin, spacing, direction)
    sub = (np.arange(f) + 0.5) / f - 0.5
    out = np.zeros(shape, np.uint16)
    jj, ii = np.meshgrid(np.arange(ny), np.arange(nx), indexing="ij")
    for k in range(nz):
        votes = []
        for dz in sub:
            for dy in sub:
                for dx in sub:
                    idx = np.stack([ii + dx, jj + dy, np.full_like(ii, k, float) + dz], -1).reshape(-1, 3)
                    votes.append(inside(idx @ M[:3, :3].T + M[:3, 3]))
        V = np.stack(votes)  # (f^3, ny*nx)
        n_lab = int(V.max()) + 1
        counts = np.stack([(V == v).sum(0) for v in range(n_lab)])
        out[k] = counts.argmax(0).reshape(ny, nx)
    return out


def oblique_phantom(shape=(48, 64, 64), spacing=(1.2, 1.0, 1.6), origin=(-20.0, 35.0, 600.0),
                    direction=None, f: int = 3) -> Phantom:
    """Shapes in LPS mm around the grid centre, on a grid tilted by ``direction`` (default: 25 degrees about an
    oblique axis plus a 10 degree gantry-style shear-free tilt)."""
    D = rotation((0.3, 1.0, 0.2), 25.0) @ rotation((1, 0, 0), 10.0) if direction is None else np.asarray(direction)
    nz, ny, nx = shape
    M = index_to_lps_matrix(origin, spacing, D)
    c = M[:3, :3] @ (np.array([nx, ny, nz]) - 1) / 2 + M[:3, 3]  # LPS centre of the grid
    R = rotation((1, 1, 0), 30.0)
    ell_c, ell_ax = c + np.array([-8.0, 0.0, 0.0]), np.array([16.0, 11.0, 9.0])
    sph_c, sph_r = c + np.array([14.0, -12.0, 10.0]), 4.0
    tube_c, tube_a = c + np.array([14.0, 10.0, 0.0]), np.array([0.2, 0.3, 1.0]) / np.linalg.norm([0.2, 0.3, 1.0])
    tube_r, tube_wall, tube_half = 4.5, 1.8, 14.0
    plate_c, plate_half = c + np.array([-8.0, 0.0, -20.0]), np.array([12.0, 8.0, 1.25])
    blk_c = c + np.array([-6.0, 18.0, 14.0])

    def inside(P):
        lab = np.zeros(len(P), np.uint16)
        Q = (P - ell_c) @ R
        lab[((Q / ell_ax) ** 2).sum(1) <= 1] = 1
        lab[((P - sph_c) ** 2).sum(1) <= sph_r ** 2] = 2
        d = P - tube_c
        t = d @ tube_a
        r2 = (d ** 2).sum(1) - t ** 2
        lab[(r2 <= tube_r ** 2) & (r2 >= (tube_r - tube_wall) ** 2) & (np.abs(t) <= tube_half)] = 3
        lab[(np.abs(P - plate_c) <= plate_half).all(1)] = 4
        d = P - blk_c
        box = (np.abs(d[:, 0]) <= 5) & (np.abs(d[:, 1]) <= 5) & (np.abs(d[:, 2]) <= 5)
        lab[box & (d[:, 0] < 0)] = 5
        lab[box & (d[:, 0] >= 0)] = 6
        return lab

    arr = _voxelise(shape, spacing, origin, D, inside, f)
    names = ["ellipsoid", "marker_sphere", "thin_tube", "thin_plate", "block_left", "block_right"]
    rgbs = [(216, 101, 79), (241, 214, 145), (200, 60, 60), (230, 220, 200), (128, 174, 128), (111, 184, 210)]
    labels = [Label(i + 1, n, rgb=rgb, source_model="analytic phantom") for i, (n, rgb) in enumerate(zip(names, rgbs))]
    lm = LabelMap(arr, tuple(float(v) for v in spacing), tuple(float(v) for v in origin), D, labels,
                  {"source": "ctmesh.fixtures.oblique_phantom"})
    truth = {
        "centre_lps": c, "ellipsoid": {"centre": ell_c, "axes": ell_ax, "R": R,
                                       "volume_mm3": float(4 / 3 * np.pi * np.prod(ell_ax))},
        "marker_sphere": {"centre": sph_c, "radius": sph_r, "volume_mm3": float(4 / 3 * np.pi * sph_r ** 3)},
        "thin_tube": {"volume_mm3": float(np.pi * (tube_r ** 2 - (tube_r - tube_wall) ** 2) * 2 * tube_half)},
        "thin_plate": {"centre": plate_c, "volume_mm3": float(np.prod(2 * plate_half))},
        "blocks": {"centre": blk_c, "volume_mm3": 500.0},
    }
    return Phantom(lm, truth)


def forearm_phantom(**kw) -> Phantom:
    """The analytic forearm phantom (``emgmirror.ct.phantom``) as a contract label map."""
    from ..ct import labels as L
    from ..ct.phantom import make_phantom
    p = make_phantom(**kw)
    names = L.default_names(2, ["radius", "ulna"])
    hexes = {"fat": L.COLORS["fat"], "muscle": L.COLORS["muscle"], "metal": L.COLORS["metal"]}
    present = set(np.unique(sitk.GetArrayViewFromImage(p.truth)).tolist()) - {0}
    labels = []
    for lid, name in names.items():
        if lid not in present:
            continue
        key = name.split("_")[-1]
        h = hexes.get(name, L.COLORS.get(key, "#b0b0b0")).lstrip("#")
        labels.append(Label(lid, name, rgb=tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)),
                            source_model="emgmirror.ct.phantom"))
    lm = LabelMap.from_sitk(sitk.Cast(p.truth, sitk.sitkUInt16), labels, {"source": "ct.phantom"})
    return Phantom(lm, p.analytic)


def totalseg_table() -> dict[str, dict]:
    """name -> {"order", "rgb", "snomed"} from SlicerTotalSegmentator's totalsegmentator_snomed_mapping.csv."""
    out = {}
    with open(DATA / "totalsegmentator_snomed_mapping.csv", newline="", encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            def code(prefix):
                v = (r.get(f"{prefix}_CodeValue") or "").strip()
                return {"scheme": r[f"{prefix}_CodingScheme"].strip(), "value": v,
                        "meaning": r[f"{prefix}_CodeMeaning"].strip()} if v else None
            rgb = [int(float(r[k])) for k in ("Color_R", "Color_G", "Color_B")] if r.get("Color_R") else None
            out.setdefault(r["Name"].strip(), {"order": i, "rgb": rgb,
                                                "snomed": {"category": code("Category"), "type": code("Type"),
                                                           "modifier": code("TypeModifier")}})
    return out


def totalseg_to_contract(subject_dir: str | Path, out_dir: str | Path) -> LabelMap:
    """Merge ``subject_dir/segmentations/*.nii.gz`` (binary masks) into one uint16 label map. Ids follow the
    SlicerTotalSegmentator table order (= TotalSegmentator ``total`` class order); unknown names go last. Where
    masks overlap the later id wins and the overlap is counted in ``qc``."""
    subject_dir = Path(subject_dir)
    seg = subject_dir / "segmentations"
    files = sorted(seg.glob("*.nii.gz"))
    if not files:
        raise FileNotFoundError(f"no masks in {seg}")
    table = totalseg_table()
    names = [f.name[:-7] for f in files]
    names.sort(key=lambda n: (table.get(n, {}).get("order", 10 ** 6), n))
    ref = sitk.ReadImage(str(seg / f"{names[0]}.nii.gz"))
    arr = np.zeros(sitk.GetArrayViewFromImage(ref).shape, np.uint16)
    labels, overlap = [], 0
    for lid, n in enumerate(names, start=1):
        im = sitk.ReadImage(str(seg / f"{n}.nii.gz"))
        if im.GetSize() != ref.GetSize() or not np.allclose(im.GetOrigin(), ref.GetOrigin(), atol=1e-3):
            raise ValueError(f"{n}: mask grid differs from {names[0]}")
        m = sitk.GetArrayViewFromImage(im) > 0
        if not m.any():
            continue
        overlap += int(np.count_nonzero(arr[m]))
        arr[m] = lid
        t = table.get(n, {})
        sn = t.get("snomed") or {}
        display = ""
        if sn.get("type"):
            display = sn["type"]["meaning"] + (f" ({sn['modifier']['meaning'].lower()})" if sn.get("modifier") else "")
        labels.append(Label(lid, n, display=display, rgb=tuple(t.get("rgb") or (177, 122, 101)),
                            snomed=t.get("snomed") if t.get("snomed", {}).get("type") else None,
                            source_model="TotalSegmentator dataset ground truth (Zenodo 6802613, CC BY 4.0)"))
    out = sitk.GetImageFromArray(arr)
    out.CopyInformation(ref)
    lm = LabelMap.from_sitk(out, labels, {"source": f"TotalSegmentator dataset {subject_dir.name}",
                                          "overlap_voxels": overlap})
    lm.nifti_path = None
    save_labelmap(lm, out_dir)
    lm.nifti_path = Path(out_dir) / "labels.nii.gz"
    meta = {"subject": subject_dir.name, "licence": "CC BY 4.0", "source": "https://zenodo.org/records/6802613",
            "n_labels": len(labels)}
    (Path(out_dir) / "source.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return lm
