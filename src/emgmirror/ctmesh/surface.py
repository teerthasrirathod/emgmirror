"""Label map → one closed surface per label, at native resolution, in patient LPS mm.

Primary path (all labels at once):

1. ``vtkSurfaceNets3D`` (VTK >= 9.5) on the cropped multi-label map, padded with background so labels cut by
   the scan edge are closed (and flagged), output as quads. Its own smoother is off by default: with it on,
   every setting we tried either left visible terraces or shrank thin structures by 10-70 %
   (docs/ct_platform/results/S2_mesh.md, ``scripts/ctmesh_tune.py``). Unsmoothed SurfaceNets output is the
   exact voxel boundary, with vertices *shared* between touching labels (coincident duplicates are merged on the
   voxel-corner lattice).
2. Per label: the quads bordering that label, oriented outward. Where two voxels of the label touch only along
   an edge or at a corner the boundary is non-manifold; such corners are split so each 6-connected piece gets
   its own vertex (the convention marching cubes uses), giving a closed 2-manifold.
3. Windowed-sinc smoothing (``vtkWindowedSincPolyDataFilter``, pass band 0.1, 50 iterations, non-shrinking) of
   each label's own surface, then a box constraint: no vertex moves more than ``constraint_voxels`` (0.45) voxel
   along any grid axis from its voxel-boundary position. Below half a voxel the two faces of a one-voxel-thick
   feature cannot cross. Each quad is then split along its shorter diagonal. Touching labels start from the
   same interface vertices (one SurfaceNets run), so after smoothing their sides stay within 0.9 voxel of each
   other. Smoothing all labels as one shared mesh (``shared_smoothing=True``) keeps interfaces identical but
   pins the surface along every curve where three labels meet: on 108 structures of a public CT the median
   dihedral angle was 11.2 deg shared vs 8.3 deg per label, median volume change 0.98 % vs 0.13 %, same HD95.

Fallback (one label at a time): ``vtkDiscreteFlyingEdges3D`` + windowed-sinc smoothing with the same box
constraint. Used for thin labels (no voxel deeper than ``thin_depth_voxels`` from the background) when it
gives the better surface, and for any label whose primary surface fails the QC gate (see pipeline).

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import trimesh
import vtk
from scipy import ndimage
from vtk.util import numpy_support as ns

from .contract import LabelMap


@dataclass
class SurfaceParams:
    smoothing_iterations: int = 50  # windowed-sinc iterations (0 = keep the voxel boundary)
    passband: float = 0.1  # windowed-sinc pass band (lower = smoother)
    constraint_voxels: float = 0.45  # max displacement per grid axis, in voxels (< 0.5: faces can't cross)
    sn_smoothing_iterations: int = 0  # VTK's built-in SurfaceNets smoother (0 = off, see module doc)
    sn_constraint_distance_mm: float | None = None  # only used when sn_smoothing_iterations > 0
    thin_depth_voxels: float = 1.0  # max distance-to-background (voxels) at or below which a label is "thin"
    fallback_iterations: int = 30
    fallback_passband: float = 0.1
    split_nudge_voxels: float = 0.05  # how far split copies of a pinched vertex are pulled apart
    shared_smoothing: bool = False  # smooth all labels as one mesh (rougher at multi-label junctions; see doc)


@dataclass
class Surface:
    label_id: int
    mesh: trimesh.Trimesh  # LPS mm, outward normals
    method: str  # "surfacenets" | "flyingedges"
    voxel_count: int = 0
    voxel_volume_mm3: float = 0.0
    cut_by_scan_edge: bool = False
    thin: bool = False
    split_vertices: int = 0  # vertices duplicated to make the surface manifold
    max_displacement_mm: float = 0.0
    bridged_voxels: int = 0  # voxels added by well_composed() (fallback only)
    timings_s: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------------

def _bbox(mask: np.ndarray):
    lo, hi = [], []
    for ax in range(3):
        other = tuple(a for a in range(3) if a != ax)
        idx = np.flatnonzero(mask.any(axis=other))
        if idx.size == 0:
            return None
        lo.append(int(idx[0]))
        hi.append(int(idx[-1]) + 1)
    return np.array(lo), np.array(hi)


def _vtk_image(arr_zyx: np.ndarray, spacing) -> vtk.vtkImageData:
    nz, ny, nx = arr_zyx.shape
    img = vtk.vtkImageData()
    img.SetDimensions(nx, ny, nz)
    img.SetSpacing(*[float(s) for s in spacing])
    vt = vtk.VTK_UNSIGNED_SHORT if arr_zyx.dtype == np.uint16 else vtk.VTK_UNSIGNED_CHAR
    img.GetPointData().SetScalars(ns.numpy_to_vtk(np.ascontiguousarray(arr_zyx).ravel(), deep=True, array_type=vt))
    return img


def _poly_arrays(pd: vtk.vtkPolyData, n: int = 3):
    if pd.GetNumberOfPoints() == 0 or pd.GetNumberOfCells() == 0:
        return np.zeros((0, 3)), np.zeros((0, n), np.int64)
    pts = ns.vtk_to_numpy(pd.GetPoints().GetData()).astype(np.float64)
    polys = ns.vtk_to_numpy(pd.GetPolys().GetConnectivityArray()).astype(np.int64).reshape(-1, n)
    return pts, polys


def _sn_quads(crop: np.ndarray, spacing, present, iterations: int = 0, constraint_mm: float | None = None):
    """vtkSurfaceNets3D as quads (topology independent of smoothing). Returns (points, quads, two-label scalars
    per quad)."""
    sn = vtk.vtkSurfaceNets3D()
    sn.SetInputData(_vtk_image(crop, spacing))
    for k, v in enumerate(present):
        sn.SetValue(k, v)
    sn.SetBackgroundLabel(0)
    sn.SetOutputMeshTypeToQuads()
    sn.SetNumberOfIterations(int(iterations))
    if iterations and constraint_mm is not None:
        sn.SetAutomaticSmoothingConstraints(False)
        sn.SetConstraintDistance(float(constraint_mm))
    sn.Update()
    V, Q = _poly_arrays(sn.GetOutput(), 4)
    if not len(Q):
        return V, np.zeros((0, 4), np.int64), np.zeros((0, 2), np.int64)
    sc = ns.vtk_to_numpy(sn.GetOutput().GetCellData().GetScalars()).astype(np.int64).reshape(-1, 2)
    return V, Q, sc


def _to_poly(V, F) -> vtk.vtkPolyData:
    pd = vtk.vtkPolyData()
    p = vtk.vtkPoints()
    p.SetData(ns.numpy_to_vtk(np.ascontiguousarray(V, np.float64), deep=True))
    pd.SetPoints(p)
    ca = vtk.vtkCellArray()
    ca.SetData(F.shape[1], ns.numpy_to_vtkIdTypeArray(np.ascontiguousarray(F, np.int64).ravel(), deep=True))
    pd.SetPolys(ca)
    return pd


def windowed_sinc(V: np.ndarray, F: np.ndarray, iterations: int, passband: float,
                  non_manifold: bool = True) -> np.ndarray:
    """Non-shrinking low-pass smoothing; returns new vertex positions (same order)."""
    if iterations <= 0 or not len(F):
        return np.array(V, float)
    w = vtk.vtkWindowedSincPolyDataFilter()
    w.SetInputData(_to_poly(V, F))
    w.SetNumberOfIterations(int(iterations))
    w.SetPassBand(float(passband))
    w.NormalizeCoordinatesOn()
    w.BoundarySmoothingOff()
    w.FeatureEdgeSmoothingOff()
    w.SetNonManifoldSmoothing(bool(non_manifold))
    w.Update()
    out = ns.vtk_to_numpy(w.GetOutput().GetPoints().GetData()).astype(np.float64)
    return out


def _split_quads(V: np.ndarray, Q: np.ndarray) -> np.ndarray:
    """Split each quad along its shorter diagonal (decided on the final, smoothed positions)."""
    d02 = np.linalg.norm(V[Q[:, 0]] - V[Q[:, 2]], axis=1)
    d13 = np.linalg.norm(V[Q[:, 1]] - V[Q[:, 3]], axis=1)
    a = (d02 <= d13)[:, None]
    return np.vstack([np.where(a, Q[:, [0, 1, 2]], Q[:, [0, 1, 3]]), np.where(a, Q[:, [0, 2, 3]], Q[:, [1, 2, 3]])])


def _clamp(V0: np.ndarray, V1: np.ndarray, spacing, c: float) -> np.ndarray:
    lim = c * np.asarray(spacing, float)
    return V0 + np.clip(V1 - V0, -lim, lim)


def _component_lut() -> np.ndarray:
    """LUT[pattern, bit] = smallest bit in the 6-connected component (inside a 2x2x2 block) that holds ``bit``.
    Bit b = dx + 2 dy + 4 dz."""
    lut = np.zeros((256, 8), np.int8)
    for pat in range(256):
        for b in range(8):
            if not pat >> b & 1:
                lut[pat, b] = -1
                continue
            seen, stack = {b}, [b]
            while stack:
                u = stack.pop()
                for k in (1, 2, 4):
                    w = u ^ k
                    if pat >> w & 1 and w not in seen:
                        seen.add(w)
                        stack.append(w)
            lut[pat, b] = min(seen)
    return lut


_LUT = _component_lut()


def _label_faces(F: np.ndarray, sc: np.ndarray, lid: int) -> np.ndarray:
    return np.vstack([F[sc[:, 0] == lid], F[sc[:, 1] == lid][:, ::-1]])


def _manifold_split(f: np.ndarray, idx_half: np.ndarray, V_smooth: np.ndarray, crop: np.ndarray, lid: int,
                    spacing, nudge: float) -> tuple[np.ndarray, np.ndarray, int]:
    """Split pinched corners of one label's voxel-boundary faces (see module doc). ``idx_half`` holds the raw
    (unsmoothed) vertex positions in crop index units (x, y, z), which sit at voxel corners (half-integers).
    Returns (V, F, n_split) with V in the smoothed vtk-world frame."""
    T = idx_half[f]  # (n, k, 3) xyz, k = 3 or 4
    same = np.all(np.isclose(T, T[:, :1]), axis=1)  # axis on which the face is flat
    ax = np.argmax(same, axis=1)
    lo, hi = T.min(1), T.max(1)
    centre = (lo + hi) / 2
    nrm = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    sign = np.sign(nrm[np.arange(len(f)), ax])
    owner = centre.copy()
    owner[np.arange(len(f)), ax] -= 0.5 * sign  # outward normal → owning voxel is behind the face
    owner = np.rint(owner).astype(np.int64)  # (n, 3) xyz voxel index in crop
    corner = np.rint(T + 0.5).astype(np.int64)  # (n, k, 3) corner c: voxels c-1 .. c
    mask = crop == lid
    nz, ny, nx = mask.shape
    pat = np.zeros(corner.shape[:2], np.int64)
    for b in range(8):
        dx, dy, dz = b & 1, b >> 1 & 1, b >> 2 & 1
        x = corner[..., 0] - 1 + dx
        y = corner[..., 1] - 1 + dy
        z = corner[..., 2] - 1 + dz
        ok = (x >= 0) & (x < nx) & (y >= 0) & (y < ny) & (z >= 0) & (z < nz)
        val = np.zeros(x.shape, bool)
        val[ok] = mask[z[ok], y[ok], x[ok]]
        pat |= val.astype(np.int64) << b
    ob = owner[:, None, :] - (corner - 1)  # (n, k, 3) offset of owner within each corner's block, in {0,1}
    obit = ob[..., 0] + 2 * ob[..., 1] + 4 * ob[..., 2]
    rep = _LUT[pat, obit].astype(np.int64)
    if (rep < 0).any():  # owner not found in its own block: inconsistent input, leave untouched
        return V_smooth, f, 0
    key = f * 8 + rep
    uk, inv = np.unique(key.ravel(), return_inverse=True)
    old = uk // 8
    newF = inv.reshape(f.shape)
    V = V_smooth[old].copy()
    counts = np.bincount(old)
    multi = counts[old] > 1
    n_split = int(np.count_nonzero(multi))
    if n_split and nudge > 0:
        # pull each copy towards the centre of its own voxel component (in crop index units → world)
        cb = corner.reshape(-1, 3)
        rr = rep.ravel()
        comp_c = np.zeros((len(uk), 3))
        first = np.zeros(len(uk), np.int64)
        first[inv] = np.arange(len(inv))
        for j in np.flatnonzero(multi):
            i = first[j]
            p = int(pat.ravel()[i])
            members = [b for b in range(8) if p >> b & 1 and _LUT[p, b] == rr[i]]
            off = np.mean([[b & 1, b >> 1 & 1, b >> 2 & 1] for b in members], axis=0) - 0.5
            comp_c[j] = off
        _ = cb
        V[multi] += nudge * comp_c[multi] * np.asarray(spacing, float)
    return V, newF, n_split


def _finish(V_world: np.ndarray, F: np.ndarray, M: np.ndarray, lo_xyz: np.ndarray, spacing) -> trimesh.Trimesh:
    """vtk-world (crop index * spacing) → LPS mm."""
    idx = V_world / np.asarray(spacing, float) + lo_xyz
    P = idx @ M[:3, :3].T + M[:3, 3]
    m = trimesh.Trimesh(P, F, process=False)
    m.remove_unreferenced_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.remove_unreferenced_vertices()
    if len(m.faces) and m.volume < 0:
        m.invert()
    return m


def _edge_labels(arr: np.ndarray) -> set[int]:
    s = set()
    for sl in (arr[0], arr[-1], arr[:, 0], arr[:, -1], arr[:, :, 0], arr[:, :, -1]):
        s |= set(np.unique(sl).tolist())
    return s - {0}


def label_depth_voxels(mask: np.ndarray) -> float:
    """Largest distance (in voxels, along the index grid) from a label voxel to the nearest non-label voxel."""
    if not mask.any():
        return 0.0
    return float(ndimage.distance_transform_edt(np.pad(mask, 1)).max())


def surfacenets_all(lm: LabelMap, ids, params: SurfaceParams = SurfaceParams()) -> dict[int, Surface]:
    """Primary path: one SurfaceNets run over all ``ids``; returns one closed surface per present label."""
    t0 = time.perf_counter()
    ids = [int(i) for i in ids]
    arr = lm.array
    sel = np.isin(arr, np.asarray(ids, arr.dtype))
    box = _bbox(sel)
    if box is None:
        return {}
    lo, hi = box
    crop = np.where(sel[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]], arr[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]], 0)
    crop = np.pad(crop.astype(np.uint16), 1)
    lo_xyz = (lo - 1)[::-1].astype(float)
    on_edge = _edge_labels(np.where(sel, arr, 0))
    present = [i for i in ids if (crop == i).any()]
    spacing = np.asarray(lm.spacing, float)

    V0, F, sc = _sn_quads(crop, spacing, present)  # F: quads until smoothing is done
    if not len(F):
        return {}
    # SurfaceNets can emit several coincident points per cell; unsmoothed points sit exactly on the voxel-corner
    # lattice, so merge them by lattice key to get one shared vertex per corner.
    key = np.rint(V0 / spacing * 2).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    F_raw = F
    V0, F = V0[first], inv.ravel()[F]
    srt = np.sort(F, axis=1)
    keep = (srt[:, 1:] != srt[:, :-1]).all(axis=1)  # drop quads that collapsed in the merge
    F, sc = F[keep], sc[keep]
    t_sn = time.perf_counter() - t0
    idx_half = V0 / spacing

    t = time.perf_counter()
    if params.sn_smoothing_iterations > 0:  # optional: VTK's own constrained smoother, for comparison
        V1, F1, _ = _sn_quads(crop, spacing, present, params.sn_smoothing_iterations,
                              params.sn_constraint_distance_mm)
        if F1.shape != F_raw.shape or not np.array_equal(F1, F_raw):  # same point order as the first run
            raise RuntimeError("SurfaceNets smoothing changed the topology")
        V0s = V1[first]
    else:
        V0s = V0
    shared = params.shared_smoothing and params.smoothing_iterations > 0
    if shared:  # smooth the whole multi-label mesh at once (kept for comparison, see module doc)
        V0s = _clamp(V0, windowed_sinc(V0s, F, params.smoothing_iterations, params.passband, non_manifold=True),
                     spacing, params.constraint_voxels)
    t_smooth = time.perf_counter() - t

    M = lm.index_to_lps
    out = {}
    counts = np.bincount(crop.ravel(), minlength=max(present) + 1)
    for lid in present:
        t = time.perf_counter()
        q = _label_faces(F, sc, lid)  # quads, oriented outward
        V, q2, n_split = _manifold_split(q, idx_half, V0s, crop, lid, spacing, params.split_nudge_voxels)
        Vr = V.copy()  # positions before per-label smoothing (voxel corners, or shared-smoothed)
        if params.smoothing_iterations > 0 and not shared:
            used = np.unique(q2)
            loc = np.full(len(V), -1)
            loc[used] = np.arange(len(used))
            Vl = windowed_sinc(V[used], loc[q2], params.smoothing_iterations, params.passband, non_manifold=False)
            V = V.copy()
            V[used] = _clamp(Vr[used], Vl, spacing, params.constraint_voxels)
        tri = _split_quads(V, q2)
        mesh = _finish(V, tri, M, lo_xyz, spacing)
        u = np.unique(q2)
        disp = np.linalg.norm(V[u] - V0[np.minimum(u, len(V0) - 1)], axis=1) if not n_split else \
            np.linalg.norm(V[u] - Vr[u], axis=1)
        s = Surface(lid, mesh, "surfacenets", int(counts[lid]), int(counts[lid]) * lm.voxel_volume_mm3,
                    lid in on_edge, False, n_split, float(disp.max()) if len(disp) else 0.0)
        s.timings_s = {"surfacenets_shared": t_sn, "smoothing_shared": t_smooth,
                       "label": time.perf_counter() - t}
        out[lid] = s
    return out


def well_composed(mask: np.ndarray, max_rounds: int = 20) -> tuple[np.ndarray, int]:
    """Add the fewest voxels that remove edge-only and corner-only contacts (2x2 diagonal patterns in any axis
    plane, 2x2x2 blocks whose set voxels touch only at the centre corner). Such configurations make every
    surface of the label non-manifold. Returns (new mask, number of voxels added)."""
    m = np.pad(np.asarray(mask, bool), 1)
    added = 0
    for _ in range(max_rounds):
        fix = np.zeros_like(m)
        for a, b in ((0, 1), (0, 2), (1, 2)):
            sl = [slice(None)] * 3

            def sh(da, db):
                s_ = list(sl)
                s_[a] = slice(da, m.shape[a] - 1 + da)
                s_[b] = slice(db, m.shape[b] - 1 + db)
                return tuple(s_)
            p00, p11, p01, p10 = m[sh(0, 0)], m[sh(1, 1)], m[sh(0, 1)], m[sh(1, 0)]
            d1 = p00 & p11 & ~p01 & ~p10
            d2 = p01 & p10 & ~p00 & ~p11
            fix[sh(0, 1)] |= d1  # bridge each diagonal pair with one voxel
            fix[sh(0, 0)] |= d2
        c = [m[i:m.shape[0] - 1 + i, j:m.shape[1] - 1 + j, k:m.shape[2] - 1 + k]
             for i in (0, 1) for j in (0, 1) for k in (0, 1)]
        idx = {(i, j, k): n for n, (i, j, k) in enumerate((i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1))}
        for (i, j, k), n in idx.items():  # corner-only pairs: (i,j,k) and its opposite, nothing else set
            if (i, j, k) > (1 - i, 1 - j, 1 - k):
                continue
            o = idx[(1 - i, 1 - j, 1 - k)]
            others = np.zeros_like(c[0])
            for q, cc in enumerate(c):
                if q not in (n, o):
                    others |= cc
            bad = c[n] & c[o] & ~others
            # bridge with the voxel next to (i,j,k) along the first axis
            t = (1 - i, j, k)
            fix[t[0]:m.shape[0] - 1 + t[0], t[1]:m.shape[1] - 1 + t[1], t[2]:m.shape[2] - 1 + t[2]] |= bad
        new = fix & ~m
        if not new.any():
            break
        added += int(new.sum())
        m |= new
    return m[1:-1, 1:-1, 1:-1], added


def flyingedges_one(lm: LabelMap, lid: int, params: SurfaceParams = SurfaceParams(),
                    fix_topology: bool = False) -> Surface | None:
    """Fallback path for one label: discrete flying edges on its own mask + windowed sinc + box constraint.
    ``fix_topology`` first bridges edge- and corner-only voxel contacts (:func:`well_composed`)."""
    t0 = time.perf_counter()
    mask = lm.array == lid
    n_added = 0
    if fix_topology:
        mask, n_added = well_composed(mask)
    box = _bbox(mask)
    if box is None:
        return None
    lo, hi = box
    crop = np.pad(mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]], 2).astype(np.uint8)
    lo_xyz = (lo - 2)[::-1].astype(float)
    spacing = np.asarray(lm.spacing, float)
    fe = vtk.vtkDiscreteFlyingEdges3D()
    fe.SetInputData(_vtk_image(crop, spacing))
    fe.SetValue(0, 1)
    fe.ComputeNormalsOff()
    fe.ComputeGradientsOff()
    fe.ComputeScalarsOff()
    fe.Update()
    clean = vtk.vtkCleanPolyData()  # flying edges emits per-edge duplicate points on some VTK builds
    clean.SetInputConnection(fe.GetOutputPort())
    clean.PointMergingOn()
    clean.SetTolerance(0.0)
    clean.Update()
    V0, F = _poly_arrays(clean.GetOutput())
    if not len(F):
        return None
    Vs = windowed_sinc(V0, F, params.fallback_iterations, params.fallback_passband, non_manifold=False)
    Vs = _clamp(V0, Vs, spacing, params.constraint_voxels)
    mesh = _finish(Vs, F, lm.index_to_lps, lo_xyz, spacing)
    n = int(np.count_nonzero(crop))
    on_edge = any(mask[sl].any() for sl in (np.s_[0], np.s_[-1], np.s_[:, 0], np.s_[:, -1], np.s_[:, :, 0],
                                                np.s_[:, :, -1]))
    disp = np.linalg.norm(Vs - V0, axis=1)
    s = Surface(lid, mesh, "flyingedges_bridged" if fix_topology else "flyingedges", n, n * lm.voxel_volume_mm3,
                bool(on_edge), False, 0, float(disp.max()) if len(disp) else 0.0)
    s.bridged_voxels = n_added
    if fix_topology:  # volume change is still judged against the original label
        s.voxel_count = int(np.count_nonzero(lm.array == lid))
        s.voxel_volume_mm3 = s.voxel_count * lm.voxel_volume_mm3
    s.timings_s = {"flyingedges": time.perf_counter() - t0}
    return s
