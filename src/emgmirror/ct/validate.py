"""Independent numerical checks of the CT -> label map -> mesh pipeline.

Each function returns plain dicts (JSON-serialisable floats, ints, strings, lists) and states in
its docstring what the numbers can and cannot show. In short:

* ``overlap_metrics``: agreement between two segmentations, typically of two reconstructions of the
  same acquisition (fine axial series vs coarse coronal/sagittal stacks). It measures robustness to
  resolution, partial volume and reconstruction, and the stability of the algorithm. It does not
  measure anatomical correctness: an error shared by both reconstructions (wrong threshold, metal
  artefact, same leak) is invisible to it.
* ``threshold_sweep``: how much volumes move when the HU threshold moves. It bounds one known
  systematic error (threshold choice), not all of them.
* ``hu_sanity``: whether the HU scale and DICOM rescale look plausible from tissue peaks.
* ``bone_sanity``: plausibility of per-bone size and topology; information, not a pass/fail.
* ``accuracy_floor``: the voxel-size floor every geometric number must be read against.
* ``mesh_vs_mask``: that an exported mesh still sits on the label map it was made from.
* Tests on :mod:`emgmirror.ct.phantom` measure algorithmic error against analytic truth with
  idealised physics (no beam hardening, no metal streaks, no motion, white noise).

Geometry: masks are binary ``SimpleITK.Image`` (any non-zero = foreground) on grids that may differ in
spacing, extent and direction. All distances are computed in physical LPS mm. A binary mask is
treated as the union of its voxels (boxes); its surface is the set of voxel faces between foreground
and background. Faces on the array border are not part of the surface (outside the array is
unknown, not background).
"""

from __future__ import annotations

import math
from typing import Callable, Mapping, Sequence

import numpy as np
import SimpleITK as sitk
from scipy import ndimage
from scipy.spatial import cKDTree

from . import labels as L

# Rough anatomical rule of thumb for adult forearm bones, deliberately wide. Typical adult maximum
# lengths are about 22-26 cm (radius) and 24-28 cm (ulna); midshaft diameters about 1-2 cm. This is
# not a population reference and is only reported as in/out of band, never as a failure.
ADULT_FOREARM_EXPECTED: dict[str, dict[str, tuple[float, float]]] = {
    "radius": {"length_mm": (190.0, 290.0), "median_diameter_mm": (8.0, 25.0)},
    "ulna": {"length_mm": (210.0, 310.0), "median_diameter_mm": (8.0, 25.0)},
}

# HU plausibility bands (value ranges, HU). Chosen as plausibility checks, not QA tolerances:
# air -1000 +- 50 (QA phantoms are within ~10 HU; patient scans add noise, streaks and foam pads);
# fat -140..-60 (subcutaneous fat is about -120..-80 at 120 kVp, widened for kernel and noise);
# muscle +20..+80 (unenhanced muscle about +40..+70; the 'muscle' compartment also holds tendon,
# vessels and partial volume); cortical median +500..+2200 (dense cortex 1000-1900 HU, thin cortices
# and partial volume lower the median, sharp kernels raise edge values).
HU_EXPECTED: dict[str, tuple[float, float]] = {
    "air": (-1050.0, -950.0), "fat": (-140.0, -60.0), "muscle": (20.0, 80.0), "cortical": (500.0, 2200.0)}


# ----------------------------------------------------------------------------------------- geometry

def _geom(img: sitk.Image):
    return (np.asarray(img.GetOrigin(), float), np.asarray(img.GetSpacing(), float),
            np.asarray(img.GetDirection(), float).reshape(3, 3), np.asarray(img.GetSize(), int))


def _to_phys(img: sitk.Image, ci: np.ndarray) -> np.ndarray:
    """Continuous index (N, 3) in x, y, z order -> physical points (N, 3)."""
    o, s, d, _ = _geom(img)
    return o + (ci * s) @ d.T


def _to_index(img: sitk.Image, p: np.ndarray) -> np.ndarray:
    o, s, d, _ = _geom(img)
    return ((p - o) @ np.linalg.inv(d).T) / s


def _corners(img: sitk.Image, pad_mm: float = 0.0) -> np.ndarray:
    """8 physical corners of the region covered by the voxels (index -0.5 .. n-0.5), grown by pad_mm."""
    _, s, _, n = _geom(img)
    lo, hi = -0.5 - pad_mm / s, n - 0.5 + pad_mm / s
    return _to_phys(img, np.array(np.meshgrid(*zip(lo, hi), indexing="ij")).reshape(3, -1).T)


def _inside(img: sitk.Image, p: np.ndarray, margin_mm: float = 0.0) -> np.ndarray:
    """Points at least margin_mm inside the voxel-covered box of img (exact for oriented boxes)."""
    _, s, _, n = _geom(img)
    ci, m = _to_index(img, p), margin_mm / s
    return np.all((ci >= -0.5 + m) & (ci <= n - 0.5 - m), axis=1)


def _range_of(img: sitk.Image, pts: np.ndarray, pad_vox: int = 1) -> tuple[slice, ...] | None:
    """Numpy [z, y, x] slices of img covering the physical points pts (bounding box, clipped)."""
    _, _, _, n = _geom(img)
    ci = _to_index(img, pts)
    lo = np.maximum(np.floor(ci.min(0)).astype(int) - pad_vox, 0)
    hi = np.minimum(np.ceil(ci.max(0)).astype(int) + pad_vox, n - 1)
    if np.any(lo > hi):
        return None
    return tuple(slice(int(lo[a]), int(hi[a]) + 1) for a in (2, 1, 0))


def _box_of(img: sitk.Image, sl: tuple[slice, ...]) -> np.ndarray:
    """Physical corners of the voxel box of a [z, y, x] crop."""
    lo = np.array([sl[2].start, sl[1].start, sl[0].start], float) - 0.5
    hi = np.array([sl[2].stop, sl[1].stop, sl[0].stop], float) - 0.5
    return _to_phys(img, np.array(np.meshgrid(*zip(lo, hi), indexing="ij")).reshape(3, -1).T)


def _intersect(a: tuple[slice, ...] | None, b: tuple[slice, ...] | None) -> tuple[slice, ...] | None:
    if a is None or b is None:
        return None
    out = tuple(slice(max(x.start, y.start), min(x.stop, y.stop)) for x, y in zip(a, b))
    return None if any(s.start >= s.stop for s in out) else out


def _union(a: tuple[slice, ...] | None, b: tuple[slice, ...] | None) -> tuple[slice, ...] | None:
    if a is None or b is None:
        return a if b is None else b
    return tuple(slice(min(x.start, y.start), max(x.stop, y.stop)) for x, y in zip(a, b))


def _fg_slices(arr: np.ndarray, sl: tuple[slice, ...] | None, pad: int = 1) -> tuple[slice, ...] | None:
    """Bounding box (+pad voxels) of the non-zero voxels of arr inside the crop sl. Reads, never copies arr."""
    if sl is None:
        return None
    v = arr[sl]
    ext = []
    for ax in range(3):
        nz = np.flatnonzero(v.any(axis=tuple(a for a in range(3) if a != ax)))
        if nz.size == 0:
            return None
        ext.append((nz[0], nz[-1]))
    return tuple(slice(max(s.start + a - pad, 0), min(s.start + b + 1 + pad, arr.shape[i]))
                 for i, (s, (a, b)) in enumerate(zip(sl, ext)))


def _voxel_centres(img: sitk.Image, sl: tuple[slice, ...]) -> np.ndarray:
    z, y, x = np.meshgrid(*(np.arange(s.start, s.stop) for s in sl), indexing="ij")
    return _to_phys(img, np.stack([x.ravel(), y.ravel(), z.ravel()], 1).astype(float))


def _lookup(arr: np.ndarray, img: sitk.Image, p: np.ndarray) -> np.ndarray:
    """True where the voxel containing p is foreground (outside the array = background)."""
    ci = np.rint(_to_index(img, p)).astype(np.int64)
    ok = np.all((ci >= 0) & (ci < np.array(arr.shape[::-1])), axis=1)
    out = np.zeros(len(p), bool)
    out[ok] = arr[ci[ok, 2], ci[ok, 1], ci[ok, 0]] != 0
    return out


def accuracy_floor(spacing_xyz: Sequence[float]) -> dict:
    """Voxel-size floor of a binary voxel segmentation.

    A binary mask says only on which side of the threshold each voxel centre fell. The true surface
    can be anywhere between a foreground and a background centre, so without sub-voxel modelling
    (partial-volume fractions, grey-level interpolation) a surface is located to about half a spacing
    along each axis, and to at most half the voxel diagonal in the worst direction. Two masks on
    grids of this size can differ by up to that amount with neither being wrong. In the report,
    'coarse agreement relative to the coarse floor' = measured distance / ``half_diagonal_mm``: values below 1
    mean the disagreement is within what the grid can resolve; values above 1 are real disagreement
    (or misregistration) larger than the discretisation.
    """
    s = np.asarray(spacing_xyz, float)
    return {"spacing_mm": [float(v) for v in s], "min_spacing_mm": float(s.min()), "max_spacing_mm": float(s.max()),
            "half_max_spacing_mm": float(s.max() / 2), "half_diagonal_mm": float(np.linalg.norm(s) / 2),
            "voxel_volume_mm3": float(np.prod(s))}


def common_fov(a: sitk.Image, b: sitk.Image, margin_mm: float = 0.0, max_samples: int = 2_000_000) -> dict:
    """Volume and LPS extent of the region covered by both grids, shrunk by margin_mm.

    Estimated on a strided lattice of a's voxel centres (at most max_samples points), so volume and
    extent are approximate to about one lattice step at the boundary; ``lattice_step_mm`` says how much.
    """
    sl = _range_of(a, _corners(b), 0)
    out = {"volume_mm3": 0.0, "extent_mm": [0.0, 0.0, 0.0], "lps_min_mm": None, "lps_max_mm": None,
           "margin_mm": float(margin_mm), "lattice_step_mm": None}
    if sl is None:
        return out
    n = np.array([s.stop - s.start for s in sl])
    k = max(1, math.ceil((n.prod() / max_samples) ** (1 / 3)))
    lat = tuple(slice(s.start, s.stop, k) for s in sl)
    z, y, x = np.meshgrid(*(np.arange(s.start, s.stop, s.step) for s in lat), indexing="ij")
    p = _to_phys(a, np.stack([x.ravel(), y.ravel(), z.ravel()], 1).astype(float))
    p = p[_inside(a, p, margin_mm) & _inside(b, p, margin_mm)]
    _, s, _, _ = _geom(a)
    out["lattice_step_mm"] = [float(v) for v in s * k]
    if len(p):
        out.update(volume_mm3=float(len(p) * np.prod(s * k)), extent_mm=[float(v) for v in np.ptp(p, 0)],
                   lps_min_mm=[float(v) for v in p.min(0)], lps_max_mm=[float(v) for v in p.max(0)])
    return out


# ---------------------------------------------------------------------------------- surface samples

def _surface_samples(arr: np.ndarray, sl: tuple[slice, ...] | None, img: sitk.Image, step: float, odd: bool = False,
                     closed: bool = False):
    """Points on the boundary faces of the voxel solid, each face split into sub-samples <= step apart.

    Returns (points (N, 3) mm, outward normals (N, 3), area weights (N,) mm2). A face lies exactly on
    the binary boundary, so there is no half-voxel inward offset (as with voxel-centre surfaces).
    ``odd`` forces an odd number of sub-samples per face edge so that face centres are samples.
    ``closed`` counts the space beyond the array as background, so a solid cut by the array edge gets its
    end faces. That matches a mesh capped at the edge (mesh vs mask). Comparisons inside a common field of
    view leave it off: there the cut faces are the same in both masks and would hide differences.
    """
    empty = (np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0))
    if sl is None:
        return empty
    c = arr[sl] != 0
    off = np.array([s.start for s in sl], float)
    if closed:
        c = np.pad(c, 1)
        off -= 1
    _, s_xyz, d, _ = _geom(img)
    s_zyx = s_xyz[::-1]
    pts, nrm, wts = [], [], []
    for ax in range(3):
        lo, hi = [slice(None)] * 3, [slice(None)] * 3
        lo[ax], hi[ax] = slice(0, -1), slice(1, None)
        a, b = c[tuple(lo)], c[tuple(hi)]
        idx = np.nonzero(a != b)
        if idx[0].size == 0:
            continue
        sign = np.where(a[idx], 1.0, -1.0)  # foreground on the low side -> normal points to +ax
        ci = np.stack(idx, 1).astype(float) + off
        ci[:, ax] += 0.5
        oth = [o for o in range(3) if o != ax]
        m = [max(1, math.ceil(s_zyx[o] / step - 1e-9)) for o in oth]
        m = [k + 1 - k % 2 for k in m] if odd else m
        g0, g1 = np.meshgrid(*[(np.arange(k) + 0.5) / k - 0.5 for k in m], indexing="ij")
        sub = np.zeros((m[0] * m[1], 3))
        sub[:, oth[0]], sub[:, oth[1]] = g0.ravel(), g1.ravel()
        ci = (ci[:, None, :] + sub[None]).reshape(-1, 3)
        pts.append(_to_phys(img, ci[:, ::-1]))
        nrm.append(np.repeat(sign, m[0] * m[1])[:, None] * d[:, 2 - ax])
        wts.append(np.full(len(ci), s_zyx[oth[0]] * s_zyx[oth[1]] / (m[0] * m[1])))
    if not pts:
        return empty
    return np.concatenate(pts), np.concatenate(nrm), np.concatenate(wts)


def _signed_query(q, tree, t_pts, t_nrm, arr, img, step):
    """Distance from q to the sampled surface and sign (+1 outside the voxel solid, -1 inside, 0 on it).

    Near the surface (d <= step) the sign comes from the nearest face normal; farther away from the
    containing voxel, which is exact for the voxel solid.
    """
    d, j = tree.query(q, workers=-1)
    s = np.sign(np.einsum("ij,ij->i", q - t_pts[j], t_nrm[j]))
    far = d > step
    s[far] = np.where(_lookup(arr, img, q[far]), -1.0, 1.0)
    return d, s


def _wmean(v, w):
    return float(np.sum(v * w) / np.sum(w)) if len(v) else float("nan")


def _wpct(v, w, q):
    if not len(v):
        return float("nan")
    o = np.argsort(v)
    cw = np.cumsum(w[o])
    return float(v[o][min(np.searchsorted(cw, q / 100 * cw[-1]), len(v) - 1)])


# ----------------------------------------------------------------------------------------- overlap

def _fraction(src: sitk.Image, grid: sitk.Image, sl: tuple[slice, ...], k: Sequence[int]) -> np.ndarray:
    """Fraction of each grid voxel (crop sl) covered by src foreground, from k sub-samples per axis.

    Sub-samples are nearest-neighbour lookups in src, so the fraction is quantised to 1/prod(k) and
    each sub-sample carries src's own discretisation.
    """
    _, s, _, _ = _geom(grid)
    k = np.asarray(k, int)
    start = np.array([sl[2].start, sl[1].start, sl[0].start], float)
    size = np.array([sl[2].stop - sl[2].start, sl[1].stop - sl[1].start, sl[0].stop - sl[0].start], int)
    ref = sitk.Image([int(v) for v in size * k], sitk.sitkUInt8)
    ref.SetSpacing(tuple(float(v) for v in s / k))
    ref.SetDirection(grid.GetDirection())
    ref.SetOrigin(tuple(float(v) for v in _to_phys(grid, (start - 0.5 + 0.5 / k)[None])[0]))
    r = sitk.Resample(src, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0.0, sitk.sitkUInt8)
    a = sitk.GetArrayViewFromImage(r) != 0
    return a.reshape(size[2], k[2], size[1], k[1], size[0], k[0]).mean(axis=(1, 3, 5), dtype=np.float32)


def _native_volume(arr, sl, img, inside) -> float:
    if sl is None:
        return 0.0
    n, (z0, z1) = 0, (sl[0].start, sl[0].stop)
    for z in range(z0, z1, 32):
        idx = np.nonzero(arr[(slice(z, min(z + 32, z1)), sl[1], sl[2])])
        if idx[0].size:
            ci = np.stack([idx[2] + sl[2].start, idx[1] + sl[1].start, idx[0] + z], 1).astype(float)
            n += int(inside(_to_phys(img, ci)).sum())
    return float(n * np.prod(img.GetSpacing()))


def overlap_metrics(mask_ref: sitk.Image, mask_test: sitk.Image, *, max_points: int = 200_000, seed: int = 0,
                    margin_mm: float | None = None, search_mm: float = 25.0, step_mm: float | None = None) -> dict:
    """Dice, volumes and symmetric surface distances of two binary masks on possibly different grids.

    Common field of view: only the region covered by both grids, shrunk by ``margin_mm``, is
    compared. ``margin_mm`` defaults to twice the coarse grid's half voxel diagonal and is never less
    than one half diagonal. A structure that leaves this region is cut there: its surface points within the margin
    of the region boundary are excluded, so the cut does not create fake surface distance, and Dice and
    volumes count only voxels whose centres lie inside the shrunk region. A distance d from a point at
    least ``margin_mm`` inside the region is exact whenever d <= margin (its nearest point cannot lie
    outside the region); larger distances close to the boundary can be overestimated, which only
    matters once disagreement already exceeds twice the floor.

    Dice is computed on the coarser grid (larger voxel volume). The finer mask is resampled to it by
    volume fraction (k sub-samples per axis, k = ceil(coarse spacing / finest spacing), max 8) and
    binarised at fraction >= 0.5, which is what the coarse reconstruction would ideally record.
    ``dice_nearest`` (nearest-neighbour resampling at coarse centres) is also given; their difference
    shows how much the resampling choice alone moves Dice. Volumes are counted on each native grid.

    Surface distances: both surfaces are the boundary faces of the voxel solids, sampled every
    <= ``step_mm`` (default: finest spacing). Every query surface point (random subset of at most
    ``max_points`` per side) is matched to its nearest sample of the other surface with a k-d tree in
    physical mm, so anisotropic spacing and direction are handled exactly. Error bound of the
    approximation: sampling overestimates each distance by at most step/sqrt(2)
    (``sampling_error_bound_mm``). The staircase surfaces themselves differ from the smooth surfaces by
    up to each grid's half voxel diagonal; that is the ``voxel_floor_mm`` (coarse grid) below which
    disagreement is not resolvable. Means and percentiles are weighted by the face area each sample
    represents. hd95 = max of the two directed 95th percentiles. Distances above ``search_mm`` may be
    overestimated (the other surface is only searched within that distance outside the common FOV).

    Signed bias: d is signed + where a test surface point lies outside the reference solid, and + where
    a reference surface point lies inside the test solid; ``signed_bias_mm`` is their mean, so > 0 means
    the test surface is systematically outside (test larger). Sign is exact for the voxel solids.

    What it shows: agreement of two segmentations. Applied to two reconstructions of one acquisition
    it measures robustness to resolution and partial volume and algorithm stability, not anatomical
    correctness; errors common to both (threshold, metal artefact, shared leaks) do not appear.
    """
    a_ref, a_test = sitk.GetArrayViewFromImage(mask_ref), sitk.GetArrayViewFromImage(mask_test)
    s_ref, s_test = np.asarray(mask_ref.GetSpacing()), np.asarray(mask_test.GetSpacing())
    test_coarse = np.prod(s_test) > np.prod(s_ref)
    grid, fine = (mask_test, mask_ref) if test_coarse else (mask_ref, mask_test)
    floor = accuracy_floor(grid.GetSpacing())
    margin = max(2 * floor["half_diagonal_mm"] if margin_mm is None else float(margin_mm), floor["half_diagonal_mm"])
    step = float(step_mm or min(s_ref.min(), s_test.min()))
    fov = common_fov(grid, fine, margin)
    nan = float("nan")
    res = {"dice": nan, "dice_nearest": nan, "dice_grid": "test" if test_coarse else "ref",
           "dice_resampling": "finer mask -> coarser grid by volume fraction >= 0.5",
           "volume_ref_mm3": 0.0, "volume_test_mm3": 0.0, "volume_diff_pct": nan,
           "msd_mm": nan, "rms_mm": nan, "hd95_mm": nan, "hausdorff_mm": nan,
           "mean_ref_to_test_mm": nan, "mean_test_to_ref_mm": nan, "signed_bias_mm": nan,
           "n_surface_points": [0, 0], "n_surface_points_total": [0, 0],
           "common_fov_mm": fov["extent_mm"], "common_fov_volume_mm3": fov["volume_mm3"], "margin_mm": margin,
           "voxel_floor_mm": floor["half_diagonal_mm"], "sampling_error_bound_mm": step / math.sqrt(2),
           "step_mm": step, "search_mm": float(search_mm), "note": "",
           "interpretation": "agreement between two segmentations inside the common field of view; "
                             "not anatomical correctness"}
    if fov["volume_mm3"] == 0:
        res["note"] = "grids do not overlap (after margin)"
        return res

    def in_fov(p):
        return _inside(mask_ref, p, margin) & _inside(mask_test, p, margin)

    crop_ref = _fg_slices(a_ref, _range_of(mask_ref, _corners(mask_test, margin + search_mm), 1))
    crop_test = _fg_slices(a_test, _range_of(mask_test, _corners(mask_ref, margin + search_mm), 1))
    v_ref, v_test = _native_volume(a_ref, crop_ref, mask_ref, in_fov), _native_volume(a_test, crop_test, mask_test, in_fov)
    res.update(volume_ref_mm3=v_ref, volume_test_mm3=v_test,
               volume_diff_pct=100 * (v_test - v_ref) / v_ref if v_ref > 0 else nan)
    if v_ref == 0 and v_test == 0:
        res["note"] = "both masks empty inside the common field of view"
        return res

    # Dice on the coarse grid, slab by slab, limited to the foreground of either mask.
    a_grid, a_fine = (a_test, a_ref) if test_coarse else (a_ref, a_test)
    c_grid, c_fine = (crop_test, crop_ref) if test_coarse else (crop_ref, crop_test)
    region = _union(c_grid, _range_of(grid, _box_of(fine, c_fine), 1) if c_fine else None)
    region = _intersect(region, _range_of(grid, _corners(fine), 0))
    s_g = np.asarray(grid.GetSpacing())
    k = np.clip(np.ceil(s_g / min(s_ref.min(), s_test.min()) - 1e-9), 1, 8).astype(int)
    src = fine if fine.GetPixelID() == sitk.sitkUInt8 else sitk.Cast(fine != 0, sitk.sitkUInt8)
    inter = inter_nn = n_a = n_a_nn = n_b = 0
    if region is not None:
        plane = (region[1].stop - region[1].start) * (region[2].stop - region[2].start)
        dz = max(1, int(16_000_000 // (plane * k.prod())))
        for z in range(region[0].start, region[0].stop, dz):
            sl = (slice(z, min(z + dz, region[0].stop)), region[1], region[2])
            r = in_fov(_voxel_centres(grid, sl)).reshape(tuple(s.stop - s.start for s in sl))
            fa = (_fraction(src, grid, sl, k) >= 0.5) & r
            fn = (_fraction(src, grid, sl, (1, 1, 1)) > 0) & r
            b = (a_grid[sl] != 0) & r
            inter, inter_nn = inter + int((fa & b).sum()), inter_nn + int((fn & b).sum())
            n_a, n_a_nn, n_b = n_a + int(fa.sum()), n_a_nn + int(fn.sum()), n_b + int(b.sum())
    res["dice"] = 2 * inter / (n_a + n_b) if n_a + n_b else nan
    res["dice_nearest"] = 2 * inter_nn / (n_a_nn + n_b) if n_a_nn + n_b else nan

    # Surface distances.
    p_r, n_r, w_r = _surface_samples(a_ref, crop_ref, mask_ref, step)
    p_t, n_t, w_t = _surface_samples(a_test, crop_test, mask_test, step)
    q_r, q_t = in_fov(p_r), in_fov(p_t)
    res["n_surface_points_total"] = [int(q_r.sum()), int(q_t.sum())]
    if not q_r.any() or not q_t.any() or len(p_r) == 0 or len(p_t) == 0:
        res["note"] = "one mask has no surface inside the common field of view"
        return res
    rng = np.random.default_rng(seed)

    def pick(mask):
        i = np.flatnonzero(mask)
        return rng.choice(i, max_points, replace=False) if len(i) > max_points else i

    i_r, i_t = pick(q_r), pick(q_t)
    d_rt, s_rt = _signed_query(p_r[i_r], cKDTree(p_t), p_t, n_t, a_test, mask_test, step)  # ref -> test
    d_tr, s_tr = _signed_query(p_t[i_t], cKDTree(p_r), p_r, n_r, a_ref, mask_ref, step)  # test -> ref
    wr, wt = w_r[i_r], w_t[i_t]
    m_rt, m_tr = _wmean(d_rt, wr), _wmean(d_tr, wt)
    res.update(n_surface_points=[len(i_r), len(i_t)], mean_ref_to_test_mm=m_rt, mean_test_to_ref_mm=m_tr,
               msd_mm=0.5 * (m_rt + m_tr), rms_mm=math.sqrt(0.5 * (_wmean(d_rt ** 2, wr) + _wmean(d_tr ** 2, wt))),
               hd95_mm=max(_wpct(d_rt, wr, 95), _wpct(d_tr, wt, 95)),
               hausdorff_mm=float(max(d_rt.max(), d_tr.max())),
               signed_bias_mm=0.5 * (_wmean(-s_rt * d_rt, wr) + _wmean(s_tr * d_tr, wt)))
    return res


def volume_fraction_resample(mask: sitk.Image, grid: sitk.Image, max_factor: int = 8) -> sitk.Image:
    """Resample a binary mask onto grid as covered volume fraction (float32, 0..1), slab by slab."""
    s_g, s_m = np.asarray(grid.GetSpacing()), np.asarray(mask.GetSpacing())
    k = np.clip(np.ceil(s_g / s_m.min() - 1e-9), 1, max_factor).astype(int)
    src = mask if mask.GetPixelID() == sitk.sitkUInt8 else sitk.Cast(mask != 0, sitk.sitkUInt8)
    nx, ny, nz = grid.GetSize()
    out = np.zeros((nz, ny, nx), np.float32)
    dz = max(1, int(16_000_000 // (nx * ny * k.prod())))
    for z in range(0, nz, dz):
        sl = (slice(z, min(z + dz, nz)), slice(0, ny), slice(0, nx))
        out[sl] = _fraction(src, grid, sl, k)
    img = sitk.GetImageFromArray(out)
    img.CopyInformation(grid)
    return img


# --------------------------------------------------------------------------------- threshold sweep

def threshold_sweep(segment_fn: Callable[[float], dict[str, float]], thresholds: Sequence[float], reference: float,
                    areas_mm2: Mapping[str, float] | None = None) -> dict:
    """Volume sensitivity to the HU threshold.

    ``segment_fn(t)`` returns named volumes (mm3) for threshold t; it is called once per threshold (the
    reference is added if missing). Per structure: volume at the reference, % change per 100 HU by
    central difference over the nearest thresholds on each side (one-sided at the ends), and the range
    over the sweep in % of the reference volume. If ``areas_mm2`` (surface area at the reference) is
    given, volume changes are converted to an equivalent mean surface shift d = dV / A in mm, which can
    be compared with the voxel size. What it does not show: the correct threshold, or errors that do
    not depend on the threshold (artefacts, leaks, wrong connectivity).
    """
    ts = sorted({float(t) for t in thresholds} | {float(reference)})
    vols = {t: {k: float(v) for k, v in segment_fn(t).items()} for t in ts}
    names = list(vols[ts[0]])
    i0 = ts.index(float(reference))
    lo, hi = ts[max(i0 - 1, 0)], ts[min(i0 + 1, len(ts) - 1)]
    per = {}
    for n in names:
        v_ref = vols[ts[i0]][n]
        series = np.array([vols[t][n] for t in ts])
        dvdt = (vols[hi][n] - vols[lo][n]) / (hi - lo) if hi > lo else float("nan")  # mm3 per HU
        row = {"volume_at_reference_mm3": v_ref, "dV_dHU_mm3": dvdt,
               "pct_per_100hu": 100 * 100 * dvdt / v_ref if v_ref else float("nan"),
               "range_pct": 100 * float(np.ptp(series)) / v_ref if v_ref else float("nan"),
               "min_mm3": float(series.min()), "max_mm3": float(series.max())}
        a = (areas_mm2 or {}).get(n)
        if a:
            row.update(area_mm2=float(a), shift_mm_per_100hu=100 * dvdt / a, range_shift_mm=float(np.ptp(series)) / a)
        per[n] = row
    return {"reference_hu": float(reference), "thresholds_hu": ts, "central_difference_hu": [lo, hi],
            "table": [{"threshold_hu": t, **vols[t]} for t in ts], "structures": per}


# ------------------------------------------------------------------------------------- HU sanity

def _strided(shape: Sequence[int], max_samples: int) -> tuple[slice, ...]:
    k = max(1, math.ceil((float(np.prod(shape)) / max_samples) ** (1 / 3)))
    return (slice(None, None, k),) * 3


def _peak(v: np.ndarray, lo: float, hi: float, n_body: int, min_frac: float = 0.03, bw: float = 2.0):
    """Histogram mode of v inside [lo, hi], with whether it is a real peak (not at the window edge and
    holding >= min_frac of the body samples within +-30 HU)."""
    w = v[(v >= lo) & (v <= hi)]
    if w.size < 50:
        return float("nan"), 0.0, False
    h, e = np.histogram(w, bins=np.arange(lo, hi + bw, bw))
    h = ndimage.gaussian_filter1d(h.astype(float), 6.0 / bw)
    m = float(e[np.argmax(h)] + bw / 2)
    frac = float(np.sum(np.abs(w - m) <= 30) / max(n_body, 1))
    return m, frac, bool(lo + 10 < m < hi - 10 and frac >= min_frac)


def hu_sanity(image: sitk.Image, labels: sitk.Image | None = None, body_mask: np.ndarray | None = None,
              *, max_samples: int = 4_000_000, expected: Mapping[str, tuple[float, float]] | None = None) -> dict:
    """Plausibility of the HU scale from robust tissue statistics on a strided subsample.

    * air: median HU of voxels outside the body (labels == 0, or ~body_mask, or with neither, all voxels)
      within (-1100, -200) HU: the upper limit drops cradle, table and pads; the lower limit drops
      out-of-reconstruction padding (-2048, -3024). Foam pads near -900..-700 can pull it up slightly.
    * fat and muscle: mode of the in-body histogram in [-250, -20] and [-20, +150] HU. Histogram modes
      are used instead of label medians on purpose: labels made by HU windows have medians forced into
      the window, so they cannot detect a shifted scale. A mode counts only if it is not at a window
      edge and holds >= 3 % of the body samples within +-30 HU. Label medians are reported as info.
    * cortical: median HU of cortical labels (10 + 2k); without labels, of in-body voxels in 400..3000 HU.
    * max/min over the full volume (streamed, no copy) and whether the scale is clipped at 3071 (12 bit).

    Strided subsampling reads every k-th voxel per axis (k chosen so <= max_samples), copying only those.
    What it does not show: local HU errors (beam hardening near metal, streaks), kernel-dependent edge
    overshoot, or correctness of any segmentation.
    """
    exp = {**HU_EXPECTED, **(expected or {})}
    view = sitk.GetArrayViewFromImage(image)
    st = _strided(view.shape, max_samples)
    hu = np.asarray(view[st], np.float32)
    lab = np.asarray(sitk.GetArrayViewFromImage(labels)[st]) if labels is not None else None
    if body_mask is not None:
        body = np.asarray(body_mask[st], bool)
    elif lab is not None:
        body = lab > 0
    else:
        body = hu > -500
    out: dict = {"n_samples": int(hu.size), "stride": int(st[0].step)}

    def item(name, value, ok=None, **extra):
        lo, hi = exp.get(name, (None, None))
        if ok is None:
            ok = bool(np.isfinite(value) and lo <= value <= hi)
        out[name] = {"value": float(value), "expected_range": [lo, hi], "ok": bool(ok), **extra}

    outside = ~body if (labels is not None or body_mask is not None) else np.ones_like(body)
    air = hu[outside & (hu > -1100) & (hu < -200)]
    item("air", float(np.median(air)) if air.size else float("nan"), n=int(air.size))
    inb = hu[body]
    for name, (lo, hi), lid in (("fat", (-250.0, -20.0), L.FAT), ("muscle", (-20.0, 150.0), L.MUSCLE)):
        m, frac, peak = _peak(inb, lo, hi, inb.size)
        extra = {"peak_fraction": frac, "is_peak": peak, "window_hu": [lo, hi]}
        if lab is not None and np.any(lab == lid):
            extra["label_median"] = float(np.median(hu[lab == lid]))
        item(name, m, ok=peak and exp[name][0] <= m <= exp[name][1], **extra)
    if lab is not None:
        cort = np.isin(lab, [i for i in np.unique(lab) if L.is_bone(int(i)) and (int(i) - L.BONE_BASE) % 2 == 0])
        cv, src = hu[cort], "cortical labels"
    else:
        cv, src = inb[(inb > 400) & (inb < 3000)], "in-body voxels 400..3000 HU"
    item("cortical", float(np.median(cv)) if cv.size else float("nan"), n=int(cv.size), source=src)
    mm = sitk.MinimumMaximumImageFilter()
    mm.Execute(image)
    mx = mm.GetMaximum()
    clipped = mx in (3071.0, 3072.0, 4095.0)
    out["max"] = {"value": float(mx), "expected_range": [None, None], "ok": not clipped, "clipped": clipped,
                  "scale": "clipped (12 bit)" if clipped else ("extended" if mx > 3071 else "below 3071"),
                  "min": float(mm.GetMinimum())}
    out["ok"] = all(out[k]["ok"] for k in ("air", "fat", "muscle", "cortical") if np.isfinite(out[k]["value"]))
    return out


# ------------------------------------------------------------------------------------ bone sanity

def bone_sanity(labels: sitk.Image, names: Mapping[int, str],
                expected: Mapping[str, Mapping[str, tuple[float, float]]] | None = None) -> dict:
    """Per-bone size, shape and topology from a label map (cortical 10+2k + medullary 11+2k = bone k).

    Per bone: volume (cm3), cortical fraction, length along the principal axis (PCA of voxel centres in
    mm; extent of projections plus the projected voxel size; robust variant = 0.5-99.5 percentile
    range), max and median cross-section equivalent diameter 2*sqrt(A/pi) of slabs perpendicular to the
    axis, image faces touched (a cut bone's length is a lower bound), number of 26-connected components.
    ``expected`` maps bone name -> metric -> (lo, hi); default ``ADULT_FOREARM_EXPECTED`` (rough rule
    of thumb). In/out of band is information, never a failure. What it does not show: whether the bone
    outline is right, only whether its size and topology are plausible.
    """
    exp = ADULT_FOREARM_EXPECTED if expected is None else expected
    arr = sitk.GetArrayViewFromImage(labels)
    _, s, d, n = _geom(labels)
    vvox = float(np.prod(s))
    stats = sitk.LabelShapeStatisticsImageFilter()
    stats.ComputePerimeterOff()
    stats.ComputeFeretDiameterOff()
    stats.Execute(labels)
    present = set(stats.GetLabels())
    bones: dict[int, list[int]] = {}
    for lid in names:
        if L.is_bone(lid):
            bones.setdefault(L.bone_index(lid), [])
    out: dict = {}
    for k in sorted(bones):
        ids = [i for i in (L.cortical(k), L.medullary(k)) if i in present]
        base = names.get(L.cortical(k), names.get(L.medullary(k), f"bone_{k + 1}"))
        base = base.removesuffix("_cortical").removesuffix("_medullary")
        flags: list[str] = []
        if not ids:
            out[base] = {"volume_cm3": 0.0, "flags": ["bone label is empty"]}
            continue
        bb = np.array([stats.GetBoundingBox(i) for i in ids])
        lo, hi = bb[:, :3].min(0), (bb[:, :3] + bb[:, 3:]).max(0)  # x, y, z
        sl = tuple(slice(int(lo[a]), int(hi[a])) for a in (2, 1, 0))
        crop = np.asarray(arr[sl])
        n_cort = int(np.sum(crop == L.cortical(k)))
        n_med = int(np.sum(crop == L.medullary(k)))
        m = (crop == L.cortical(k)) | (crop == L.medullary(k))
        idx = np.nonzero(m)
        ci = np.stack([idx[2] + lo[0], idx[1] + lo[1], idx[0] + lo[2]], 1).astype(float)
        p = _to_phys(labels, ci)
        c = p - p.mean(0)
        w, v = np.linalg.eigh(c.T @ c / len(c))
        axis = v[:, -1]
        t = c @ axis
        vox_ext = float(np.sum(np.abs(axis @ d) * s))  # voxel size projected on the axis
        length = float(np.ptp(t) + vox_ext)
        robust = float(np.percentile(t, 99.5) - np.percentile(t, 0.5))
        cnt = np.bincount(((t - t.min()) / vox_ext + 0.5).astype(int))
        cnt = cnt[cnt > 0]
        diam = 2 * np.sqrt(cnt * vvox / vox_ext / np.pi)
        lab_cc, ncc = ndimage.label(m, structure=np.ones((3, 3, 3)))
        sizes = np.bincount(lab_cc.ravel())[1:]
        touch = [f"{ax}_{side}" for a, ax in enumerate("xyz")
                 for side, hit in (("min", lo[a] == 0), ("max", hi[a] == n[a])) if hit]
        vol = (n_cort + n_med) * vvox
        row = {"volume_cm3": vol / 1000, "cortical_volume_cm3": n_cort * vvox / 1000,
               "medullary_volume_cm3": n_med * vvox / 1000, "cortical_fraction": n_cort / (n_cort + n_med),
               "length_mm": length, "length_robust_mm": robust, "axis_lps": [float(x) for x in axis],
               "max_diameter_mm": float(diam.max()), "median_diameter_mm": float(np.median(diam)),
               "slab_mm": vox_ext, "touches_border": touch, "n_components": int(ncc),
               "largest_component_fraction": float(sizes.max() / sizes.sum())}
        if n_med == 0:
            flags.append("medullary volume is zero")
        if n_cort == 0:
            flags.append("cortical volume is zero")
        if touch:
            flags.append(f"bone touches the scan edge ({', '.join(touch)}): length is a lower bound")
        if ncc > 1:
            flags.append(f"bone is split into {ncc} connected pieces "
                         f"(largest {100 * row['largest_component_fraction']:.1f} % of voxels)")
        rng = {}
        for metric, (a, b) in exp.get(base, {}).items():
            val = row.get(metric)
            if val is None:
                continue
            inr = bool(a <= val <= b)
            rng[metric] = {"value": val, "band": [a, b], "in_band": inr}
            if not inr:
                lb = " (lower bound)" if metric.startswith("length") and touch else ""
                flags.append(f"{metric} {val:.1f}{lb} outside the rough band {a:g}-{b:g}")
        row["expected"] = rng
        row["flags"] = flags
        out[base] = row
    return out


# ----------------------------------------------------------------------------------- mesh vs mask

def mesh_vs_mask(mesh_vertices_mm: np.ndarray, mesh_faces: np.ndarray, mask: sitk.Image,
                 step_mm: float | None = None) -> dict:
    """Distance of a mesh to the boundary of the voxel mask it was made from, with inside/outside sign.

    Evaluated at the vertices and at triangle centroids (area weighted). The mask surface is the
    voxel-face boundary sampled every <= step_mm (default half the finest spacing; sampling
    overestimates distances by at most step/sqrt(2); face centres are always samples). A marching-cubes
    surface at level 0.5 on the mask has its vertices at face centres (vertex distance 0) and cuts staircase corners (centroid distance up to
    about half a voxel). Signed > 0 = mesh outside the mask (inflated), < 0 = inside (shrunk, e.g. by
    smoothing). What it does not show: whether the mask itself is right.
    """
    v = np.asarray(mesh_vertices_mm, float)
    f = np.asarray(mesh_faces, int)
    arr = sitk.GetArrayViewFromImage(mask)
    s = np.asarray(mask.GetSpacing())
    step = float(step_mm or s.min() / 2)
    pad = float(2 * s.max() + 5.0)
    sl = _fg_slices(arr, _range_of(mask, np.vstack([v.min(0) - pad, v.max(0) + pad]), 1))
    p, nrm, _ = _surface_samples(arr, sl, mask, step, odd=True, closed=True)
    floor = accuracy_floor(s)
    if len(p) == 0 or len(v) == 0:
        return {"note": "empty mask or mesh", "voxel_floor_mm": floor["half_diagonal_mm"]}
    tree = cKDTree(p)
    tri = v[f]
    cen = tri.mean(1)
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    dv, sv = _signed_query(v, tree, p, nrm, arr, mask, step)
    dc, sc = _signed_query(cen, tree, p, nrm, arr, mask, step)
    return {"n_vertices": int(len(v)), "n_faces": int(len(f)), "surface_area_mm2": float(area.sum()),
            "vertex_mean_abs_mm": float(dv.mean()), "vertex_max_mm": float(dv.max()),
            "vertex_signed_mean_mm": float(np.mean(sv * dv)),
            "mean_abs_mm": _wmean(dc, area), "rms_mm": math.sqrt(_wmean(dc ** 2, area)),
            "p95_mm": _wpct(dc, area, 95), "max_mm": float(dc.max()), "signed_mean_mm": _wmean(sc * dc, area),
            "voxel_floor_mm": floor["half_diagonal_mm"], "sampling_error_bound_mm": step / math.sqrt(2)}


# ------------------------------------------------------------------------------- enclosed tissue

def enclosed_tissue(labels: sitk.Image, names: Mapping[int, str], top: int = 3) -> dict:
    """Non-bone voxels that a bone surrounds in at least one slice (axial, coronal or sagittal), per bone.

    A ring of bone closes around marrow, so a wrong label inside the bone outline shows up here. Real anatomy
    also does (a canal around an implant, a fossa seen edge-on), so this is a list to look at, not a pass/fail.
    For each of the largest regions it gives the volume, the labels found there and the box fill (region volume
    over its bounding box): a region that fills most of its box is a straight-edged artefact, not anatomy.
    Metal voxels are not counted as tissue. What it does not show: tissue that is wrongly labelled bone.
    """
    arr = sitk.GetArrayViewFromImage(labels)
    vvox = float(np.prod(labels.GetSpacing()))
    stats = sitk.LabelShapeStatisticsImageFilter()
    stats.ComputePerimeterOff()
    stats.ComputeFeretDiameterOff()
    stats.Execute(labels)
    present = set(stats.GetLabels())
    ks = sorted({L.bone_index(i) for i in names if L.is_bone(i)})
    out: dict = {}
    for k in ks:
        ids = [i for i in (L.cortical(k), L.medullary(k)) if i in present]
        base = names.get(L.cortical(k), names.get(L.medullary(k), f"bone_{k + 1}"))
        base = base.removesuffix("_cortical").removesuffix("_medullary")
        if not ids:
            continue
        bb = np.array([stats.GetBoundingBox(i) for i in ids])
        lo, hi = bb[:, :3].min(0), (bb[:, :3] + bb[:, 3:]).max(0)
        sl = tuple(slice(int(lo[a]), int(hi[a])) for a in (2, 1, 0))
        crop = np.asarray(arr[sl])
        bone = np.isin(crop, ids)
        enc = np.zeros(bone.shape, bool)
        for ax in range(3):
            b = np.moveaxis(bone, ax, 0)
            e = np.moveaxis(enc, ax, 0)
            for i in range(b.shape[0]):
                if b[i].any():
                    e[i] |= ndimage.binary_fill_holes(b[i])
        enc &= ~bone & (crop != L.METAL)
        lab, n = ndimage.label(enc)
        row = {"enclosed_cm3": float(enc.sum()) * vvox / 1000, "n_regions": int(n), "largest": []}
        if n:
            sizes = np.bincount(lab.ravel())[1:]
            objs = ndimage.find_objects(lab)
            for j in np.argsort(-sizes)[:top]:
                o = objs[j]
                box = int(np.prod([q.stop - q.start for q in o]))
                vals = np.unique(crop[o][lab[o] == j + 1])
                row["largest"].append({"volume_mm3": float(sizes[j]) * vvox, "box_fill": float(sizes[j]) / box,
                                       "labels": [int(v) for v in vals],
                                       "index_zyx": [[int(q.start + s.start), int(q.stop + s.start)]
                                                     for q, s in zip(o, sl)]})
        row["flags"] = [f"a straight-edged region of {r['volume_mm3']:.0f} mm3 inside the bone outline (box fill "
                        f"{r['box_fill']:.2f}): likely an artefact of the method, not anatomy"
                        for r in row["largest"] if r["box_fill"] > 0.5 and r["volume_mm3"] > 20]
        out[base] = row
        del crop, bone, enc, lab
    return out


# ---------------------------------------------------------------------------------------- report

def _f(x, nd=2):
    if x is None:
        return "n/a"
    if isinstance(x, (list, tuple)):
        return " x ".join(_f(v, nd) for v in x)
    if isinstance(x, (float, np.floating)) and not np.isfinite(x):
        return "n/a"
    return f"{x:.{nd}f}" if isinstance(x, (float, np.floating)) else str(x)


def _named(d: Mapping | None, key: str) -> dict:
    if not d:
        return {}
    return {"": d} if key in d else dict(d)


def _table(head: Sequence[str], rows: Sequence[Sequence]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |"
                                                                     for r in rows]


def format_validation_markdown(results: Mapping) -> str:
    """Markdown report sections from a dict with any of the keys ``accuracy_floor``, ``overlap``,
    ``threshold_sweep``, ``hu_sanity``, ``bone_sanity``, ``mesh_vs_mask``. ``accuracy_floor``,
    ``overlap`` and ``mesh_vs_mask`` may be a single result or a mapping name -> result."""
    out: list[str] = ["# Validation", ""]
    fl = _named(results.get("accuracy_floor"), "half_diagonal_mm")
    if fl:
        out += ["## Voxel-size floor", ""]
        out += _table(["grid", "spacing x, y, z (mm)", "largest spacing (mm)", "half voxel diagonal (mm)"],
                      [[n or "-", _f(r["spacing_mm"], 3), _f(r["max_spacing_mm"], 3), _f(r["half_diagonal_mm"], 3)]
                       for n, r in fl.items()])
        out += ["", "A binary voxel surface is located to about half a voxel; distances below the half diagonal "
                    "are not resolvable on that grid. This does not show the size of any actual error.", ""]
    ov = _named(results.get("overlap"), "dice")
    if ov:
        out += ["## Agreement between reconstructions", ""]
        out += _table(["comparison", "Dice", "Dice (nearest)", "V ref (cm3)", "V test (cm3)", "dV (%)", "MSD (mm)",
                       "ref -> test mean (mm)", "test -> ref mean (mm)",
                       "RMS (mm)", "HD95 (mm)", "max (mm)", "bias (mm)", "floor (mm)", "MSD / floor"],
                      [[n or "-", _f(r["dice"], 3), _f(r["dice_nearest"], 3), _f(r["volume_ref_mm3"] / 1000),
                        _f(r["volume_test_mm3"] / 1000), _f(r["volume_diff_pct"], 1), _f(r["msd_mm"]),
                        _f(r.get("mean_ref_to_test_mm")), _f(r.get("mean_test_to_ref_mm")),
                        _f(r["rms_mm"]), _f(r["hd95_mm"]), _f(r["hausdorff_mm"]), _f(r["signed_bias_mm"]),
                        _f(r["voxel_floor_mm"]), _f(r["msd_mm"] / r["voxel_floor_mm"])] for n, r in ov.items()])
        out += ["", "ref -> test mean: average distance from the primary (reference) surface to the coarse one; "
                    "test -> ref mean: the other way. A large test -> ref value with a small ref -> test value means "
                    "the coarse run has bone the primary does not, and the reverse means it misses bone."]
        out += [""] + [f"- {n or 'comparison'}: common field of view {_f(r['common_fov_mm'], 1)} mm "
                       f"(margin {_f(r['margin_mm'], 1)} mm), surface points {r['n_surface_points']}, "
                       f"sampling error <= {_f(r['sampling_error_bound_mm'])} mm, Dice grid {r['dice_grid']}"
                       + (f", note: {r['note']}" if r.get("note") else "") for n, r in ov.items()]
        out += ["", "Bias > 0 means the test surface lies outside the reference. These numbers compare two "
                    "segmentations of one acquisition; they do not show anatomical correctness or errors "
                    "shared by both reconstructions.", ""]
    sw = results.get("threshold_sweep")
    if sw:
        out += [f"## Threshold sensitivity (reference {_f(sw['reference_hu'], 0)} HU)", ""]
        names = list(sw["structures"])
        out += _table(["threshold (HU)"] + [f"{n} (cm3)" for n in names],
                      [[_f(r["threshold_hu"], 0)] + [_f(r[n] / 1000) for n in names] for r in sw["table"]])
        out += [""] + _table(["structure", "V at reference (cm3)", "change per 100 HU (%)", "range over sweep (%)",
                              "shift per 100 HU (mm)", "range as shift (mm)"],
                             [[n, _f(r["volume_at_reference_mm3"] / 1000), _f(r["pct_per_100hu"], 1),
                               _f(r["range_pct"], 1), _f(r.get("shift_mm_per_100hu")), _f(r.get("range_shift_mm"))]
                              for n, r in sw["structures"].items()])
        out += ["", "This bounds the error from the threshold choice only; it does not show which threshold is "
                    "correct or errors that do not depend on the threshold.", ""]
    hs = results.get("hu_sanity")
    if hs:
        out += ["## HU calibration", ""]
        rows = []
        for k in ("air", "fat", "muscle", "cortical", "max"):
            if k in hs:
                r = hs[k]
                lo, hi = r["expected_range"]
                rows.append([k, _f(r["value"], 0) + " HU", "-" if lo is None else f"{lo:g} to {hi:g} HU",
                             "yes" if r["ok"] else "no", r.get("scale", "")])
        out += _table(["tissue", "value", "expected", "ok", "note"], rows)
        out += ["", f"Values from every {hs['stride']}th voxel per axis ({hs['n_samples']} samples). This does not "
                    "show local HU errors such as beam hardening or streaks near metal.", ""]
    bs = results.get("bone_sanity")
    if bs:
        out += ["## Bone plausibility", ""]
        out += _table(["bone", "volume (cm3)", "cortical fraction", "length (mm)", "length p0.5-p99.5 (mm)",
                       "max diameter (mm)", "median diameter (mm)", "components", "touches edge"],
                      [[n, _f(r.get("volume_cm3")), _f(r.get("cortical_fraction")), _f(r.get("length_mm"), 1),
                        _f(r.get("length_robust_mm"), 1), _f(r.get("max_diameter_mm"), 1),
                        _f(r.get("median_diameter_mm"), 1), r.get("n_components", "n/a"),
                        ", ".join(r.get("touches_border", [])) or "no"] for n, r in bs.items()])
        flags = [f"- {n}: {fl}" for n, r in bs.items() for fl in r.get("flags", [])]
        out += [""] + (flags or ["- no flags"])
        out += ["", "Bands are rough adult rules of thumb; being inside them does not show that the outline is "
                    "correct.", ""]
    mv = _named(results.get("mesh_vs_mask"), "mean_abs_mm")
    if mv:
        out += ["## Mesh against label map", ""]
        out += _table(["mesh", "vertex mean (mm)", "mean (mm)", "RMS (mm)", "P95 (mm)", "max (mm)", "signed mean (mm)",
                       "floor (mm)"],
                      [[n or "-", _f(r.get("vertex_mean_abs_mm"), 3), _f(r.get("mean_abs_mm"), 3), _f(r.get("rms_mm"), 3),
                        _f(r.get("p95_mm"), 3), _f(r.get("max_mm"), 3), _f(r.get("signed_mean_mm"), 3),
                        _f(r.get("voxel_floor_mm"), 3)] for n, r in mv.items()])
        out += ["", "Signed > 0 means the mesh lies outside the label map. This shows that the mesh follows the label "
                    "map, not that the label map follows the anatomy.", ""]
    return "\n".join(out)
