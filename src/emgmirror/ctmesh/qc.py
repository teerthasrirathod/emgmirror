"""Mesh quality gate.

``self_intersections`` and ``mesh_qc`` (with their helpers) are copied unchanged from
``emgmirror.ct.mesh`` (the old pipeline keeps its own copy). New here: surface distance in mm against the exact
voxel boundary of the label (HD95, mean, max; both directions), and :func:`gate`, the pass/fail decision.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
import trimesh
import vtk
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from vtk.util import numpy_support as ns

# --- copied from emgmirror/ct/mesh.py -------------------------------------------------------------------------

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


# --- new in ctmesh ---------------------------------------------------------------------------------------------

def voxel_boundary(mask: np.ndarray, index_to_lps: np.ndarray, offset_zyx=(0, 0, 0)) -> tuple[np.ndarray, np.ndarray]:
    """Exact boundary of a boolean [z, y, x] mask: one quad (two triangles) per face between a True voxel and a
    False one (outside the array counts as False). Returns (V, F) in LPS mm (vertices merged; orientation not
    guaranteed, so use it for unsigned distances only). ``offset_zyx`` is the index of ``mask[0, 0, 0]`` in the full grid."""
    m = np.pad(np.asarray(mask, bool), 1)
    Vs, Fs, nv = [], [], 0
    for ax in range(3):
        d = np.diff(m.astype(np.int8), axis=ax)
        for sign in (1, -1):  # +1: False -> True along +ax (outward normal is -ax); -1: True -> False (normal +ax)
            idx = np.argwhere(d == sign)
            if not len(idx):
                continue
            c = idx.astype(float)
            c[:, ax] += 0.5
            c -= 1.0
            o = [a for a in range(3) if a != ax]
            corners = []
            for du, dv in ((-.5, -.5), (.5, -.5), (.5, .5), (-.5, .5)):
                q = c.copy()
                q[:, o[0]] += du
                q[:, o[1]] += dv
                corners.append(q)
            Q = np.stack(corners, 1) + np.asarray(offset_zyx, float)  # (n, 4, 3) zyx index
            Q = Q[..., ::-1].reshape(-1, 3)  # xyz index
            base = nv + 4 * np.arange(len(idx))[:, None]
            tri = np.vstack([base + [0, 1, 2], base + [0, 2, 3]])
            Vs.append(Q)
            Fs.append(tri)
            nv += len(Q)
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    Vi = np.vstack(Vs)
    _, first, inv = np.unique(np.rint(Vi * 2).astype(np.int64), axis=0, return_index=True, return_inverse=True)
    V = Vi[first] @ index_to_lps[:3, :3].T + index_to_lps[:3, 3]
    return V, inv.ravel()[np.vstack(Fs)]


def label_boundary(labels: np.ndarray, lid: int, index_to_lps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Voxel boundary of one label of a [z, y, x] label map, computed on its bounding box only."""
    mask = labels == lid
    lo, hi = [], []
    for ax in range(3):
        idx = np.flatnonzero(mask.any(axis=tuple(a for a in range(3) if a != ax)))
        if not idx.size:
            return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
        lo.append(idx[0])
        hi.append(idx[-1] + 1)
    return voxel_boundary(mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]], index_to_lps, lo)


def _polydata(V: np.ndarray, F: np.ndarray) -> vtk.vtkPolyData:
    pd = vtk.vtkPolyData()
    pts = vtk.vtkPoints()
    pts.SetData(ns.numpy_to_vtk(np.ascontiguousarray(V, np.float64), deep=True))
    pd.SetPoints(pts)
    ca = vtk.vtkCellArray()
    ca.SetData(3, ns.numpy_to_vtkIdTypeArray(np.ascontiguousarray(F, np.int64).ravel(), deep=True))
    pd.SetPolys(ca)
    return pd


def distance_to_surface(points: np.ndarray, V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Exact unsigned distance from each point to the triangle surface (V, F) (VTK cell locator)."""
    f = vtk.vtkImplicitPolyDataDistance()
    f.SetInput(_polydata(V, F))
    out = vtk.vtkDoubleArray()
    f.FunctionValue(ns.numpy_to_vtk(np.ascontiguousarray(points, np.float64), deep=True), out)
    return np.abs(ns.vtk_to_numpy(out)).astype(float)


def surface_samples(V: np.ndarray, F: np.ndarray, spacing_mm: float, max_points: int = 2_000_000,
                    seed: int = 0) -> np.ndarray:
    """Vertices and triangle centroids, plus random points on triangles larger than half a voxel (about one per
    (spacing/2)^2 of area), so big decimated triangles are sampled as densely as small native ones."""
    V, F = np.asarray(V, float), np.asarray(F)
    if not len(F):
        return V
    T = V[F]
    area = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    extra = np.clip(np.floor(area / (0.5 * spacing_mm) ** 2).astype(np.int64) - 2, 0, None)
    pts = [V, T.mean(1)]
    n = int(min(extra.sum(), max_points))
    if n > 0:
        rng = np.random.default_rng(seed)
        fi = np.repeat(np.arange(len(F)), extra)
        if len(fi) > n:
            fi = rng.choice(fi, n, replace=False)
        r1, r2 = np.sqrt(rng.random(len(fi))), rng.random(len(fi))
        tri = T[fi]
        pts.append(tri[:, 0] * (1 - r1)[:, None] + tri[:, 1] * (r1 * (1 - r2))[:, None]
                   + tri[:, 2] * (r1 * r2)[:, None])
    return np.vstack(pts)


def surface_distance(Va, Fa, Vb, Fb, spacing_mm: float) -> dict:
    """Symmetric surface distance in mm between meshes a and b: HD95 (95th percentile of the pooled distances of
    both directions), mean, max, and each direction's HD95."""
    if not len(Fa) or not len(Fb):
        return {"hd95_mm": None, "mean_dist_mm": None, "hausdorff_mm": None, "hd95_a_to_b_mm": None,
                "hd95_b_to_a_mm": None}
    da = distance_to_surface(surface_samples(Va, Fa, spacing_mm), Vb, Fb)
    db = distance_to_surface(surface_samples(Vb, Fb, spacing_mm, seed=1), Va, Fa)
    d = np.concatenate([da, db])
    return {"hd95_mm": float(np.percentile(d, 95)), "mean_dist_mm": float(d.mean()), "hausdorff_mm": float(d.max()),
            "hd95_a_to_b_mm": float(np.percentile(da, 95)), "hd95_b_to_a_mm": float(np.percentile(db, 95))}


@dataclass
class GateLimits:
    hd95_voxels: float = 1.0  # HD95 against the label's voxel boundary, in units of voxel_mm
    web_hd95_voxels: float = 1.5  # web LOD: display geometry, looser distance limit
    volume_warn_pct: float = 5.0  # |volume change| above this is flagged (reported, not a failure)


def voxel_mm(spacing) -> float:
    """The voxel size used for the HD95 limit: geometric mean of the spacing (mm). Equal to the spacing on an
    isotropic grid; on a 0.8 x 0.8 x 2.5 mm grid it is 1.17 mm."""
    return float(np.prod(np.asarray(spacing, float)) ** (1 / 3))


def gate(qc: dict, spacing, limits: GateLimits = GateLimits(), web: bool = False) -> tuple[bool, list[str]]:
    """Pass/fail for one mesh. ``qc`` holds mesh_qc keys plus hd95_mm and volume_change_pct. ``web=True`` is the
    display gate: closed and within ``web_hd95_voxels``; self-intersections are reported but do not fail it."""
    why = []
    if not qc.get("watertight"):
        why.append("not watertight")
    if not qc.get("winding_consistent", True):
        why.append("inconsistent winding")
    if qc.get("self_intersections", 0) and not web:
        why.append(f"{qc['self_intersections']} self-intersecting triangle pairs")
    lim = (limits.web_hd95_voxels if web else limits.hd95_voxels) * voxel_mm(spacing)
    if qc.get("hd95_mm") is None or qc["hd95_mm"] > lim:
        why.append(f"HD95 {qc.get('hd95_mm')} mm > {lim:.2f} mm")
    return not why, why
