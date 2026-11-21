"""One decimation pass from the native-resolution surface into each level of detail, then repair.

Each LOD (``print``, ``web``) is simplified **directly from the native mesh** (never from another LOD), with
meshoptimizer's edge-collapse simplifier (MIT): it stops at the triangle budget or at an absolute geometric
error cap in mm, whichever comes first. Repair: merge duplicate vertices, drop degenerate faces, fix winding
(trimesh), then the result must be a 2-manifold that manifold3d accepts. If a LOD fails the QC gate (holes,
self-intersections or HD95) the pass is retried with half the error cap and twice the budget, up to
``retries`` times; if it still fails, the native mesh is used for that LOD and the fact is reported.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass
class LodSpec:
    name: str
    max_faces: int  # triangle budget for one structure
    error_voxels: float  # absolute error cap, in voxels (voxel_mm of the grid)


PRINT = LodSpec("print", 200_000, 0.25)
WEB = LodSpec("web", 30_000, 0.5)


def simplify(mesh: trimesh.Trimesh, target_faces: int, max_error_mm: float) -> trimesh.Trimesh:
    """meshoptimizer simplify to at most ``target_faces`` triangles, stopping early at ``max_error_mm``."""
    import meshoptimizer as mo
    if len(mesh.faces) <= target_faces:
        return mesh.copy()
    # simplify in a local frame (centred, float32) so float precision is uniform across the body
    c = mesh.vertices.mean(0)
    V = np.ascontiguousarray(mesh.vertices - c, np.float32)
    idx = np.ascontiguousarray(mesh.faces.ravel(), np.uint32)
    dst = np.zeros_like(idx)
    err = np.zeros(1, np.float32)
    n = mo.simplify(dst, idx, V, target_index_count=int(target_faces) * 3, target_error=float(max_error_mm),
                    options=mo.SIMPLIFY_ERROR_ABSOLUTE, result_error=err)
    F = dst[:n].reshape(-1, 3).astype(np.int64)
    out = trimesh.Trimesh(mesh.vertices.copy(), F, process=False)
    out.remove_unreferenced_vertices()
    return out


def repair(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, dict]:
    """Light, topology-preserving repair. Returns (mesh, report)."""
    m = mesh.copy()
    rep = {"merged_vertices": 0, "removed_faces": 0, "inverted": False, "manifold3d_ok": False}
    nv, nf = len(m.vertices), len(m.faces)
    m.merge_vertices(merge_tex=True, merge_norm=True)
    m.update_faces(m.nondegenerate_faces())
    # edge collapse can fold a patch into two coincident, opposite triangles (a zero-volume fin): drop both
    for _ in range(5):
        key = np.sort(m.faces, axis=1)
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        dup = cnt[inv.ravel()] > 1
        if not dup.any():
            break
        m.update_faces(~dup)
    m.remove_unreferenced_vertices()
    rep["merged_vertices"] = nv - len(m.vertices)
    rep["removed_faces"] = nf - len(m.faces)
    if not m.is_winding_consistent:
        trimesh.repair.fix_winding(m)
    if len(m.faces) and m.volume < 0:
        m.invert()
        rep["inverted"] = True
    rep["manifold3d_ok"] = manifold_ok(m)
    return m, rep


def manifold_ok(m: trimesh.Trimesh) -> bool:
    """True if manifold3d accepts the mesh as a closed oriented 2-manifold."""
    if not len(m.faces):
        return False
    try:
        import manifold3d as mf
        man = mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                  tri_verts=np.asarray(m.faces, np.uint32)))
        return man.status() == mf.Error.NoError and not man.is_empty()
    except Exception:  # pragma: no cover - manifold3d missing or rejects the input
        return False
