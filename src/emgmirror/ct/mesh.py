"""Label / mask to triangle mesh: marching cubes, Taubin smoothing, decimation, QC and export.

Meshes are ``trimesh.Trimesh`` in DICOM patient LPS mm with outward normals (positive volume).
Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import scipy.sparse as sp
import SimpleITK as sitk
import trimesh
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from skimage import measure

from . import DISCLAIMER


@dataclass
class MeshParams:
    smooth_iterations: int = 20  # Taubin iterations (0 = raw marching cubes)
    taubin_lambda: float = 0.5
    taubin_mu: float = -0.53
    max_faces: int | None = 300_000  # decimate above this (None = never)
    min_component_voxels: int = 0  # drop mesh islands smaller than this many voxels of volume


def _empty_info(voxel_count: int = 0, voxel_volume: float = 0.0) -> dict:
    return {"voxel_count": voxel_count, "voxel_volume_mm3": voxel_volume, "raw_volume_mm3": 0.0, "volume_mm3": 0.0,
            "volume_change_pct": 0.0, "smoothing_volume_change_pct": 0.0, "decimation_volume_change_pct": 0.0,
            "n_vertices": 0, "n_faces": 0, "smoothing_displacement_mean_mm": 0.0,
            "smoothing_displacement_max_mm": 0.0, "decimated": False, "timings_s": {}}


def _empty_mesh() -> trimesh.Trimesh:
    return trimesh.Trimesh(np.zeros((0, 3)), np.zeros((0, 3), np.int64), process=False)


def _bbox(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Inclusive (lo, hi) index bounds [z, y, x] of True voxels without a full-size nonzero()."""
    lo, hi = [], []
    for ax in range(3):
        other = tuple(a for a in range(3) if a != ax)
        idx = np.flatnonzero(mask.any(axis=other))
        if idx.size == 0:
            return None
        lo.append(idx[0])
        hi.append(idx[-1])
    return np.array(lo), np.array(hi)


def _drop_small_components(mesh: trimesh.Trimesh, min_volume: float) -> trimesh.Trimesh:
    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh
    keep = [p for p in parts if abs(p.volume) >= min_volume]
    if len(keep) == len(parts):
        return mesh
    return trimesh.util.concatenate(keep) if keep else _empty_mesh()


def _decimate(mesh: trimesh.Trimesh, target: int) -> trimesh.Trimesh:
    try:
        import fast_simplification
    except ImportError as e:  # pragma: no cover
        raise ImportError("decimation needs the 'fast-simplification' package (or set max_faces=None)") from e
    v, f = fast_simplification.simplify(np.ascontiguousarray(mesh.vertices, np.float32),
                                        np.ascontiguousarray(mesh.faces, np.int32), target_count=int(target))
    out = trimesh.Trimesh(np.asarray(v, np.float64), np.asarray(f, np.int64), process=False)
    out.update_faces(out.nondegenerate_faces())
    out.remove_unreferenced_vertices()
    return out


def mask_to_mesh(mask: np.ndarray, image: sitk.Image, params: MeshParams = MeshParams()) -> tuple[trimesh.Trimesh, dict]:
    """Mesh a boolean [z, y, x] mask on the grid of ``image`` (only its geometry is used)."""
    t0 = time.perf_counter()
    spacing = np.array(image.GetSpacing(), float)
    voxel_mm3 = float(np.prod(spacing))
    box = _bbox(mask)
    if box is None:
        return _empty_mesh(), _empty_info()
    shape = np.array(mask.shape)
    lo = np.maximum(box[0] - 1, 0)
    hi = np.minimum(box[1] + 2, shape)
    crop = mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    n_vox = int(np.count_nonzero(crop))
    vol = np.pad(crop.astype(np.float32), 1)  # zero border: surfaces at the crop edge are closed
    offset = lo - 1  # [z, y, x] index of vol[0, 0, 0]
    info = _empty_info(n_vox, n_vox * voxel_mm3)
    tm = info["timings_s"]
    tm["crop"] = time.perf_counter() - t0

    t = time.perf_counter()
    verts, faces, _, _ = measure.marching_cubes(vol, level=0.5)
    tm["marching_cubes"] = time.perf_counter() - t
    idx_xyz = verts[:, ::-1] + offset[::-1]  # continuous index, x y z order
    D = np.array(image.GetDirection(), float).reshape(3, 3)
    phys = np.array(image.GetOrigin(), float) + (idx_xyz * spacing) @ D.T
    mesh = trimesh.Trimesh(phys, faces, process=False)
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    if mesh.volume < 0:
        mesh.invert()
    if params.min_component_voxels > 0:
        mesh = _drop_small_components(mesh, params.min_component_voxels * voxel_mm3)
    raw_vol = float(mesh.volume)
    info["raw_volume_mm3"] = raw_vol

    verts_s = mesh.vertices
    if params.smooth_iterations > 0 and len(mesh.faces):
        t = time.perf_counter()
        verts_s = taubin_smooth(mesh.vertices, mesh.faces, params.smooth_iterations, params.taubin_lambda,
                                params.taubin_mu)
        d = np.linalg.norm(verts_s - mesh.vertices, axis=1)
        info["smoothing_displacement_mean_mm"] = float(d.mean())
        info["smoothing_displacement_max_mm"] = float(d.max())
        mesh = trimesh.Trimesh(verts_s, mesh.faces, process=False)
        tm["smoothing"] = time.perf_counter() - t
    smooth_vol = float(mesh.volume)
    info["smoothing_volume_change_pct"] = (smooth_vol - raw_vol) / raw_vol * 100 if raw_vol else 0.0

    if params.max_faces is not None and len(mesh.faces) > params.max_faces:
        t = time.perf_counter()
        mesh = _decimate(mesh, params.max_faces)
        if mesh.volume < 0:
            mesh.invert()
        info["decimated"] = True
        tm["decimation"] = time.perf_counter() - t
    final_vol = float(mesh.volume)
    info["volume_mm3"] = final_vol
    info["decimation_volume_change_pct"] = (final_vol - smooth_vol) / smooth_vol * 100 if smooth_vol else 0.0
    info["volume_change_pct"] = (final_vol - raw_vol) / raw_vol * 100 if raw_vol else 0.0
    info["n_vertices"], info["n_faces"] = len(mesh.vertices), len(mesh.faces)
    tm["total"] = time.perf_counter() - t0
    return mesh, info


def labels_to_mesh(labels: sitk.Image, ids: Iterable[int], params: MeshParams = MeshParams()
                   ) -> tuple[trimesh.Trimesh, dict]:
    """Mesh the union of the given label ids."""
    arr = sitk.GetArrayViewFromImage(labels)
    mask = np.isin(arr, np.asarray(list(ids), dtype=arr.dtype))
    return mask_to_mesh(mask, labels, params)


def _umbrella(faces: np.ndarray, n: int) -> sp.csr_matrix:
    """Row-normalised uniform adjacency (each vertex averages its edge neighbours)."""
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    e = np.concatenate([e, e[:, ::-1]])
    A = sp.coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)).tocsr()
    A.data[:] = 1.0  # collapse duplicate edges
    deg = np.asarray(A.sum(axis=1)).ravel()
    return sp.diags(1.0 / np.maximum(deg, 1)) @ A


def taubin_smooth(vertices: np.ndarray, faces: np.ndarray, iterations: int, lam: float = 0.5,
                  mu: float = -0.53) -> np.ndarray:
    """Taubin lambda|mu smoothing with umbrella weights. Each iteration = a shrinking (lam > 0) step followed by an
    inflating (mu < 0) step; mu = 0 degenerates to plain Laplacian smoothing."""
    v = np.array(vertices, float)
    if iterations <= 0 or len(faces) == 0:
        return v
    W = _umbrella(np.asarray(faces), len(v))
    for _ in range(iterations):
        v += lam * (W @ v - v)
        if mu:
            v += mu * (W @ v - v)
    return v


def _seg_tri_hit(p0: np.ndarray, p1: np.ndarray, tri: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Segments p0->p1 (n,3) against triangles (n,3,3): touching counts (Moller-Trumbore, t in [0, 1])."""
    d = p1 - p0
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    h = np.cross(d, e2)
    a = np.einsum("ij,ij->i", e1, h)
    scale = np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1) * np.linalg.norm(d, axis=1)
    ok = np.abs(a) > 1e-12 * np.maximum(scale, 1e-300)
    f = 1.0 / np.where(ok, a, 1.0)
    s = p0 - tri[:, 0]
    u = f * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    v = f * np.einsum("ij,ij->i", d, q)
    t = f * np.einsum("ij,ij->i", e2, q)
    return ok & (u >= -eps) & (v >= -eps) & (u + v <= 1 + eps) & (t >= -eps) & (t <= 1 + eps)


def _tri_tri_hit(P: np.ndarray, Q: np.ndarray) -> np.ndarray:
    """Triangle pairs (n,3,3) x (n,3,3): True if an edge of one pierces the other (coplanar overlaps not detected)."""
    hit = np.zeros(len(P), bool)
    for k in range(3):
        hit |= _seg_tri_hit(P[:, k], P[:, (k + 1) % 3], Q)
        hit |= _seg_tri_hit(Q[:, k], Q[:, (k + 1) % 3], P)
    return hit


def self_intersections(mesh: trimesh.Trimesh, max_pairs: int = 1000, chunk_pairs: int = 2_000_000) -> int:
    """Count triangle pairs that intersect without sharing a vertex (capped at ``max_pairs``).

    Broad phase: uniform grid hash over triangle bounding boxes; a pair is evaluated only in the cell that holds
    the minimum corner of the two boxes' overlap, so no de-duplication is needed. Narrow phase: vectorised
    edge-vs-triangle tests in numpy. Coplanar overlapping triangles are not counted.
    """
    V, F = np.asarray(mesh.vertices, float), np.asarray(mesh.faces)
    nf = len(F)
    if nf < 2:
        return 0
    T = V[F]
    bmin, bmax = T.min(axis=1), T.max(axis=1)
    cell = max(2.0 * float(np.median((bmax - bmin).max(axis=1))), 1e-9)
    org = bmin.min(axis=0)
    c0 = np.floor((bmin - org) / cell).astype(np.int64)
    c1 = np.floor((bmax - org) / cell).astype(np.int64)
    dims = c1.max(axis=0) + 1
    span = c1 - c0 + 1
    n_cells = span.prod(axis=1)
    tri = np.repeat(np.arange(nf), n_cells)
    local = np.arange(len(tri)) - np.repeat(np.cumsum(n_cells) - n_cells, n_cells)
    sx, sy = span[tri, 0], span[tri, 1]
    cc = c0[tri] + np.stack([local % sx, (local // sx) % sy, local // (sx * sy)], axis=1)
    key = (cc[:, 0] * dims[1] + cc[:, 1]) * dims[2] + cc[:, 2]
    order = np.argsort(key, kind="stable")
    key, tri = key[order], tri[order]
    _, first, counts = np.unique(key, return_index=True, return_counts=True)
    end = np.repeat(first + counts, counts)
    nxt = end - np.arange(len(key)) - 1  # partners after each entry within its cell
    cum = np.cumsum(nxt)
    total = 0
    start = 0
    while start < len(key) and total < max_pairs:
        base = cum[start - 1] if start else 0
        stop = int(np.searchsorted(cum, base + chunk_pairs, side="right"))
        stop = min(max(stop, start + 1), len(key))
        cnt = nxt[start:stop]
        n = int(cnt.sum())
        if n:
            ii = np.repeat(np.arange(start, stop), cnt)
            off = np.arange(n) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            jj = ii + 1 + off
            a, b = tri[ii], tri[jj]
            m = (bmin[a] <= bmax[b]).all(axis=1) & (bmin[b] <= bmax[a]).all(axis=1)
            a, b, ii = a[m], b[m], ii[m]
            m = ~(F[a][:, :, None] == F[b][:, None, :]).any(axis=(1, 2))
            a, b, ii = a[m], b[m], ii[m]
            corner = np.floor((np.maximum(bmin[a], bmin[b]) - org) / cell).astype(np.int64)
            ck = (corner[:, 0] * dims[1] + corner[:, 1]) * dims[2] + corner[:, 2]
            m = ck == key[ii]
            a, b = a[m], b[m]
            if len(a):
                total += int(_tri_tri_hit(T[a], T[b]).sum())
        start = stop
    return min(total, max_pairs)


def mesh_qc(mesh: trimesh.Trimesh, voxel_volume_mm3: float | None = None, raw: trimesh.Trimesh | None = None) -> dict:
    """Quality report. ``hausdorff_to_raw_mm`` is one-sided (smoothed vertices -> raw surface), approximated with a
    cKDTree on a dense area-weighted sampling of the raw surface plus its vertices (slight overestimate)."""
    if len(mesh.faces) == 0:
        return {"watertight": False, "winding_consistent": False, "euler_number": 0, "n_components": 0,
                "n_components_edge": 0,
                "self_intersections": 0, "volume_mm3": 0.0, "area_mm2": 0.0, "bbox_mm": [[0.0] * 3, [0.0] * 3],
                "extent_mm": [0.0] * 3, "volume_vs_voxels_pct": None, "hausdorff_to_raw_mm": None}
    f = mesh.faces
    used = np.unique(f)
    g = sp.coo_matrix((np.ones(f.size), (np.repeat(f[:, 0], 3), f.ravel())), shape=(len(mesh.vertices),) * 2)
    g = g + sp.coo_matrix((np.ones(len(f)), (f[:, 1], f[:, 2])), shape=g.shape)
    n_comp = len(np.unique(connected_components(g, directed=False)[1][used]))
    # Pieces joined only through a single shared vertex count as separate here. Faces are linked through every
    # edge they share, also an edge used by more than two faces (trimesh's face_adjacency drops those).
    e = np.sort(f[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _, eid = np.unique(e, axis=0, return_inverse=True)
    fe = sp.coo_matrix((np.ones(len(eid)), (np.repeat(np.arange(len(f)), 3), eid.ravel())),
                       shape=(len(f), int(eid.max()) + 1)).tocsr()
    n_edge = int(connected_components(fe @ fe.T, directed=False)[0])
    lo, hi = mesh.bounds
    vol = float(mesh.volume)
    haus = None
    if raw is not None and len(raw.faces):
        pts = np.vstack([raw.vertices, raw.sample(min(3_000_000, 4 * len(raw.faces)))])
        haus = float(cKDTree(pts).query(mesh.vertices)[0].max())
    return {"watertight": bool(mesh.is_watertight), "winding_consistent": bool(mesh.is_winding_consistent),
            "euler_number": int(mesh.euler_number), "n_components": n_comp, "n_components_edge": n_edge,
            "self_intersections": self_intersections(mesh), "volume_mm3": vol, "area_mm2": float(mesh.area),
            "bbox_mm": [lo.tolist(), hi.tolist()], "extent_mm": (hi - lo).tolist(),
            "volume_vs_voxels_pct": (vol - voxel_volume_mm3) / voxel_volume_mm3 * 100 if voxel_volume_mm3 else None,
            "hausdorff_to_raw_mm": haus}


def _rgba(hexcolor: str) -> list[int]:
    h = hexcolor.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [255]


FILE_NOTE = f"{DISCLAIMER} Units mm, patient LPS."  # fits the 80-byte STL header


def _stl_bytes(m: trimesh.Trimesh) -> bytes:
    """Binary STL with the disclaimer in the 80-byte header (it must not start with 'solid', which marks ASCII STL)."""
    data = bytearray(trimesh.exchange.stl.export_stl(m))
    data[:80] = FILE_NOTE.encode("ascii")[:80].ljust(80, b" ")
    return bytes(data)


def _ply_bytes(m: trimesh.Trimesh) -> bytes:
    """Binary PLY with the disclaimer as a header comment line."""
    data = trimesh.exchange.ply.export_ply(m, encoding="binary")
    end = data.index(b"end_header")
    head, rest = data[:end], data[end:]
    lines = head.split(b"\n")
    lines.insert(2, b"comment " + FILE_NOTE.encode("ascii"))  # after 'ply' and 'format ...'
    return b"\n".join(lines) + rest


def _glb_with_note(glb: bytes) -> bytes:
    """Add the disclaimer to the glTF asset block (asset.extras) and rewrite the JSON chunk with valid padding."""
    import json
    import struct
    n = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:20 + n])
    doc.setdefault("asset", {}).setdefault("extras", {})["disclaimer"] = DISCLAIMER
    doc["asset"]["extras"]["units"] = "mm, patient LPS"
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    rest = glb[20 + n:]
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + len(rest)) + struct.pack("<I4s", len(js), b"JSON") + js + rest
    return out


def export_meshes(meshes: dict[str, trimesh.Trimesh], out_dir: Path, colors: dict[str, str] | None = None) -> dict:
    """Write STL / OBJ / PLY per mesh plus combined.glb and README.txt. Coordinates stay in patient LPS mm.

    Every file carries the research-use disclaimer in its own header field (STL header, OBJ and PLY comment,
    glTF asset extras), so the label stays with a mesh that is copied out of the folder."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    colors = colors or {}
    res: dict = {}
    scene = trimesh.Scene()
    for name, m in meshes.items():
        stem = re.sub(r"[^\w.\-]", "_", name)
        paths = {}
        for ext in ("stl", "obj", "ply"):
            p = out_dir / f"{stem}.{ext}"
            if ext == "ply":
                p.write_bytes(_ply_bytes(m))
            elif ext == "stl":
                p.write_bytes(_stl_bytes(m))
            else:
                p.write_bytes(trimesh.exchange.obj.export_obj(m, header=FILE_NOTE).encode("utf-8"))
            paths[ext] = str(p)
        res[name] = paths
        g = trimesh.Trimesh(np.asarray(m.vertices), np.asarray(m.faces), process=False)
        g.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            name=name, baseColorFactor=_rgba(colors.get(name, "#a0a0a0")), metallicFactor=0.0, roughnessFactor=0.8))
        scene.add_geometry(g, node_name=name, geom_name=name)
    glb = out_dir / "combined.glb"
    scene.metadata["disclaimer"] = DISCLAIMER
    glb.write_bytes(_glb_with_note(scene.export(file_type="glb")))
    res["combined_glb"] = str(glb)
    (out_dir / "README.txt").write_text(
        "Units: millimetres (mm).\nCoordinate frame: DICOM patient LPS (x to patient left, y posterior, z head).\n"
        "Coordinates are identical in STL, OBJ, PLY and GLB (no rescaling or reorientation).\n"
        f"{DISCLAIMER}\n", encoding="utf-8")
    return res
