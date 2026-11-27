"""Benchmark helpers: fetch a few public TotalSegmentator-dataset subjects and score the engine against them.

Data: TotalSegmentator dataset v3.0.0, Zenodo record 6802613 (CC BY 4.0). The archive is one ~37 GB zip;
:class:`RemoteZip` reads its central directory and the chosen members with HTTP range requests, so only the
selected subjects are downloaded. Only subjects from the dataset's ``test`` split are used.

Scores per structure (reference = the dataset's segmentation, prediction = the fused engine output, with
``part_of`` children merged back into their parent): Dice on the native grid, and mean symmetric surface
distance and HD95 in mm from :func:`emgmirror.ct.validate.overlap_metrics`.
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import SimpleITK as sitk

ZENODO_RECORD = "https://zenodo.org/api/records/6802613"
# Study types in the order they are picked (one subject each), chosen to spread over body regions.
STUDY_ORDER = ["ct head", "ct angiography head", "ct neck", "ct chest-neck", "ct chest-abdomen", "ct abdomen",
               "ct abdomen-pelvis", "ct pelvis", "ct chest-abdomen-pelvis", "ct neck-chest-abdomen-pelvis",
               "ct polytrauma", "ct spine", "ct angiography pelvis-leg", "ct upper leg", "ct hip", "ct heart",
               "ct angiography chest-abdomen-pelvis", "ct gesichtshead"]


class _RangeFile(io.RawIOBase):
    def __init__(self, url: str, size: int) -> None:
        self.url, self.size, self.pos = url, size, 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, off: int, whence: int = 0) -> int:
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def readinto(self, b) -> int:
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        for attempt in range(5):
            try:
                req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}",
                                                                "User-Agent": "ctseg-benchmark"})
                data = urllib.request.urlopen(req, timeout=120).read()
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


class RemoteZip:
    def __init__(self, url: str, size: int) -> None:
        self.zip = zipfile.ZipFile(io.BufferedReader(_RangeFile(url, size), buffer_size=4 << 20))

    @classmethod
    def from_zenodo(cls, record_url: str = ZENODO_RECORD) -> "RemoteZip":
        req = urllib.request.Request(record_url, headers={"User-Agent": "ctseg-benchmark"})
        meta = json.loads(urllib.request.urlopen(req, timeout=60).read())
        f = max(meta["files"], key=lambda x: x["size"])
        return cls(f["links"]["self"], int(f["size"]))


def read_meta(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"))


def pick_subjects(meta: list[dict], n: int) -> list[dict]:
    test = sorted((m for m in meta if m.get("split") == "test"), key=lambda m: m["image_id"])
    out, used = [], set()
    for st in STUDY_ORDER:
        for m in test:
            if m["study_type"] == st and m["image_id"] not in used:
                out.append(m)
                used.add(m["image_id"])
                break
        if len(out) >= n:
            break
    for m in test:  # top up from the rest of the test split
        if len(out) >= n:
            break
        if m["image_id"] not in used:
            out.append(m)
            used.add(m["image_id"])
    return out


def fetch_subject(rz: RemoteZip, image_id: str, dest: Path) -> Path:
    """Extract ``<id>/ct.nii.gz`` and ``<id>/segmentations/*.nii.gz`` into ``dest/<id>``."""
    root = next(n.split("/")[0] for n in rz.zip.namelist()[:5])
    pre = f"{root}/{image_id}/"
    d = dest / image_id
    if (d / "ct.nii.gz").exists() and (d / "segmentations").is_dir():
        return d
    for name in rz.zip.namelist():
        if name.startswith(pre) and name.endswith(".nii.gz"):
            target = d / name[len(pre):]
            target.parent.mkdir(parents=True, exist_ok=True)
            with rz.zip.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())
    return d


def _read(path: Path) -> sitk.Image:
    """SimpleITK read with the engine's fallback for slightly non-orthonormal headers."""
    try:
        return sitk.ReadImage(str(path))
    except RuntimeError as e:
        if "orthonormal" not in str(e):
            raise
        from .inputs import _load_nifti_repaired
        return _load_nifti_repaired(Path(path), 10**10, 10**10)[0]


def _same_physical_grid(a: sitk.Image, b: sitk.Image) -> bool:
    """Whether voxel indices refer to the same physical coordinates, not just same array shape."""
    return (
        a.GetSize() == b.GetSize()
        and np.allclose(a.GetSpacing(), b.GetSpacing(), atol=1e-4, rtol=0)
        and np.allclose(a.GetOrigin(), b.GetOrigin(), atol=1e-3, rtol=0)
        and np.allclose(a.GetDirection(), b.GetDirection(), atol=1e-5, rtol=0)
    )


def score_subject(labels_path: Path, labels_json: dict, seg_dir: Path, *, surface: bool = True,
                  max_points: int = 20_000) -> list[dict]:
    """Per-structure Dice (+ surface distance) of the engine output against the reference masks."""
    from emgmirror.ct.validate import overlap_metrics

    from . import ontology

    pred_img = sitk.ReadImage(str(labels_path))
    pred = sitk.GetArrayViewFromImage(pred_img)
    children: dict[str, list[int]] = {}
    for e in labels_json["labels"]:
        for f in e["flags"]:
            if f.startswith("part_of:"):
                children.setdefault(f.split(":", 1)[1], []).append(e["id"])
    rows = []
    for f in sorted(seg_dir.glob("*.nii.gz")):
        name = f.name[:-7]
        if name not in ontology.table():
            continue
        ref_img = _read(f)
        if not _same_physical_grid(ref_img, pred_img):
            ref_img = sitk.Resample(ref_img, pred_img, sitk.Transform(), sitk.sitkNearestNeighbor, 0)
        ref = sitk.GetArrayViewFromImage(ref_img) > 0
        ids = [ontology.label(name).id] + children.get(name, [])
        # lung lobes are scored on their own label only: lung_vessels voxels carved out of a lobe count as
        # misses (there is no unbiased way to assign them back to one lobe)
        tst = np.isin(pred, ids)
        nr, nt = int(ref.sum()), int(tst.sum())
        if nr == 0 and nt == 0:
            continue
        inter = int(np.count_nonzero(ref & tst))
        row = {"structure": name, "ref_voxels": nr, "pred_voxels": nt,
               "dice": 2.0 * inter / (nr + nt) if nr + nt else float("nan")}
        if surface and nr and nt:
            a = sitk.GetImageFromArray(ref.astype(np.uint8))
            a.CopyInformation(ref_img)
            b = sitk.GetImageFromArray(tst.astype(np.uint8))
            b.CopyInformation(pred_img)
            m = overlap_metrics(a, b, max_points=max_points)
            row.update(msd_mm=m.get("msd_mm"), hd95_mm=m.get("hd95_mm"))
        if name.endswith(("_left", "_right")) and nt:
            base, side = name.rsplit("_", 1)
            other = seg_dir / f"{base}_{'right' if side == 'left' else 'left'}.nii.gz"
            if other.exists():
                o_img = _read(other)
                if not _same_physical_grid(o_img, pred_img):
                    o_img = sitk.Resample(o_img, pred_img, sitk.Transform(), sitk.sitkNearestNeighbor, 0)
                same = inter
                opposite = int(np.count_nonzero(tst & (sitk.GetArrayViewFromImage(o_img) > 0)))
                if same or opposite:
                    # correct side: the predicted label overlaps the reference structure of its own side more
                    row["laterality_ok"] = same > opposite
        rows.append(row)
    return rows
