"""The S1 → S2 input contract: ``labels.nii.gz`` (uint16 label map, same grid and affine as the CT) plus
``labels.json``::

    {"version": 1,
     "labels": [{"id": int, "name": str, "display": str, "rgb": [r, g, b], "snomed": {...} | null,
                 "source_model": str, "flags": [str]}],
     "qc": {...}}

NIfTI stores RAS; SimpleITK converts to ITK/DICOM LPS on read, so everything here is LPS.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from .geometry import index_to_lps_matrix

GENERIC_RGB = (177, 122, 101)


@dataclass
class Label:
    id: int
    name: str
    display: str = ""
    rgb: tuple[int, int, int] = GENERIC_RGB
    snomed: dict | None = None
    source_model: str = ""
    flags: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.id = int(self.id)
        self.display = self.display or self.name.replace("_", " ")
        self.rgb = tuple(int(c) for c in self.rgb)[:3]
        self.flags = list(self.flags or [])

    def to_json(self) -> dict:
        d = asdict(self)
        d["rgb"] = list(self.rgb)
        return d


@dataclass
class LabelMap:
    array: np.ndarray  # uint16 [z, y, x]
    spacing: tuple[float, float, float]  # x, y, z mm
    origin: tuple[float, float, float]  # LPS mm of voxel (0, 0, 0) centre
    direction: np.ndarray  # 3x3, columns = LPS direction of the i, j, k axes
    labels: list[Label]
    qc: dict = field(default_factory=dict)
    nifti_path: Path | None = None

    @property
    def index_to_lps(self) -> np.ndarray:
        return index_to_lps_matrix(self.origin, self.spacing, self.direction)

    @property
    def voxel_volume_mm3(self) -> float:
        return float(np.prod(self.spacing))

    def label(self, lid: int) -> Label:
        for lab in self.labels:
            if lab.id == lid:
                return lab
        raise KeyError(lid)

    def to_sitk(self) -> sitk.Image:
        im = sitk.GetImageFromArray(np.ascontiguousarray(self.array, np.uint16))
        im.SetSpacing(tuple(float(v) for v in self.spacing))
        im.SetOrigin(tuple(float(v) for v in self.origin))
        im.SetDirection(tuple(float(v) for v in np.asarray(self.direction, float).ravel()))
        return im

    @classmethod
    def from_sitk(cls, im: sitk.Image, labels: list[Label], qc: dict | None = None) -> "LabelMap":
        arr = sitk.GetArrayFromImage(im)
        if arr.ndim != 3:
            raise ValueError(f"label map must be 3-D, got shape {arr.shape}")
        if not np.issubdtype(arr.dtype, np.integer):
            if not np.all(np.mod(arr, 1) == 0):
                raise ValueError("label map has non-integer values")
        if arr.min() < 0 or arr.max() > 65535:
            raise ValueError("label values must fit uint16")
        return cls(arr.astype(np.uint16, copy=False), tuple(im.GetSpacing()), tuple(im.GetOrigin()),
                   np.array(im.GetDirection(), float).reshape(3, 3), labels, qc or {})


def _labels_from_json(doc: dict) -> list[Label]:
    if int(doc.get("version", 1)) != 1:
        raise ValueError(f"unsupported labels.json version {doc.get('version')}")
    out, seen = [], set()
    for d in doc.get("labels", []):
        lab = Label(id=d["id"], name=str(d["name"]), display=str(d.get("display") or ""),
                    rgb=tuple(d.get("rgb") or GENERIC_RGB), snomed=d.get("snomed"),
                    source_model=str(d.get("source_model") or ""), flags=list(d.get("flags") or []))
        if lab.id <= 0 or lab.id > 65535:
            raise ValueError(f"label id {lab.id} out of range 1..65535")
        if lab.id in seen:
            raise ValueError(f"duplicate label id {lab.id}")
        seen.add(lab.id)
        out.append(lab)
    return out


def load_labelmap(nifti: str | Path, labels_json: str | Path | None = None) -> LabelMap:
    """Read the contract. ``labels_json`` defaults to ``labels.json`` next to the NIfTI. Label ids present in the
    image but missing from the JSON get a generic entry (flag ``unlisted``)."""
    nifti = Path(nifti)
    labels_json = Path(labels_json) if labels_json else nifti.with_name("labels.json")
    doc = json.loads(labels_json.read_text(encoding="utf-8"))
    im = sitk.ReadImage(str(nifti))
    lm = LabelMap.from_sitk(im, _labels_from_json(doc), doc.get("qc") or {})
    lm.nifti_path = nifti
    present = set(np.unique(lm.array).tolist()) - {0}
    listed = {lab.id for lab in lm.labels}
    for lid in sorted(present - listed):
        lm.labels.append(Label(id=lid, name=f"label_{lid}", flags=["unlisted"]))
    return lm


def save_labelmap(lm: LabelMap, out_dir: str | Path) -> tuple[Path, Path]:
    """Write ``labels.nii.gz`` + ``labels.json`` (copies the source NIfTI byte-for-byte when there is one)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nii = out_dir / "labels.nii.gz"
    if lm.nifti_path is not None and Path(lm.nifti_path).resolve() != nii.resolve():
        shutil.copyfile(lm.nifti_path, nii)
    elif lm.nifti_path is None:
        sitk.WriteImage(lm.to_sitk(), str(nii), useCompression=True)
    js = out_dir / "labels.json"
    js.write_text(json.dumps({"version": 1, "labels": [lab.to_json() for lab in lm.labels], "qc": lm.qc},
                             indent=1), encoding="utf-8")
    return nii, js


def snomed_triplet(sn: dict | None) -> dict | None:
    """Normalise the ``snomed`` field to {"category": code, "type": code, "modifier": code|None} where
    code = {"scheme", "value", "meaning"}. Accepts our own shape and dcmqi-style keys (CodeValue, ...)."""
    if not sn:
        return None

    def code(c):
        if not c:
            return None
        if isinstance(c, (str, int)):
            return {"scheme": "SCT", "value": str(c), "meaning": str(c)}
        v = c.get("value", c.get("code", c.get("CodeValue")))
        if v in (None, ""):
            return None
        return {"scheme": str(c.get("scheme", c.get("CodingSchemeDesignator", "SCT"))), "value": str(v),
                "meaning": str(c.get("meaning", c.get("CodeMeaning", v)))}

    t = code(sn.get("type", sn.get("Type")))
    if t is None:
        return None
    return {"category": code(sn.get("category", sn.get("Category"))) or
            {"scheme": "SCT", "value": "123037004", "meaning": "Anatomical Structure"},
            "type": t, "modifier": code(sn.get("modifier", sn.get("TypeModifier", sn.get("Modifier"))))}
