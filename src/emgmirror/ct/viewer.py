"""Single-file offline HTML viewer for CT-derived 3D models and the scan they came from.

``write_viewer`` produces one ``.html`` file with no external requests. Binary data are gzip-compressed,
base64-encoded and stored in a JSON block that the page unpacks with the browser's ``DecompressionStream``.

Embedded binary layout (all little-endian, C order):

* mesh vertices: ``uint16`` quantised, shape ``(N, 3)``, ``v = bbox_min + q / 65535 * (bbox_max - bbox_min)``
  per mesh and axis, so the error is at most half a step, i.e. ``extent / 131070`` (about 0.002 mm for 250 mm)
* mesh faces: ``uint32``, shape ``(M, 3)``
* volume: ``int16`` HU, shape ``(nz, ny, nx)`` (the ``[z, y, x]`` NumPy view of the SimpleITK image)
* labels: ``uint8``, same shape as the volume
"""

from __future__ import annotations

import base64
import gzip
import html
import json
import re
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from . import DISCLAIMER
from . import labels as L

_TEMPLATE = Path(__file__).parent / "static" / "viewer_template.html"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
FORMAT_VERSION = 1


def _pack(arr: np.ndarray) -> str:
    return base64.b64encode(gzip.compress(np.ascontiguousarray(arr).tobytes(), 6, mtime=0)).decode("ascii")


def _quantise(v: np.ndarray) -> tuple[np.ndarray, list[float]]:
    lo, hi = v.min(axis=0), v.max(axis=0)
    ext = np.where(hi > lo, hi - lo, 1.0)
    q = np.rint((v - lo) / ext * 65535.0).astype("<u2")
    return q, [float(x) for x in (*lo, *hi)]


def _mesh_entry(name: str, m: dict) -> dict | None:
    v = np.asarray(m["vertices"], dtype=np.float64).reshape(-1, 3)
    f = np.asarray(m["faces"]).reshape(-1, 3)
    if len(v) == 0 or len(f) == 0:
        return None
    if f.min() < 0 or f.max() >= len(v):
        raise ValueError(f"mesh {name!r}: face index out of range")
    q, bbox = _quantise(v)
    color = str(m.get("color", "#b0b0b0"))
    return {
        "name": name, "label": str(m.get("label", name)), "group": str(m.get("group", "Structures")),
        "color": color if _HEX.match(color) else "#b0b0b0",
        "opacity": float(np.clip(m.get("opacity", 1.0), 0.0, 1.0)), "visible": bool(m.get("visible", True)),
        "info": {str(k): str(x) for k, x in (m.get("info") or {}).items()},
        "explain": str(m.get("explain", "")), "nv": int(len(v)), "nf": int(len(f)), "bbox": bbox,
        "verts": _pack(q), "faces": _pack(f.astype("<u4")),
    }


def _default_label_meta(labels: sitk.Image) -> tuple[dict[int, str], dict[int, str]]:
    ids = [int(i) for i in np.unique(sitk.GetArrayViewFromImage(labels)) if i > 0]
    n_bones = max([L.bone_index(i) + 1 for i in ids if L.is_bone(i)], default=0)
    names = {i: n for i, n in L.default_names(n_bones).items() if i in ids}
    base = {L.FAT: L.COLORS["fat"], L.MUSCLE: L.COLORS["muscle"], L.METAL: L.COLORS["metal"]}
    colors = {i: base.get(i, L.COLORS["medullary"] if i % 2 else L.COLORS["cortical"]) for i in ids}
    return names, colors


def write_viewer(out_path: Path, meshes: dict[str, dict], volume: sitk.Image | None = None,
                 labels: sitk.Image | None = None, meta: dict | None = None) -> Path:
    """Write a self-contained offline viewer to ``out_path`` and return it. See the module docstring for formats."""
    out_path = Path(out_path)
    meta = dict(meta or {})
    entries = [e for n, m in meshes.items() if (e := _mesh_entry(n, m)) is not None]
    vol_block: dict | None = None
    lab_b64: str | None = None
    if labels is not None and volume is None:
        raise ValueError("labels need a volume on the same grid")
    if volume is not None:
        arr = np.clip(sitk.GetArrayViewFromImage(volume), -32768, 32767).astype("<i2")
        vol_block = {"size": [int(s) for s in volume.GetSize()], "spacing": [float(s) for s in volume.GetSpacing()],
                     "origin": [float(s) for s in volume.GetOrigin()],
                     "direction": [float(s) for s in volume.GetDirection()], "data": _pack(arr)}
        if labels is not None:
            if tuple(labels.GetSize()) != tuple(volume.GetSize()):
                raise ValueError("labels must have the same size as the volume")
            lab_b64 = _pack(sitk.GetArrayViewFromImage(labels).astype("u1"))
            if not meta.get("label_names") or not meta.get("label_colors"):
                names, colors = _default_label_meta(labels)
                meta["label_names"] = meta.get("label_names") or names
                meta["label_colors"] = meta.get("label_colors") or colors
    clean = {
        "title": str(meta.get("title", "CT scan")), "subtitle": str(meta.get("subtitle", "")),
        "summary": [str(s) for s in meta.get("summary", [])], "notes": [str(s) for s in meta.get("notes", [])],
        "label_names": {str(k): str(v) for k, v in (meta.get("label_names") or {}).items()},
        "label_colors": {str(k): str(v) for k, v in (meta.get("label_colors") or {}).items() if _HEX.match(str(v))},
        "voxel_mm": [float(x) for x in meta["voxel_mm"]] if meta.get("voxel_mm") is not None else None,
    }
    payload = {"format": FORMAT_VERSION, "meta": clean, "meshes": entries, "volume": vol_block, "labels": lab_b64}
    blob = json.dumps(payload, separators=(",", ":"))
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = _TEMPLATE.read_text(encoding="utf-8")
    for key, val in (("__TITLE__", html.escape(clean["title"])), ("__DISCLAIMER__", DISCLAIMER)):
        page = page.replace(key, val)
    page = page.replace("__PAYLOAD__", blob)  # last, so payload text is never re-scanned for placeholders
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path
