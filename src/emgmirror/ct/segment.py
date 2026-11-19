"""CT segmentation into body, fat, lean tissue, metal and per-bone cortical / medullary labels (no learning).

Two layers:

* Generic layer, valid for any CT: padding and air, body mask without table/cradle, metal with a blooming rim,
  bone by hysteresis thresholding, a solid periosteal envelope per bone with a medullary label inside, bones
  numbered ``bone_1..n`` by decreasing volume, fat and lean tissue, and an artefact region around metal.
* Forearm naming layer: renames bones to radius / ulna / humerus_distal / carpals_hand only when several
  independent pieces of evidence agree; otherwise it keeps generic names and says why in ``info``.

Every size is physical (mm, mm3, HU). Morphology uses the per-axis spacing and shape descriptors use the
direction matrix, so nothing depends on array axis order, orientation, or the whole limb being in view.
Heavy steps run on the body bounding box; distances use SimpleITK's float32 Maurer transform.
"""

from __future__ import annotations

import gc
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
import SimpleITK as sitk
from scipy import ndimage as ndi

from . import labels as L

EDGE_FRACTIONS = (0.4, 0.6)  # alternative edge levels for the surface sensitivity check (0.5 = half-maximum)

@dataclass
class SegParams:
    """Thresholds and sizes. ``None`` means "measure it on this scan" (the value used is reported in info)."""

    # --- padding / air / body ---
    pad_min_fraction: float = 0.002  # one exact value <= -1024 HU on >= 0.2 % of voxels is reconstruction padding,
    # not air: real air is noisy and never concentrates on one value (the -1024 clip floor is handled as air).
    body_hu: float | None = None  # skin surface threshold; None = half-way between the air and soft-tissue peaks
    # (the half-maximum of the air/skin edge), clamped to [-700, -300].
    body_coarse_mm: float = 1.0  # grid for the body opening; fine detail is restored from the full-resolution threshold.
    body_open_radius_mm: float = 4.0  # opening ball radius: removes shells/specks thinner than 8 mm (the measured
    # cradle is ~4-5 mm thick incl. its bright layer) while fingers (>= ~10 mm thick) and limbs survive.
    body_margin_mm: float = 2.0  # full-resolution skin voxels within this distance of the opened body are restored
    # (covers the 1 mm coarse-grid quantisation plus sub-mm skin detail); a touching cradle is kept only this deep.
    min_body_mm3: float = 2000.0  # body pieces below 2 cm3 (specks, props) are dropped; a fingertip is larger.
    body_tissue_hu: tuple[float, float] = (-300.0, 300.0)  # a kept body piece must have its median HU in this
    # range (fat ... lean); thick plastic or foam props fail it.
    # --- metal ---
    metal_seed_floor_hu: float = 3000.0  # metal seeds never below 3000 HU: compact cortex peaks at ~1500-2000 HU at
    # 120 kV and sharp-kernel overshoot rarely passes ~2500 HU; titanium is ~6000-8000+, steel > 10000 (extended scale).
    metal_seed_margin_hu: float = 500.0  # seed = max(floor, measured end of the cortical tail + this margin).
    min_metal_mm3: float = 0.5  # smaller seed blobs are noise / starvation spikes (a 0.8 mm wire 1 mm long is 0.5 mm3).
    metal_rim_max_mm: float = 2.0  # cap of the blooming rim excluded from bone seeds (measured per scan, >= 1 voxel).
    artefact_radius_mm: float = 50.0  # streaks cross the whole limb section; the artefact region is searched this far.
    artefact_density: float = 0.05  # region = where >= 5 % of nearby soft-tissue voxels have implausible HU.
    artefact_sigma_mm: float = 2.0  # neighbourhood for that local fraction and for in-painting (normalised convolution).
    # --- bone ---
    bone_low_hu: float | None = None  # hysteresis low threshold = periosteal surface; None = measured (see _bone_thresholds)
    bone_high_hu: float | None = None  # hysteresis seeds; None = half-maximum between soft-tissue and cortical peaks
    skin_bone_gap_mm: float = 1.0  # no bone within this distance of air: skin and dermis are thicker than this
    # everywhere on a limb (shallowest bone on this forearm: 2.5 mm), while the sharp kernel's edge overshoot at the
    # skin reaches 250-360 HU and passes as bone on the 2 mm reformats.
    bone_noise_k: float = 6.0  # low threshold never closer than 6 noise sigmas above the lean peak (rejects noise).
    bone_smooth_mm: float = 0.0  # optional Gaussian sigma before bone thresholding (0: sharp kernel as is; see info).
    min_bone_mm3: float = 25.0  # smaller bone blobs are calcifications / noise; sesamoids (~50-100 mm3) survive.
    close_mm: float = 1.5  # closing radius that plugs foramina, screw holes and gaps < 3 mm before hole filling.
    give_back_ball_mm: float = 0.75  # what the closing added near the surface is returned only where a ball of this
    # radius reaches it from outside the bone (a concavity); a seam under a dotted thin cortex or a pore under a
    # porous cortex, reached only through gaps < 1.5 mm, stays bone.
    surface_layer_mm: float = 1.0  # outer envelope layer re-thresholded at the LOCAL half-maximum between lean tissue
    # and the nearby cortical maximum (Hangartner 2007, J Musculoskelet Neuronal Interact 7:9): removes the
    # partial-volume halo the low hysteresis threshold adds (~ +0.5 voxel radius) without losing thin cortex.
    local_max_mm: float = 1.0  # half-width of the neighbourhood searched for the local cortical maximum
    marker_grid_mm: float = 0.9  # grid for the marker distance map (markers only need to lie inside each part)
    split_h_mm: float = 1.0  # distance-map maxima must stand >= 1 mm above the saddle to seed a separate part
    # (suppresses ripples along a shaft; any real neck or joint contact is deeper than 1 mm).
    merge_contrast: float = 0.8  # border p90 / region p90 at or above which two parts are one bone (see _separate)
    pocket_exposure: float = 0.15  # a cavity that only slice-by-slice filling closes (a canal open at a fracture, but
    # also a fossa or notch that happens to look ringed in one slice direction) is bone interior only when at most
    # this share of its surface is open to the outside: an open canal is a long tube with a small mouth (a few %),
    # a fossa is a shallow pocket with a wide mouth (a quarter or more; values found are listed in info).
    island_exposure: float = 0.10  # a bone region with < 10 % of its surface facing non-bone is an interior island
    # (dense trabecular cluster inside another bone) and is merged into its main neighbour.
    cortical_hu: float | None = None  # cortical vs medullary split inside the envelope; None = Otsu of envelope HU.
    cortical_smooth_mm: float = 0.5  # Gaussian sigma (inside the envelope only) before that split: trabeculae
    # (~0.1-0.3 mm) are not resolved, so their MEAN density is what can be classified; 0.5 mm keeps 1 mm cortex.
    # --- soft tissue ---
    fat_lean_hu: float | None = None  # None = half-way between the in-body fat and lean peaks (see _soft_tissue).
    soft_median_mm: float = 0.5  # median filter half-width before fat/lean classification (edge preserving).
    skin_mm: float = 1.5  # skin thickness (forearm dermis ~1-2 mm) reported as a lean shell; also fascia offset.
    skin_pv_mm: float = 0.6  # outermost layer forced to lean: air/skin partial volume otherwise reads as fat.
    fascia_close_mm: float = 3.0  # closing of the deep (sub-fascial) tissue to separate subcutaneous fat.
    # --- naming ---
    name_bones: bool = True  # run the forearm naming layer
    long_bone_min_elongation: float = 4.0  # length / equivalent diameter of a long bone
    long_bone_min_span: float = 0.4  # long bones span >= 40 % of the body extent along the limb axis
    taper_min_ratio: float = 1.25  # end-area ratio needed to call a bone end "large"
    limb_taper_min_ratio: float = 1.15  # body cross-section ratio (25 % vs 75 % of the forearm span) for proximal


@dataclass
class SegResult:
    labels: sitk.Image
    names: dict[int, str]
    info: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------------------------------- utilities

def _mem_mb() -> tuple[float, float]:
    """(current, peak) resident memory of this process in MB (Windows working set or POSIX maxrss)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            k = ctypes.WinDLL("kernel32")
            k.GetCurrentProcess.restype = wintypes.HANDLE
            ps = ctypes.WinDLL("psapi")
            ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            ps.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return c.WorkingSetSize / 2 ** 20, c.PeakWorkingSetSize / 2 ** 20
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        return r, r
    except Exception:  # pragma: no cover
        return float("nan"), float("nan")


class _Clock:
    def __init__(self, progress):
        self.t0 = self.t = time.perf_counter()
        self.steps: list[dict] = []
        self.progress = progress

    def __call__(self, name: str, frac: float):
        gc.collect()
        now = time.perf_counter()
        cur, peak = _mem_mb()
        self.steps.append({"step": name, "seconds": round(now - self.t, 2), "rss_mb": round(cur, 0), "peak_mb": round(peak, 0)})
        self.t = now
        if self.progress:
            self.progress(name, frac)


def _edt(mask: np.ndarray, sp) -> np.ndarray:
    """float32 distance (mm) from each True voxel centre to the nearest False voxel centre; 0 on False voxels.

    Uses SimpleITK's Maurer transform (float32, ~4 B/voxel) instead of scipy's EDT (float64 plus int index arrays).
    Voxels beyond the array are not background, so a mask cut by the array edge is not eroded there."""
    if not (~mask).any():
        return np.full(mask.shape, np.float32(1e6))
    im = sitk.GetImageFromArray((~mask).view(np.uint8))
    im.SetSpacing(tuple(float(s) for s in sp[::-1]))
    d = sitk.GetArrayFromImage(sitk.SignedMaurerDistanceMap(im, insideIsPositive=True, squaredDistance=False,
                                                            useImageSpacing=True, backgroundValue=0))
    np.negative(d, out=d)
    np.maximum(d, 0, out=d)
    return d


def _dilate(mask, sp, r):
    """Voxels within r mm of the mask. Computed on the mask's bounding box grown by r: no voxel outside that box can
    be within r of the mask, so the result is identical to thresholding a whole-array distance map."""
    out = np.zeros(mask.shape, bool)
    if mask.any():
        bb = _bbox(mask, [int(np.ceil(r / s)) + 1 for s in sp])
        out[bb] = _edt(~mask[bb], sp) <= r
    return out


def _erode(mask, sp, r):
    """Mask voxels deeper than r mm. Computed on the bounding box plus one voxel: that one-voxel shell is empty, and
    for a voxel inside a box the nearest empty voxel outside it is never closer than the shell, so the result is
    identical to the whole-array map (array faces are not background, exactly as in _edt)."""
    out = np.zeros(mask.shape, bool)
    if mask.any():
        bb = _bbox(mask, 1)
        out[bb] = _edt(mask[bb], sp) > r
    return out


def _dist_to(mask, sp, rmax):
    """float32 distance (mm) to the nearest mask voxel, exact wherever it is <= rmax and 1e6 beyond the mask's
    bounding box grown by rmax (where it is certainly > rmax)."""
    out = np.full(mask.shape, np.float32(1e6))
    if mask.any():
        bb = _bbox(mask, [int(np.ceil(rmax / s)) + 1 for s in sp])
        out[bb] = _edt(~mask[bb], sp)
    return out


def _close(mask, sp, r):
    return _erode(_dilate(mask, sp, r), sp, r) | mask


def _open(mask, sp, r):
    return _dilate(_erode(mask, sp, r), sp, r) & mask


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Hole filling by labelling the background (6-connected) and keeping components that touch the array border.

    One labelling pass instead of scipy's iterative binary_fill_holes, which is very slow on large volumes."""
    lab, n = ndi.label(~mask)
    if n == 0:
        return mask.copy()
    border = np.zeros(n + 1, bool)
    for ax in range(mask.ndim):
        for i in (0, -1):
            border[np.unique(np.take(lab, i, axis=ax))] = True
    border[0] = True
    return ~border[lab] | mask


def _fill_capped(mask: np.ndarray) -> np.ndarray:
    """3D hole filling where cavities cut open by the array faces are closed by a lid (2D fill of the face slice).

    Plain 3D fill fails for a marrow canal leaving the scan: the cavity touches the boundary and counts as outside."""
    p = np.pad(mask, 1)
    for ax in range(3):
        for src, dst in ((1, 0), (-2, -1)):
            sl_s = [slice(None)] * 3
            sl_d = [slice(None)] * 3
            sl_s[ax], sl_d[ax] = src, dst
            p[tuple(sl_d)] = _fill_holes(p[tuple(sl_s)])
    f = _fill_holes(p)[1:-1, 1:-1, 1:-1]
    return f | mask


def _fill_2d_any(mask: np.ndarray, max_passes: int = 50) -> np.ndarray:
    """Per-slice 2D hole filling along each of the three array axes, repeated until nothing changes (catches canals
    open at one end).

    One pass is not enough: a voxel stays empty only when its slice is open along all three axes, so a single pass
    leaves the product of the open rows and open columns, a rectangular prism, inside an otherwise filled cavity.
    Once the cavity around it is filled the prism is ringed in-plane, and the next pass closes it. Each pass only
    adds voxels, so this stops; the result is the smallest superset of `mask` closed under per-slice filling."""
    out = mask.copy()
    for _ in range(max_passes):
        before = int(out.sum())
        for ax in range(3):
            o = np.moveaxis(out, ax, 0)
            for i in range(o.shape[0]):
                if o[i].any():
                    o[i] |= _fill_holes(o[i])
        if int(out.sum()) == before:
            break
    return out


def _components(mask: np.ndarray, conn: int = 3) -> tuple[np.ndarray, int]:
    st = ndi.generate_binary_structure(3, 1 if conn == 1 else 3)
    lab, n = ndi.label(mask, structure=st)
    return lab, n


def _keep_big(mask, vox_mm3, min_mm3):
    lab, n = _components(mask)
    if n == 0:
        return mask, 0, 0
    cnt = np.bincount(lab.ravel())
    keep = cnt * vox_mm3 >= min_mm3
    keep[0] = False
    return keep[lab], int(keep.sum()), int(n - keep.sum())


def _hist_peaks(v: np.ndarray, lo: float, hi: float, bin_hu: float = 5.0, smooth_hu: float = 15.0):
    """Smoothed histogram of v within [lo, hi): (centres, counts)."""
    edges = np.arange(lo, hi + bin_hu, bin_hu)
    h, _ = np.histogram(v, bins=edges)
    h = ndi.gaussian_filter1d(h.astype(float), smooth_hu / bin_hu)
    return (edges[:-1] + edges[1:]) / 2, h


def _mode(v, lo, hi, bin_hu=5.0, smooth_hu=15.0):
    c, h = _hist_peaks(v, lo, hi, bin_hu, smooth_hu)
    if h.max() <= 0:
        return None
    return float(c[np.argmax(h)])


def _otsu(v: np.ndarray, lo: float, hi: float, bin_hu: float = 5.0) -> float:
    edges = np.arange(lo, hi + bin_hu, bin_hu)
    h, _ = np.histogram(np.clip(v, lo, hi - 1e-3), bins=edges)
    c = (edges[:-1] + edges[1:]) / 2
    w = np.cumsum(h)
    m = np.cumsum(h * c)
    wt, mt = w[-1], m[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sb = (mt * w / wt - m) ** 2 / (w * (wt - w))
    sb[~np.isfinite(sb)] = 0
    return float(c[np.argmax(sb)] + bin_hu / 2)


def _two_gauss(v: np.ndarray, m1: float, m2: float, iters: int = 60):
    """1D EM fit of two Gaussians (fat, lean) started at the two histogram modes; returns params and Bayes boundary."""
    v = v.astype(np.float32)
    mu = np.array([m1, m2], float)
    sd = np.array([25.0, 25.0])
    w = np.array([0.5, 0.5])
    for _ in range(iters):
        p = np.stack([w[k] / sd[k] * np.exp(-0.5 * ((v - mu[k]) / sd[k]) ** 2) for k in range(2)])
        r = p / np.maximum(p.sum(0), 1e-30)
        n = r.sum(1)
        w = n / n.sum()
        mu = (r * v).sum(1) / n
        sd = np.sqrt((r * (v - mu[:, None]) ** 2).sum(1) / n) + 1e-3
    x = np.linspace(min(mu), max(mu), 2001)
    p0 = w[0] / sd[0] * np.exp(-0.5 * ((x - mu[0]) / sd[0]) ** 2)
    p1 = w[1] / sd[1] * np.exp(-0.5 * ((x - mu[1]) / sd[1]) ** 2)
    cross = x[np.argmin(np.abs(np.log(p0 + 1e-300) - np.log(p1 + 1e-300)))]
    return {"mu": mu.round(1).tolist(), "sd": sd.round(1).tolist(), "w": w.round(3).tolist(), "boundary": float(cross)}


def _phys(image: sitk.Image, idx_zyx: np.ndarray) -> np.ndarray:
    """Physical LPS mm coordinates of array indices [z, y, x] (N x 3)."""
    o = np.array(image.GetOrigin())
    d = np.array(image.GetDirection()).reshape(3, 3)
    s = np.array(image.GetSpacing())
    return (np.asarray(idx_zyx, float)[:, ::-1] * s) @ d.T + o


def _sub_image(image: sitk.Image, sl: tuple[slice, ...]) -> sitk.Image:
    """A zero image with the geometry of the crop ``sl`` (array order) of ``image``."""
    size = [s.stop - s.start for s in sl][::-1]
    ref = sitk.Image([int(v) for v in size], sitk.sitkUInt8)
    ref.SetSpacing(image.GetSpacing())
    ref.SetDirection(image.GetDirection())
    ref.SetOrigin(image.TransformIndexToPhysicalPoint([int(s.start) for s in sl][::-1]))
    return ref


def _to_grid(mask_img: sitk.Image, ref: sitk.Image) -> np.ndarray:
    r = sitk.Resample(mask_img, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    return sitk.GetArrayFromImage(r).astype(bool)


# ------------------------------------------------------------------------------------------- padding and body

def _padding(a: np.ndarray, params: SegParams) -> tuple[float | None, dict]:
    """Detect a constant padding value (any scanner: -2048, -3024, -32768, ...) from value concentration."""
    sub = a[::4, ::4, ::4]
    low = sub[sub <= -1024]
    info: dict = {"method": "most frequent exact value <= -1024 HU on a 1/64 subsample"}
    if low.size == 0:
        return None, info
    vals, cnt = np.unique(low, return_counts=True)
    v, c = float(vals[np.argmax(cnt)]), int(cnt.max())
    frac = c / sub.size
    info.update(candidate=v, fraction=round(frac, 4))
    if frac >= params.pad_min_fraction and v < -1024:
        info["pad_value"] = v
        return v, info
    if v == -1024 and frac >= params.pad_min_fraction:
        info["note"] = "many voxels at exactly -1024: clipped air floor (treated as air, not padding)"
    return None, info


def _body(image: sitk.Image, a: np.ndarray, sp: np.ndarray, params: SegParams, pad_value):
    """Body mask on a crop. Returns (crop slices, body bool crop, t_body, info)."""
    info: dict = {}
    valid = a[::3, ::3, ::3]
    valid = valid[valid != pad_value] if pad_value is not None else valid.ravel()
    air = _mode(valid, -1100, -850)
    tissue = _mode(valid, -200, 120)
    if params.body_hu is not None:
        t_body, why = float(params.body_hu), "user value"
    elif air is not None and tissue is not None:
        t_body, why = float(np.clip((air + tissue) / 2, -700, -300)), "half-way between air and soft-tissue peaks"
    else:
        t_body, why = -500.0, "peaks not found: conventional -500 HU"
    info.update(air_peak_hu=air, soft_peak_hu=tissue, threshold_hu=round(t_body, 1), threshold_reason=why)

    # coarse grid: average HU in blocks of ~body_coarse_mm (padding counts as air)
    f = [max(1, int(round(params.body_coarse_mm / s))) for s in sp]  # array order
    img = image if pad_value is None else sitk.Threshold(image, pad_value + 1, 32767, -1000)
    coarse = sitk.BinShrink(img, f[::-1])
    del img
    ca = sitk.GetArrayFromImage(coarse)
    csp = np.array(coarse.GetSpacing()[::-1])
    cthr = ca > t_body
    opened = _open(cthr, csp, params.body_open_radius_mm)
    lab, n = _components(opened)
    cvox = float(np.prod(csp))
    kept, dropped = np.zeros(n + 1, bool), []
    if n:
        cnt = np.bincount(lab.ravel(), minlength=n + 1)
        med = ndi.median(ca, lab, index=np.arange(1, n + 1)) if n < 2000 else np.zeros(n)
        for i in range(1, n + 1):
            vol, m = cnt[i] * cvox, float(np.asarray(med)[i - 1])
            ok = vol >= params.min_body_mm3 and params.body_tissue_hu[0] <= m <= params.body_tissue_hu[1]
            kept[i] = ok
            if not ok and vol >= 100:
                dropped.append({"volume_cm3": round(vol / 1000, 2), "median_hu": round(m)})
    core = kept[lab]
    del lab, opened
    info["coarse_spacing_mm"] = [round(float(s), 3) for s in csp]
    info["pieces_kept"] = int(kept.sum())
    info["pieces_dropped_over_0.1cm3"] = dropped[:20]
    if not core.any():
        info["warning"] = "no body found"
        return None, None, t_body, info
    zone = _dilate(core, csp, params.body_margin_mm)
    info["thin_removed_by_opening_cm3"] = round(float((cthr & ~zone).sum()) * cvox / 1000, 1)
    del cthr, ca
    # crop = bounding box of the zone in full-resolution indices (+2 voxels)
    nz = np.nonzero(zone.any(axis=(1, 2)))[0], np.nonzero(zone.any(axis=(0, 2)))[0], np.nonzero(zone.any(axis=(0, 1)))[0]
    sl = tuple(slice(max(0, int(ix[0] * f[k]) - 2),
                     a.shape[k] if ix[-1] == zone.shape[k] - 1 else min(a.shape[k], int((ix[-1] + 1) * f[k]) + 2))
               for k, ix in enumerate(nz))

    def up(m):
        # each fine voxel takes the value of the coarse block it lies in; the last (incomplete) block of an axis,
        # which BinShrink drops, takes the value of the block before it so the body reaches the array face
        ix = [np.minimum(np.arange(q.start, q.stop) // f[k], m.shape[k] - 1) for k, q in enumerate(sl)]
        return m[np.ix_(*ix)]

    zone_f, core_f = up(zone), up(core)
    del zone, core
    thr = a[sl] > t_body
    body = thr & zone_f
    lab, n = _components(body)
    hit = np.unique(lab[core_f & body])
    keep = np.zeros(n + 1, bool)
    keep[hit] = True
    keep[0] = False
    body = keep[lab]
    del lab, core_f, zone_f
    gc.collect()
    vox = float(np.prod(sp))
    # skin check: threshold voxels connected to the body but cut away (cradle contact, thin protrusions)
    lab, n = _components(thr)
    hit = np.unique(lab[body])
    conn = np.isin(lab, hit[hit > 0]) & ~body
    del lab
    info["cut_away_touching_cm3"] = round(float(conn.sum()) * vox / 1000, 3)
    if conn.any():
        bb = _bbox(conn, int(np.ceil(15.0 / sp.min())))
        d_cut = _edt(~body[bb], sp)[conn[bb]]
        info["cut_away_depth_mm_p50_p95_max"] = [round(float(np.percentile(d_cut, q)), 2) for q in (50, 95, 100)]
    del conn, thr
    filled = _fill_capped(body)
    filled |= _fill_2d_votes(body)
    info["holes_filled_cm3"] = round(float((filled & ~body).sum()) * vox / 1000, 2)
    return sl, filled, t_body, info


def _fill_2d_votes(mask: np.ndarray, votes: int = 2) -> np.ndarray:
    """Voxels that are holes in per-slice 2D fills along >= `votes` of the three axes (tunnelled cavities)."""
    cnt = np.zeros(mask.shape, np.uint8)
    for ax in range(3):
        m = np.moveaxis(mask, ax, 0)
        c = np.moveaxis(cnt, ax, 0)
        for i in range(m.shape[0]):
            if m[i].any():
                c[i] += _fill_holes(m[i]) & ~m[i]
    return cnt >= votes


def body_mask(image: sitk.Image, params: SegParams = SegParams()) -> np.ndarray:
    """bool [z, y, x] full size: the patient without table, cradle, padding or air (internal holes filled)."""
    a = sitk.GetArrayViewFromImage(image)
    sp = np.array(image.GetSpacing()[::-1], float)
    pad, _ = _padding(a, params)
    sl, b, _, _ = _body(image, a, sp, params, pad)
    out = np.zeros(a.shape, bool)
    if sl is not None:
        out[sl] = b
    return out


# ------------------------------------------------------------------------------------------------------- metal

def _metal(h: np.ndarray, body: np.ndarray, sp: np.ndarray, params: SegParams, t_tail: float, hmax_scan: float,
           clipped: bool, body_near: np.ndarray | None = None):
    """Metal (bool), rim (bool) and info. Edge = per-object half-maximum between metal plateau and surroundings."""
    vox = float(np.prod(sp))
    info: dict = {}
    if clipped:
        t_seed = hmax_scan - 1
        info["seed_reason"] = "scale clipped: voxels at the clip value (metal and dense bone are not separable)"
    else:
        t_seed = max(params.metal_seed_floor_hu, t_tail + params.metal_seed_margin_hu)
        info["seed_reason"] = f"max({params.metal_seed_floor_hu:g} HU floor, cortical tail end {t_tail:g} + " \
                              f"{params.metal_seed_margin_hu:g})"
    info["seed_hu"] = float(t_seed)
    seeds = h >= t_seed
    if not seeds.any():
        info.update(present=False)
        return np.zeros(h.shape, bool), np.zeros(h.shape, bool), info
    lab, n = _components(seeds)
    cnt = np.bincount(lab.ravel(), minlength=n + 1)
    if body_near is None:
        body_near = _dilate(body, sp, 1.0)
    inb = np.bincount(lab.ravel(), weights=body_near.ravel(), minlength=n + 1)
    ok = (cnt * vox >= params.min_metal_mm3) & (inb > 0)
    ok[0] = False
    info["seed_components_outside_body"] = int(((inb == 0) & (cnt > 0))[1:].sum())
    info["seed_components_too_small"] = int(((cnt * vox < params.min_metal_mm3) & (inb > 0))[1:].sum())
    seeds = ok[lab]
    del lab
    if not seeds.any():
        info.update(present=False)
        return np.zeros(h.shape, bool), np.zeros(h.shape, bool), info
    # per-object half maximum (shell statistics from ONE distance map; objects closer than 3 mm share shells)
    slab, n = _components(seeds)
    d_seed = _dist_to(seeds, sp, 3.0)
    metal = np.zeros(h.shape, bool)
    objs = ndi.find_objects(slab)
    comps = []
    pv = [int(np.ceil(4.0 / s)) for s in sp]
    for i, o in enumerate(objs, 1):
        bb = tuple(slice(max(0, s.start - p), min(dim, s.stop + p)) for s, p, dim in zip(o, pv, h.shape))
        hs = h[bb]
        sd = slab[bb] == i
        plateau = float(np.median(hs[sd]))
        shell = (d_seed[bb] > 1.5) & (d_seed[bb] <= 3.0) & body[bb]
        bg = float(np.median(hs[shell])) if shell.any() else 0.0
        t_edge = t_seed if clipped else max((plateau + bg) / 2, t_tail)
        gl, _ = _components(hs >= t_edge)
        ids = np.unique(gl[sd])
        obj = np.isin(gl, ids[ids > 0])
        del gl
        metal[bb] |= obj
        comps.append({"plateau_hu": round(plateau), "surround_hu": round(bg), "edge_hu": round(t_edge),
                      "volume_mm3": round(float(obj.sum()) * vox, 1), "_bb": bb, "_bg": bg, "_pl": plateau})
    del d_seed
    d_out = _dist_to(metal, sp, 3.5)  # used up to 3.25 mm (edge profile) below
    for c in comps:  # partial-volume volume: integral of the normalised excess density within 2 mm of the object
        bb = c.pop("_bb")
        bg, pl = c.pop("_bg"), c.pop("_pl")
        near = d_out[bb] <= 2.0
        frac = np.clip((h[bb][near].astype(np.float32) - bg) / max(pl - bg, 1.0), 0, 1)
        c["partial_volume_mm3"] = round(float(frac.sum()) * vox, 1)
    del slab
    lab, n = _components(metal)
    cnt = np.bincount(lab.ravel(), minlength=n + 1)
    info["present"] = True
    info["components"] = int(n)
    info["volume_mm3"] = round(float(metal.sum()) * vox, 1)
    info["per_seed_object"] = comps[:30]
    info["partial_volume_mm3"] = round(sum(c["partial_volume_mm3"] for c in comps), 1)
    info["partial_volume_note"] = ("sum of (HU - surround) / (plateau - surround) within 2 mm of each object: blur "
                                   "conserves this integral, so it is not inflated by blooming (streaks bias it)")
    # geometry of each component (array-axis independent: principal axes in mm)
    geo = []
    for i in np.argsort(cnt[1:])[::-1][:12] + 1:
        idx = np.argwhere(lab == i) * sp
        c = idx - idx.mean(0)
        ev = np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(c.T)) if len(c) > 3 else np.zeros(3), 0))[::-1]
        proj = c @ np.linalg.eigh(np.cov(c.T))[1][:, ::-1] if len(c) > 3 else c
        ext = (proj.max(0) - proj.min(0)) if len(c) else np.zeros(3)
        geo.append({"volume_mm3": round(float(cnt[i]) * vox, 1), "extent_mm_principal": [round(float(e), 1) for e in ext],
                    "sd_mm_principal": [round(float(e), 2) for e in ev]})
    info["component_geometry"] = geo
    # blooming estimate: median HU vs distance outside the metal edge
    prof = []
    for r0 in np.arange(0.0, 3.01, 0.25):
        sel = (d_out > r0) & (d_out <= r0 + 0.25) & body
        if sel.sum() > 20:
            prof.append((round(float(r0 + 0.125), 3), round(float(np.median(h[sel])))))
    info["edge_profile_mm_hu"] = prof
    halo = 0.0
    for r, v in prof:
        if v >= t_tail:
            halo = r + 0.125
        else:
            break
    rim_mm = float(np.clip(max(halo, float(np.max(sp))), float(np.max(sp)), params.metal_rim_max_mm))
    info["bloom_halo_mm"] = round(halo, 2)
    info["bloom_note"] = ("distance outside the half-maximum edge over which the median HU stays above the cortical tail "
                          "end; an under-shoot ring (dark) right outside the edge means the halo is reconstructed as a "
                          "dark ring instead")
    info["rim_mm"] = round(rim_mm, 2)
    rim = (d_out <= rim_mm) & ~metal & body
    del d_out
    info["rim_volume_mm3"] = round(float(rim.sum()) * vox, 1)
    return metal, rim, info


# -------------------------------------------------------------------------------------------------------- bone

def _bone_thresholds(h: np.ndarray, body: np.ndarray, params: SegParams) -> dict:
    """Candidate bone thresholds from the in-body histogram; returns the chosen low/high and all candidates."""
    v = h[body][::7].astype(np.float32)
    lean = _mode(v, -50, 150)
    lean = 40.0 if lean is None else lean
    # noise: robust sd of voxels near the lean peak (lower half-width at half maximum, unaffected by bone/fat tails)
    c, hh = _hist_peaks(v, lean - 200, lean + 1, 2.0, 4.0)
    half = hh.max() / 2
    below = c[(hh < half) & (c < lean)]
    hwhm = lean - (below.max() if below.size else lean - 30)
    sigma = float(hwhm / 1.1774)
    cort = _mode(v, 700, 2600, 10.0, 60.0)
    cort = 1500.0 if cort is None else cort
    cand = {"lean_peak_hu": lean, "lean_noise_sd_hu": round(sigma, 1), "cortical_peak_hu": cort}
    cand["half_max_soft_cortical"] = round((lean + cort) / 2, 1)
    cand["otsu_in_body_minus200_to_2500"] = round(_otsu(v[(v > -200)], -200, 2500), 1)
    cc, hc = _hist_peaks(v, lean, cort, 10.0, 40.0)
    cand["valley_soft_cortical"] = float(cc[np.argmin(hc)])
    cand["noise_floor_lean_plus_k_sd"] = round(lean + params.bone_noise_k * sigma, 1)
    # cortical tail end: first bin above the cortical peak where the histogram drops below 1 % of the peak
    ct, ht = _hist_peaks(v, cort, 6000, 25.0, 50.0)
    pk = ht[0] if ht.size else 0
    below = ct[ht < 0.01 * pk] if pk > 0 else ct
    cand["cortical_tail_end_hu"] = float(below[0]) if below.size else float(cort + 1000)
    hi = params.bone_high_hu if params.bone_high_hu is not None else cand["half_max_soft_cortical"]
    if params.bone_low_hu is not None:
        lo, why = float(params.bone_low_hu), "user value"
    else:
        lo = max(cand["noise_floor_lean_plus_k_sd"], 150.0)
        why = (f"max(lean peak + {params.bone_noise_k:g} noise sd, 150 HU): the lowest level at which soft-tissue noise "
               "does not grow into bone; thin cortex and trabecular metaphyses are kept by hysteresis from seeds at the "
               "soft/cortical half-maximum")
    cand.update(low_hu=round(float(lo), 1), high_hu=round(float(max(hi, lo)), 1), low_reason=why,
                high_reason="half-maximum between lean and cortical peaks (surface of thick cortex)")
    return cand


def _bone_low(h, body, metal, rim, sp, t_low, t_high, params, smooth_mm=0.0):
    """Hysteresis bone mask (bool crop) without metal and rim, small blobs removed."""
    if smooth_mm > 0:
        im = sitk.GetImageFromArray(h.astype(np.float32))
        im.SetSpacing(tuple(float(s) for s in sp[::-1]))
        hs = sitk.GetArrayFromImage(sitk.SmoothingRecursiveGaussian(im, smooth_mm))
        del im
    else:
        hs = h
    cand = (hs >= t_low) & body & ~metal & ~rim
    if params.skin_bone_gap_mm > 0:
        # nothing within this depth below the skin is bone: a sharp kernel's overshoot at the skin/air edge reaches
        # bone HU on coarse reconstructions. Air only (> -1500 HU): padding and a cut field of view do not count.
        air = ~body & (h > -1500)
        if air.any():
            cand &= ~_dilate(air, sp, params.skin_bone_gap_mm)
        del air
    seeds = (hs >= t_high) & cand
    del hs
    lab, n = _components(cand)
    good = np.zeros(n + 1, bool)
    good[np.unique(lab[seeds])] = True
    good[0] = False
    cnt = np.bincount(lab.ravel(), minlength=n + 1)
    vox = float(np.prod(sp))
    good &= cnt * vox >= params.min_bone_mm3
    out = good[lab]
    return out


def bone_mask(image: sitk.Image, body: np.ndarray, params: SegParams = SegParams(),
              threshold_hu: float | None = None) -> np.ndarray:
    """bool [z, y, x] full size: thresholded bone (hysteresis; low threshold = ``threshold_hu`` when given).

    Metal and its blooming rim are excluded. Monotone: a higher threshold never adds voxels."""
    a = sitk.GetArrayViewFromImage(image)
    sp = np.array(image.GetSpacing()[::-1], float)
    out = np.zeros(a.shape, bool)
    if not body.any():
        return out
    sl = _bbox(body, 3)
    h = np.asarray(a[sl])
    b = body[sl]
    th = _bone_thresholds(h, b, params)
    clipped, hmax = _scale(a)
    metal, rim, _ = _metal(h, b, sp, params, th["cortical_tail_end_hu"], hmax, clipped)
    lo = th["low_hu"] if threshold_hu is None else float(threshold_hu)
    hi = max(th["high_hu"], lo)
    out[sl] = _bone_low(h, b, metal, rim, sp, lo, hi, params, params.bone_smooth_mm)
    return out


def _bbox(mask, margin=0):
    """Bounding box of a non-empty mask as slices, grown by `margin` voxels (one number or one per axis)."""
    mg = [int(margin)] * 3 if np.isscalar(margin) else [int(v) for v in margin]
    ix = [np.nonzero(mask.any(axis=tuple(j for j in range(3) if j != k)))[0] for k in range(3)]
    return tuple(slice(max(0, int(i[0]) - mg[k]), min(mask.shape[k], int(i[-1]) + 1 + mg[k])) for k, i in enumerate(ix))


def _scale(a) -> tuple[bool, float]:
    """(clipped, max HU): a scale is clipped when its maximum sits at 3071 / 4095 with many voxels on it."""
    hmax = float(a.max())
    clipped = False
    if hmax in (3071.0, 3072.0, 4095.0):
        clipped = int((a[::2, ::2, ::2] == hmax).sum()) >= 10
    return clipped, hmax


def _separate(h, B, metal, sp, t_high, params, cort_peak):
    """Split the bone mask into bones.

    1. solid = closing (close_mm) + capped 3D fill of bone (+ metal touching it), so marrow is part of each bone;
    2. markers = regional maxima of the h-maxima transform of the solid's distance map (one per thick part;
       Vincent & Soille 1991, Soille 2003 ch. 6), h = split_h_mm, computed on a ~marker_grid_mm grid (markers only
       need to sit inside each part; the full-resolution h-maxima reconstruction took ~5 min on a forearm);
    3. watershed from these markers on inverted HU, so region borders settle in the darkest surface between markers
       (joint spaces, fracture gaps);
    4. adjacent regions are merged unless their shared border is a density valley: the 90th percentile HU on the
       border (metal excluded) below ``merge_contrast`` x the smaller of the two regions' own 90th percentiles.
       Inside one bone the border crosses continuous compact bone (a full cortical ring), so it is not a valley;
       between bones in contact it runs through the joint gap / partial volume;
    5. regions with almost no surface facing outside the solid (interior islands) join their main neighbour."""
    vox = float(np.prod(sp))
    info: dict = {}
    mt = metal & _dilate(B, sp, 2.0)
    solid = _fill_capped(_close(B | mt, sp, params.close_mm))
    mk, n = _markers(solid, sp, params)
    info["markers"] = int(n)
    if n == 0:
        return np.zeros(h.shape, np.int32), info
    feat = np.clip(t_high - h.astype(np.float32), 0, None)
    feat[~solid] = feat.max() + 1
    fi = sitk.GetImageFromArray(feat)
    del feat
    mi = sitk.GetImageFromArray(mk.astype(np.uint32))
    del mk
    ws = sitk.MorphologicalWatershedFromMarkers(fi, mi, markWatershedLine=False, fullyConnected=True)
    del fi, mi
    reg = sitk.GetArrayFromImage(ws).astype(np.int32)
    del ws
    reg[~solid] = 0
    reg, pairs = _merge_by_contrast(reg, h, metal, params.merge_contrast)
    info["borders"] = pairs[:40]
    info["borders_note"] = ("border p90 HU / min(region p90 HU); merged when >= merge_contrast "
                            f"({params.merge_contrast:g}); 'area_mm2' counts voxel faces")
    reg, merged = _merge_islands(reg, solid, params.island_exposure)
    info["islands_merged"] = merged
    ids = np.unique(reg)
    info["regions"] = int((ids > 0).sum())
    del solid
    return reg, info


def _markers(solid: np.ndarray, sp, params) -> tuple[np.ndarray, int]:
    """Watershed markers: h-maxima of the distance map of `solid` on a coarse grid, mapped back (int32, count)."""
    f = [max(1, int(round(params.marker_grid_mm / s))) for s in sp]
    si = sitk.GetImageFromArray(solid.view(np.uint8) * np.uint8(100))
    si.SetSpacing(tuple(float(s) for s in sp[::-1]))
    cs = sitk.BinShrink(si, f[::-1]) > 50
    ca = sitk.GetArrayFromImage(cs).astype(bool)
    if not ca.any():
        lab, n = _components(solid)
        return lab, n
    dc = _edt(ca, np.array(cs.GetSpacing()[::-1]))
    di = sitk.GetImageFromArray(dc)
    di.CopyInformation(cs)
    rm = sitk.RegionalMaxima(sitk.HMaxima(di, float(params.split_h_mm)), backgroundValue=0, foregroundValue=1,
                             fullyConnected=True, flatIsMaxima=True)
    mkc, n = _components(sitk.GetArrayFromImage(rm).astype(bool) & ca)
    mi = sitk.GetImageFromArray(mkc.astype(np.uint32))
    mi.CopyInformation(cs)
    ref = sitk.Image(si.GetSize(), sitk.sitkUInt32)
    ref.CopyInformation(si)
    mk = sitk.GetArrayFromImage(sitk.Resample(mi, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0,
                                              sitk.sitkUInt32)).astype(np.int32)
    mk[~solid] = 0
    # solid pieces that received no marker (smaller than a coarse voxel) get their own
    lab, nl = _components(solid)
    has = np.zeros(nl + 1, bool)
    has[np.unique(lab[mk > 0])] = True
    miss = np.flatnonzero(~has[1:]) + 1
    if miss.size:
        lut = np.zeros(nl + 1, np.int32)
        lut[miss] = np.arange(n + 1, n + 1 + miss.size)
        add = lut[lab]
        mk = np.where(add > 0, add, mk)
        n += int(miss.size)
    return mk, int(n)


def _local_fraction(ind: np.ndarray, valid: np.ndarray, sp, sigma_mm: float) -> np.ndarray:
    """Gaussian-weighted fraction of `ind` among `valid` voxels (computed on a grid of ~sigma/2, mapped back)."""
    f = [max(1, int(round(sigma_mm / 2 / s))) for s in sp]
    pads = [(0, (-n) % k) for n, k in zip(ind.shape, f)]

    def block(m):
        m = np.pad(m.astype(np.float32), pads)
        sh = [n // k for n, k in zip(m.shape, f)]
        return m.reshape(sh[0], f[0], sh[1], f[1], sh[2], f[2]).mean(axis=(1, 3, 5))
    cs = np.array(sp) * f
    sig = [sigma_mm / c for c in cs]
    num = ndi.gaussian_filter(block(ind & valid), sig)
    den = ndi.gaussian_filter(block(valid), sig)
    fr = num / np.maximum(den, 1e-3)
    for ax, k in enumerate(f):
        fr = np.repeat(fr, k, axis=ax)
    return fr[: ind.shape[0], : ind.shape[1], : ind.shape[2]]


def _streak_spurs(h, B, body, metal, sp, params, dark_hu) -> tuple[np.ndarray, dict]:
    """Thin bright spurs of the bone mask inside the metal streak zone (removed so streaks do not become bone).

    Zone: body voxels within artefact_radius_mm of metal where >= artefact_density of the neighbourhood is darker
    than `dark_hu` (no tissue is that dark: dark streaks mark where the projection data were corrupted). Spurs: bone
    voxels there that an opening of radius ~one voxel removes and that lie > 1 mm from the opened bone."""
    info: dict = {}
    if not metal.any():
        return np.zeros(B.shape, bool), {"zone_cm3": 0.0}
    vox = float(np.prod(sp))
    near = _dilate(metal, sp, params.artefact_radius_mm)
    zone = body & near & (_local_fraction(h < dark_hu, body & ~B, sp, params.artefact_sigma_mm) >= params.artefact_density)
    info["dark_hu"] = round(float(dark_hu), 1)
    info["zone_cm3"] = round(float(zone.sum()) * vox / 1000, 2)
    if not zone.any():
        return np.zeros(B.shape, bool), info
    bb = _bbox(zone, int(np.ceil(3.0 / sp.min())))
    r = float(np.max(sp)) * 1.05
    op = _open(B[bb], sp, r)
    spur = np.zeros(B.shape, bool)
    spur[bb] = B[bb] & zone[bb] & (_edt(~op, sp) > 1.0)
    info["spur_cm3"] = round(float(spur.sum()) * vox / 1000, 3)
    info["zone"] = zone  # popped by the caller
    return spur, info


def _merge_by_contrast(reg, h, metal, thr):
    """Union-find merge of adjacent watershed regions whose shared border is not a density valley."""
    n = int(reg.max())
    if n < 2:
        return reg, []
    p90 = np.zeros(n + 1)
    ok = (reg > 0) & ~metal
    for i, v in _group_values(reg[ok], h[ok]):
        p90[i] = np.percentile(v, 90)
    border: dict[tuple[int, int], list] = {}
    for ax in range(3):
        sa = [slice(None)] * 3
        sb = [slice(None)] * 3
        sa[ax], sb[ax] = slice(0, -1), slice(1, None)
        ra, rb = reg[tuple(sa)], reg[tuple(sb)]
        m = (ra > 0) & (rb > 0) & (ra != rb) & ~metal[tuple(sa)] & ~metal[tuple(sb)]
        if not m.any():
            continue
        lo, hi = np.minimum(ra[m], rb[m]), np.maximum(ra[m], rb[m])
        val = 0.5 * (h[tuple(sa)][m].astype(np.float32) + h[tuple(sb)][m].astype(np.float32))
        key = lo.astype(np.int64) * (n + 1) + hi
        for kk, v in _group_values(key, val):
            border.setdefault((int(kk // (n + 1)), int(kk % (n + 1))), []).append(v)
    parent = list(range(n + 1))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    pairs = []
    for (i, j), vs in sorted(border.items(), key=lambda t: -sum(len(v) for v in t[1])):
        v = np.concatenate(vs)
        ref = max(min(p90[i], p90[j]), 1.0)
        c = float(np.percentile(v, 90)) / ref
        merge = c >= thr and v.size >= 3
        pairs.append({"regions": [i, j], "area_mm2_faces": int(v.size), "border_p90_hu": round(float(np.percentile(v, 90))),
                      "region_p90_hu": [round(p90[i]), round(p90[j])], "contrast": round(c, 3), "merged": bool(merge)})
        if merge:
            parent[find(i)] = find(j)
    lut = np.array([find(i) for i in range(n + 1)], np.int32)
    return lut[reg], pairs


def _group_values(keys: np.ndarray, vals: np.ndarray):
    """Yield (key, values) for each distinct key (one sort, no Python loop over voxels)."""
    if keys.size == 0:
        return
    o = np.argsort(keys, kind="stable")
    k, v = keys[o], vals[o]
    cut = np.flatnonzero(np.diff(k)) + 1
    for kk, vv in zip(k[np.r_[0, cut]], np.split(v, cut)):
        yield kk, vv


def _merge_islands(reg, solid, thr):
    merged = 0
    for _ in range(5):
        ids = np.unique(reg)
        ids = ids[ids > 0]
        if len(ids) < 2:
            break
        # surface voxels of each region and what they face (6-neighbourhood)
        changes = {}
        objs = ndi.find_objects(reg)
        for i in ids:
            o = objs[i - 1]
            if o is None:
                continue
            bb = tuple(slice(max(0, s.start - 1), s.stop + 1) for s in o)
            r = reg[bb]
            m = r == i
            ring = ndi.binary_dilation(m) & ~m
            faces = r[ring]
            ext = int((faces == 0).sum())
            nb = faces[(faces != 0) & (faces != i)]
            tot = ext + nb.size
            if tot == 0 or nb.size == 0:
                continue
            if ext / tot < thr:
                vals, c = np.unique(nb, return_counts=True)
                changes[i] = int(vals[np.argmax(c)])
        if not changes:
            break
        for i, j in changes.items():
            while j in changes and changes[j] != i:
                j = changes[j]
            reg[reg == i] = j
            merged += 1
    return reg, merged


def _pockets(c: np.ndarray, f3: np.ndarray, sp, max_exposure: float) -> tuple[np.ndarray, list[dict]]:
    """Cavities of the closed bone `c` that the 3D fill `f3` leaves open but per-slice 2D filling closes.

    Each connected cavity is kept (returned True) when the open share of its surface is <= max_exposure: faces
    towards bone or the 3D-filled interior are closed, faces towards anything else are open, faces on the array
    boundary count as closed (the same lid rule as _fill_capped). Areas are in mm2, so voxel shape does not matter."""
    cand = _fill_2d_any(c)
    cand &= ~f3
    rows: list[dict] = []
    if not cand.any():
        return cand, rows
    lab, n = _components(cand, conn=1)
    area = (sp[1] * sp[2], sp[0] * sp[2], sp[0] * sp[1])
    opened, closed = np.zeros(n + 1), np.zeros(n + 1)
    for ax in range(3):
        lo, hi = [slice(None)] * 3, [slice(None)] * 3
        lo[ax], hi[ax] = slice(0, -1), slice(1, None)
        for here, there in ((tuple(lo), tuple(hi)), (tuple(hi), tuple(lo))):
            la, lb = lab[here], lab[there]
            face = (la > 0) & (lb == 0)  # 6-connected components: a neighbouring cavity voxel is the same cavity
            wall = f3[there]
            closed += np.bincount(la[face & wall], minlength=n + 1) * area[ax]
            opened += np.bincount(la[face & ~wall], minlength=n + 1) * area[ax]
        for edge in (0, -1):  # array boundary: a lid
            closed += np.bincount(np.take(lab, edge, axis=ax).ravel(), minlength=n + 1) * area[ax]
    expo = opened / np.maximum(opened + closed, 1e-9)
    keep = expo <= max_exposure
    keep[0] = False
    vol = np.bincount(lab.ravel(), minlength=n + 1) * float(np.prod(sp))
    for i in np.argsort(vol[1:])[::-1][:8] + 1:
        if vol[i] >= 20.0:  # report cavities of at least 20 mm3
            rows.append({"volume_mm3": round(float(vol[i]), 1), "open_share": round(float(expo[i]), 3),
                         "filled": bool(keep[i])})
    return keep[lab], rows


def _envelope(m: np.ndarray, sp, r_close: float, max_exposure: float = 0.15,
              ball_mm: float = 0.75) -> tuple[np.ndarray, list[dict]]:
    """Solid periosteal envelope of ONE bone mask m, and the list of slice-closed cavities that were judged.

    A closing (radius r_close) plugs foramina, screw holes, cortical gaps and intertrabecular spaces; holes are then
    filled in 3D (lids at the array faces). A canal open at a fracture or at one end is not a 3D hole but is still a
    closed ring across the bone, so cavities closed by per-slice 2D filling along any axis are added when they are
    tunnel-like (see _pockets); a fossa that only looks ringed in one slice direction stays outside the bone.
    Voxels that only the closing added and that lie within r_close of the filled surface are given back when a
    ball of radius `ball_mm` reaches them from outside the bone through non-bone space: the layer the closing
    smeared into a concavity is returned, so a concave bone surface is not swallowed (joint gaps cannot be bridged:
    m holds one bone only). Shallow voxels no such ball reaches stay bone: the seam under a thin cortex that
    thresholding left dotted, and trabecular pores just under a porous cortex, connect to the outside only through
    gaps narrower than the ball. Pores left fully enclosed are bone interior."""
    c = _close(m, sp, r_close)
    f = _fill_capped(c)
    pk, rows = _pockets(c, f, sp, max_exposure)
    f |= pk
    shallow = c & ~m & ~(_edt(f, sp) > r_close)
    if shallow.any():
        lab, n = _components(_open(~m, sp, ball_mm), conn=1)
        outside = np.zeros(n + 1, bool)
        outside[np.unique(lab[~f])] = True  # components of ball-reachable space that touch the outside of f
        outside[0] = False
        f &= ~(shallow & outside[lab])
    return _fill_holes(f), rows


def _envelopes(reg, B, metal, body, sp, params) -> tuple[np.ndarray, dict]:
    """int32 envelope label per watershed region, larger bones first; a voxel is never given to two bones.

    An envelope may take any voxel that is not another region's thresholded bone or metal: watershed basins also
    hold marrow, and a basin seeded inside one bone's marrow must not keep that marrow out of the bone around it.
    Whatever a bone's envelope then encloses completely (dense specks, such basins) becomes part of that bone."""
    env_lab = np.zeros(reg.shape, np.int32)
    objs = ndi.find_objects(reg)
    nb = np.bincount(reg[B], minlength=int(reg.max()) + 1)
    nb[0] = 0
    order = [int(i) for i in np.argsort(-nb, kind="stable") if nb[i] > 0]
    pv = [int(np.ceil((params.close_mm + 1) / s)) + 1 for s in sp]
    hard = B | metal
    info: dict = {"cavities": [], "absorbed_regions": 0}
    for i in order:
        o = objs[i - 1]
        bb = tuple(slice(max(0, s.start - p), min(d, s.stop + p)) for s, p, d in zip(o, pv, reg.shape))
        r = reg[bb] == i
        e, rows = _envelope(hard[bb] & r, sp, params.close_mm, params.pocket_exposure, params.give_back_ball_mm)
        e &= body[bb] & ~(hard[bb] & ~r)
        el = env_lab[bb]
        el[e & (el == 0)] = i
        info["cavities"] += [{"region": i, **row} for row in rows]
    eobjs = ndi.find_objects(env_lab)
    for i in order:
        o = eobjs[i - 1] if i - 1 < len(eobjs) else None
        if o is None:
            continue
        bb = tuple(slice(max(0, s.start - 1), min(d, s.stop + 1)) for s, d in zip(o, reg.shape))
        el = env_lab[bb]
        ei = el == i
        if not ei.any():  # this region was itself enclosed by a larger bone
            continue
        inside = _fill_holes(ei) & ~ei & body[bb]
        if inside.any():
            info["absorbed_regions"] += int(np.setdiff1d(np.unique(el[inside]), [0, i]).size)
            el[inside] = i
    info["cavities"] = sorted(info["cavities"], key=lambda q: -q["volume_mm3"])[:30]
    info["cavity_note"] = (f"cavities closed only by slice-wise filling; filled when open_share <= "
                           f"{params.pocket_exposure:g} (share of the cavity surface not facing bone)")
    return env_lab, info


def _local_surface(h, env, metal, sp, lean, t_tail, params, t_low):
    """Voxels to remove from the outer envelope layer: HU below the local half-maximum (lean + local max) / 2."""
    depth = max(params.surface_layer_mm, 1.05 * float(np.max(sp)))  # at least one voxel on coarse grids
    layer = env & (_edt(_fill_holes(env), sp) <= depth)  # interior gaps are not surface
    hm = np.where(env & ~metal, np.minimum(h, t_tail), -1024).astype(np.int16)
    size = [2 * max(1, int(round(params.local_max_mm / s))) + 1 for s in sp]
    lmax = ndi.maximum_filter(hm, size=size)
    del hm
    lmax = lmax.astype(np.float32)
    t_loc = (lean + lmax) / 2
    cand = layer & ~metal & (h >= t_low)  # only thresholded halo voxels; filled marrow is never peeled
    peel = cand & (h < t_loc)
    vox = float(np.prod(sp))
    n_peel = int(peel.sum())
    # How much the delivered bone volume changes if the edge is put at another fraction of the way from lean tissue
    # to the local maximum (0.5 = half-maximum). This is the decision that sets the delivered outer surface; the
    # global threshold t_low only decides which voxels can be bone at all.
    sens = {}
    for fr in EDGE_FRACTIONS:
        n = int((cand & (h < lean + fr * (lmax - lean))).sum())
        sens[f"{fr:g}"] = round((n_peel - n) * vox / 1000, 4)
    del cand, lmax
    info = {"method": "local half-maximum (Hangartner 2007) on the outer layer",
            "layer_cm3": round(float(layer.sum()) * vox / 1000, 3), "removed_cm3": round(n_peel * vox / 1000, 3),
            "median_local_threshold_hu": round(float(np.median(t_loc[layer])), 1) if layer.any() else None,
            "edge_fraction_volume_change_cm3": sens}
    return peel, info


def _inpaint(v: np.ndarray, fix: np.ndarray, good: np.ndarray, sp, sigma_mm: float, default: float) -> np.ndarray:
    """Replace v at `fix` by a Gaussian-weighted mean of v over `good` (normalised convolution, Knutsson & Westin
    1993); voxels with no good neighbour within ~3 sigma get `default`. Works on the bounding box of `fix`."""
    if not fix.any():
        return v
    bb = _bbox(fix, int(np.ceil(3 * sigma_mm / sp.min())))
    sig = [sigma_mm / s for s in sp]
    w = good[bb].astype(np.float32)
    num = ndi.gaussian_filter(np.where(good[bb], v[bb], 0).astype(np.float32), sig)
    den = ndi.gaussian_filter(w, sig)
    f = fix[bb]
    sub = v[bb]
    sub[f] = np.where(den[f] > 1e-3, num[f] / np.maximum(den[f], 1e-3), default)
    return v


# -------------------------------------------------------------------------------------------------- segment()

def segment(image: sitk.Image, soft: sitk.Image | None = None, params: SegParams = SegParams(),
            progress: Callable[[str, float], None] | None = None) -> SegResult:
    """Label a CT: 0 outside, 1 fat, 2 lean, 4 metal, 10+2k / 11+2k cortical / medullary of bone k (see labels.py)."""
    clock = _Clock(progress)
    info: dict = {"params": asdict(params), "flags": []}
    flags = info["flags"]
    a = sitk.GetArrayViewFromImage(image)
    sp = np.array(image.GetSpacing()[::-1], float)
    vox = float(np.prod(sp))
    info["grid"] = {"size_xyz": list(image.GetSize()), "spacing_xyz_mm": [round(s, 4) for s in image.GetSpacing()],
                    "direction": [round(d, 4) for d in image.GetDirection()]}
    out = np.zeros(a.shape, np.uint8)

    def result(names):
        lab = sitk.GetImageFromArray(out)
        lab.CopyInformation(image)
        info["timing"] = clock.steps
        info["total_seconds"] = round(time.perf_counter() - clock.t0, 1)
        return SegResult(lab, names, info)

    # 1. padding, scale
    pad, info["padding"] = _padding(a, params)
    clipped, hmax = _scale(a)
    info["scale"] = {"max_hu": hmax, "min_hu": float(a.min()), "clipped": clipped,
                     "extended": hmax > 3200}
    if clipped:
        flags.append(f"HU scale clipped at {hmax:g}: metal and dense cortex cannot be separated by value; metal is "
                     "taken as voxels at the clip value and may include dense bone or miss low-density implants")
    clock("padding", 0.05)

    # 2. body
    sl, body, t_body, info["body"] = _body(image, a, sp, params, pad)
    clock("body", 0.2)
    if sl is None:
        flags.append("no body found")
        return result(L.default_names(0))
    info["crop_index_zyx"] = [[s.start, s.stop] for s in sl]
    h = np.array(a[sl])  # int16 crop
    if pad is not None:
        pm = h == pad
        touch = (_dilate(pm, sp, 1.0) & body).sum() * vox if pm.any() else 0
        if touch > 50:
            flags.append(f"the body touches the edge of the reconstruction field ({touch / 1000:.2f} cm3 within 1 mm "
                         "of padding): the limb outline is truncated there")
        del pm

    # 3. thresholds, metal
    th = _bone_thresholds(h, body, params)
    info["bone_thresholds"] = th
    body_near = _dilate(body, sp, 1.0)
    metal, rim, info["metal"] = _metal(h, body, sp, params, th["cortical_tail_end_hu"], hmax, clipped, body_near)
    body |= metal & body_near
    del body_near
    info["body"]["volume_cm3"] = round(float(body.sum()) * vox / 1000, 2)
    clock("metal", 0.3)

    # 4. bone
    t_low, t_high = th["low_hu"], th["high_hu"]
    B = _bone_low(h, body, metal, rim, sp, t_low, t_high, params, params.bone_smooth_mm)
    info["bone_mask_cm3"] = round(float(B.sum()) * vox / 1000, 2)
    dark = min(-300.0, -100.0 - 4 * th["lean_noise_sd_hu"])  # below fat (-100) by 4 noise sd: no tissue is darker
    spur, info["streaks"] = _streak_spurs(h, B, body, metal, sp, params, dark)
    streak_zone = info["streaks"].pop("zone", None)
    if spur.any():
        B &= ~spur
        B, _, _ = _keep_big(B, vox, params.min_bone_mm3)
    del spur
    clock("bone threshold", 0.4)
    lab = np.zeros(h.shape, np.uint8)
    env = np.zeros(h.shape, bool)
    k_of: dict[int, int] = {}
    env_lab = None
    bsl = None
    if B.any():
        mt = metal & _dilate(B, sp, 2.0)
        bsl = _bbox(B | mt, int(np.ceil((params.close_mm + 3.0) / sp.min())))
        del mt
        hB, BB, MB, RB = h[bsl], B[bsl], metal[bsl], rim[bsl]
        reg, info["separation"] = _separate(hB, BB, MB, sp, t_high, params, th["cortical_peak_hu"])
        clock("bone separation", 0.55)
        env_lab, info["envelopes"] = _envelopes(reg, BB, MB, body[bsl], sp, params)
        del reg
        clock("bone envelopes", 0.65)
        envB = env_lab > 0
        peel, info["surface"] = _local_surface(hB, envB, MB, sp, th["lean_peak_hu"], th["cortical_tail_end_hu"], params, t_low)
        env_lab[peel] = 0
        envB &= ~peel
        del peel
        env[bsl] = envB
        ev = hB[envB & ~MB & ~RB]
        if params.cortical_hu is not None:
            t_cort, why = float(params.cortical_hu), "user value"
        elif ev.size > 100:
            t_cort, why = _otsu(ev, -200, 2500), "Otsu split of envelope HU (marrow/trabecular vs compact bone)"
        else:
            t_cort, why = 0.5 * (t_low + t_high), "too few envelope voxels: mean of bone thresholds"
        del ev
        info["cortical_threshold_hu"] = round(t_cort, 1)
        info["cortical_threshold_reason"] = why
        info["cortical_note"] = ("at 0.3-0.5 mm voxels trabeculae are not resolved: 'medullary' is envelope interior "
                                 "below the threshold (marrow and trabecular bone by mean density), 'cortical' is "
                                 "compact bone")
        hb = _inpaint(hB.astype(np.float32), RB & envB, envB & ~MB & ~RB, sp, 1.0, t_cort - 1)
        if params.cortical_smooth_mm > 0:  # normalised convolution inside the envelope (soft tissue does not leak in)
            w = (envB & ~MB).astype(np.float32)
            sig = [params.cortical_smooth_mm / q for q in sp]
            num = ndi.gaussian_filter(hb * w, sig)
            den = ndi.gaussian_filter(w, sig)
            hb = np.where(den > 1e-3, num / np.maximum(den, 1e-3), hb).astype(np.float32)
            del w, num, den
        outer = envB & (_edt(_fill_holes(envB), sp) <= min(params.surface_layer_mm, 1.05 * float(np.max(sp))))
        cort = envB & ((hb >= t_cort) | outer)  # the outer layer is compact bone, never marrow
        info["outer_layer_below_cortical_threshold_cm3"] = round(float((outer & (hb < t_cort)).sum()) * vox / 1000, 3)
        del hb, outer
        ids = [int(i) for i in np.unique(env_lab) if i > 0]
        cnt = np.bincount(env_lab[~MB].ravel(), minlength=int(env_lab.max()) + 1)
        order = sorted(ids, key=lambda i: -cnt[i])
        if len(order) > L.MAX_BONES:
            flags.append(f"{len(order) - L.MAX_BONES} smallest bone pieces beyond {L.MAX_BONES} were labelled as soft "
                         "tissue")
            order = order[:L.MAX_BONES]
        k_of = {i: k for k, i in enumerate(order)}
        rank = np.full(int(env_lab.max()) + 1, -1, np.int32)
        for i, k in k_of.items():
            rank[i] = k
        rk = rank[env_lab]
        isb = (rk >= 0) & ~MB
        lb = np.zeros(hB.shape, np.uint8)
        lb[isb] = np.where(cort[isb], L.BONE_BASE + 2 * rk[isb], L.BONE_BASE + 2 * rk[isb] + 1).astype(np.uint8)
        env[bsl] &= rk >= 0
        lab[bsl] = lb
        del rk, cort, lb, isb
    else:
        info["separation"] = {"markers": 0}
    clock("cortical / medullary", 0.7)

    # 7. soft tissue
    soft_info, fatlean = _soft_tissue(image, soft, sl, h, body, lab, metal, env, sp, params, flags, streak_zone)
    del streak_zone
    info["soft_tissue"] = soft_info
    lab[(lab == 0) & body] = fatlean[(lab == 0) & body]
    lab[metal] = L.METAL
    del fatlean
    clock("soft tissue", 0.85)

    # 8. per-bone stats and naming
    bones = _bone_stats(image, sl, lab, metal, sp, h, len(k_of))
    if params.name_bones:
        names, naming = _name_bones(image, sl, body, bones, lab, sp, params)
    else:
        names, naming = L.default_names(len(k_of)), {"enabled": False}
    if "_lab" in naming:  # pieces were merged into a named group: renumber 0..n-1 by volume, recompute stats
        lab = naming.pop("_lab")
        lab, names = _compact(lab, names, sp)
        bones = _bone_stats(image, sl, lab, metal, sp, h, sum(1 for v in names if L.is_bone(v)) // 2)
        for kk in ("ulna", "radius"):
            naming[kk] = next((L.bone_index(v) + 1 for v, nm in names.items() if nm == f"{kk}_cortical"), None)
    for b in bones:
        b["name"] = names[L.cortical(b["k"])][:-len("_cortical")]
        v = (b["volume_cm3"] + b["metal_in_or_on_envelope_cm3"]) * 1000
        b["equivalent_diameter_mm"] = round(2 * float(np.sqrt(v / (np.pi * max(b["length_mm"], 1e-3)))), 2)
        if b["volume_cm3"] < 0.5 and b["equivalent_diameter_mm"] < 3.0 and b["length_mm"] > 4 * b["equivalent_diameter_mm"]:
            b["note"] = "thin elongated dense structure: may be a vascular or soft-tissue calcification, not a bone"
            flags.append(f"{b['name']} ({b['volume_cm3']:.3f} cm3, {b['length_mm']:.0f} mm long, "
                         f"{b['equivalent_diameter_mm']:.1f} mm thick) is thin and elongated: possibly a calcification")
    info["bones"] = bones
    info["naming"] = naming
    for b in bones:
        if b.get("touches_scan_edge"):
            flags.append(f"{b['name']} is cut by the scan boundary: its length and volume are lower bounds")
    out[sl] = lab
    info["volumes_cm3"] = {names.get(int(v), str(v)): round(float(c) * vox / 1000, 3)
                           for v, c in zip(*np.unique(lab, return_counts=True)) if v > 0}
    clock("stats and naming", 1.0)
    return result(names)


# ------------------------------------------------------------------------------------------------- soft tissue

def _soft_tissue(image, soft, sl, h, body, lab, metal, env, sp, params, flags, streak_zone=None):
    """Fat / lean labels (uint8 crop) from the soft kernel when available; artefact region near metal."""
    vox = float(np.prod(sp))
    info: dict = {}
    s_arr = None
    if soft is not None:
        same = (soft.GetSize() == image.GetSize() and np.allclose(soft.GetSpacing(), image.GetSpacing())
                and np.allclose(soft.GetOrigin(), image.GetOrigin(), atol=1e-3)
                and np.allclose(soft.GetDirection(), image.GetDirection(), atol=1e-5))
        if same:
            s_arr = np.array(sitk.GetArrayViewFromImage(soft)[sl])
            info["series"] = "soft kernel (same grid)"
        else:
            ref = _sub_image(image, sl)
            s_arr = sitk.GetArrayFromImage(sitk.Resample(soft, ref, sitk.Transform(), sitk.sitkLinear, -1000,
                                                         sitk.sitkInt16))
            info["series"] = "soft kernel resampled (linear) onto the bone-kernel grid"
    if s_arr is None:
        s_arr = h.copy()
        info["series"] = "bone kernel (no soft kernel given): noisier, classification relies on the median filter"
    raw = s_arr
    timing: dict = {}
    t0 = [time.perf_counter()]

    def tick(name):
        now = time.perf_counter()
        timing[name] = round(now - t0[0], 1)
        t0[0] = now
    tick("load soft series")
    rad = [max(1, int(round(params.soft_median_mm / s))) if params.soft_median_mm > 0 else 0 for s in sp]
    if max(rad) > 0:
        im = sitk.GetImageFromArray(s_arr)
        sm = sitk.GetArrayFromImage(sitk.Median(im, [int(r) for r in rad[::-1]]))
        del im
    else:
        sm = s_arr
    info["median_radius_vox_zyx"] = rad
    tick("median filter")
    softc = body & ~env & ~metal
    # skin layers
    d_skin = _edt(body, sp)
    shell = softc & (d_skin <= params.skin_mm)
    pv = softc & (d_skin <= params.skin_pv_mm)
    v = sm[softc & ~shell][::3].astype(np.float32)
    fat_pk = _mode(v, -160, -40)
    lean_pk = _mode(v, -10, 120)
    conv = -30.0
    info["conventional_fat_window"] = [-190, -30]
    info["conventional_lean_window"] = [-29, 150]
    fit = None
    if fat_pk is not None and lean_pk is not None and lean_pk - fat_pk > 40:
        sel = v[(v > fat_pk - 150) & (v < lean_pk + 150)]
        fit = _two_gauss(sel[:: max(1, sel.size // 400000)], fat_pk, lean_pk)
        c, hh = _hist_peaks(v, fat_pk, lean_pk, 2.0, 8.0)
        info["valley_hu"] = float(c[np.argmin(hh)])
    info.update(fat_peak_hu=fat_pk, lean_peak_hu=lean_pk, two_gaussian=fit)
    if params.fat_lean_hu is not None:
        t_fl, why = float(params.fat_lean_hu), "user value"
    elif fit is not None:
        t_fl = 0.5 * (fat_pk + lean_pk)
        why = ("half-way between the fat and lean histogram peaks: a partial-volume voxel goes to the tissue that fills "
               "most of it (the two-Gaussian Bayes boundary and the valley are reported but are pulled by the unequal "
               "class sizes and partial-volume tails)")
    else:
        t_fl, why = conv, "no clear fat and lean peaks: conventional -30 HU"
        flags.append("fat/lean threshold could not be measured on this scan; the conventional -30 HU was used")
    info["fat_lean_hu"] = round(t_fl, 1)
    info["fat_lean_reason"] = why
    tick("skin distance and histogram")
    info["difference_from_conventional_hu"] = round(t_fl - conv, 1)
    # plausible range and artefact region near metal
    sd_f = fit["sd"][0] if fit else 30.0
    sd_l = fit["sd"][1] if fit else 30.0
    lo_ok = min(-190.0, (fat_pk if fat_pk is not None else -100) - 4 * sd_f)
    hi_ok = max(150.0, (lean_pk if lean_pk is not None else 40) + 4 * sd_l)
    info["plausible_soft_hu"] = [round(lo_ok, 1), round(hi_ok, 1)]
    near_bone = _dilate(env, sp, 1.0) | _dilate(metal, sp, params.metal_rim_max_mm + 1.0)
    bad = softc & ~shell & ((sm < lo_ok) | ((sm > hi_ok) & ~near_bone))
    val = sm.astype(np.float32)
    art = np.zeros(h.shape, bool)
    tick("plausible range")
    if metal.any():
        zone = softc & _dilate(metal, sp, params.artefact_radius_mm)
        art = zone & (_local_fraction(bad, zone, sp, params.artefact_sigma_mm) >= params.artefact_density)
        if streak_zone is not None:
            art |= streak_zone & softc
        # in-paint implausible voxels inside the region from plausible neighbours (normalised convolution,
        # Knutsson & Westin 1993) so dark streaks do not become fat and bright streaks do not become lean outliers
        fix = art & bad
        val = _inpaint(val, fix, zone & ~bad, sp, params.artefact_sigma_mm, t_fl + 1)
        info["artefact"] = _region_summary(image, sl, art, sp)
        info["artefact"]["implausible_voxels_in_region_cm3"] = round(float(fix.sum()) * vox / 1000, 3)
        info["artefact"]["definition"] = (f"soft tissue within {params.artefact_radius_mm:g} mm of metal where >= "
                                          f"{100 * params.artefact_density:g} % of the voxels in a {params.artefact_sigma_mm:g}"
                                          " mm neighbourhood have implausible HU, plus the bone-kernel dark-streak zone; "
                                          "labels there are unreliable (implausible voxels were in-painted before "
                                          "fat/lean classification)")
        del zone, fix
        tick("artefact region")
    if "artefact" not in info:
        info["artefact"] = {"volume_cm3": 0.0}
    out_bad = bad & ~art
    info["implausible_outside_artefact_cm3"] = round(float(out_bad.sum()) * vox / 1000, 3)
    if info["implausible_outside_artefact_cm3"] > 1.0:
        flags.append(f"{info['implausible_outside_artefact_cm3']:.1f} cm3 of soft tissue away from metal has HU outside "
                     "the plausible fat-lean range (gas, contrast, calcification, dressing or noise)")
    fat = softc & (val < t_fl)
    fat_raw = softc & (raw < t_fl)
    info["fat_cm3_without_median"] = round(float(fat_raw.sum()) * vox / 1000, 2)
    del fat_raw
    fat &= ~pv  # air/skin partial volume is skin (lean), not fat
    info["skin_pv_forced_lean_cm3"] = round(float((pv & (val < t_fl)).sum()) * vox / 1000, 2)
    out = np.where(softc, np.where(fat, L.FAT, L.MUSCLE), 0).astype(np.uint8)
    lean = softc & ~fat
    info["fat_cm3"] = round(float(fat.sum()) * vox / 1000, 2)
    info["lean_cm3"] = round(float(lean.sum()) * vox / 1000, 2)
    info["fat_cm3_with_median"] = round(float((softc & (val < t_fl)).sum()) * vox / 1000, 2)
    info["skin_shell_cm3"] = round(float(shell.sum()) * vox / 1000, 2)
    info["skin_shell_lean_cm3"] = round(float((shell & lean).sum()) * vox / 1000, 2)
    info["lean_without_skin_shell_cm3"] = round(float((lean & ~shell).sum()) * vox / 1000, 2)
    tick("classification")
    info["skin_note"] = (f"the label 'muscle' is all lean soft tissue incl. a ~{params.skin_mm:g} mm skin shell "
                         "(reported separately here); muscle alone is about lean_without_skin_shell_cm3")
    # subcutaneous vs intermuscular fat: deep tissue envelope (fascia) from lean below the skin, bones and metal
    deep = (lean & (d_skin > params.skin_mm)) | env | metal
    del d_skin
    fas = _fill_capped(_close(deep, sp, params.fascia_close_mm)) & body
    info["fat_subcutaneous_cm3"] = round(float((fat & ~fas).sum()) * vox / 1000, 2)
    info["fat_intermuscular_cm3"] = round(float((fat & fas).sum()) * vox / 1000, 2)
    info["fat_split_note"] = (f"split by a fascia envelope = closing ({params.fascia_close_mm:g} mm) and filling of lean "
                              f"tissue deeper than {params.skin_mm:g} mm plus bones; approximate where fat is thin")
    tick("fat split")
    info["timing_s"] = timing
    return info, out


def _region_summary(image, sl, m, sp):
    vox = float(np.prod(sp))
    if not m.any():
        return {"volume_cm3": 0.0}
    bb = _bbox(m)
    lo = [s.start + c.start for s, c in zip(bb, sl)]
    hi = [s.stop - 1 + c.start for s, c in zip(bb, sl)]
    pts = _phys(image, np.array([lo, hi]))
    return {"volume_cm3": round(float(m.sum()) * vox / 1000, 2),
            "index_range_zyx": [[int(a), int(b)] for a, b in zip(lo, hi)],
            "physical_bbox_lps_mm": [[round(float(min(pts[0, k], pts[1, k])), 1), round(float(max(pts[0, k], pts[1, k])), 1)]
                                     for k in range(3)]}


# --------------------------------------------------------------------------------------------- stats and naming

def _bone_stats(image, sl, lab, metal, sp, h, n):
    """Per-bone volume, cortical fraction, length along its own principal axis (mm), centroid, axis, edge contact."""
    vox = float(np.prod(sp))
    out = []
    shape_full = sitk.GetArrayViewFromImage(image).shape
    for k in range(n):
        cm, mm = lab == L.cortical(k), lab == L.medullary(k)
        e = cm | mm
        if not e.any():
            continue
        bb = _bbox(e, 1)
        e_in = e[bb]
        menv = metal[bb] & _dilate(e_in, sp, 0.6)  # metal inside or on the envelope
        c, m = int(cm.sum()), int(mm.sum())
        idx = np.argwhere(e_in | menv)
        if len(idx) > 200000:
            idx = idx[:: len(idx) // 200000]
        p = _phys(image, idx + [s.start + q.start for s, q in zip(sl, bb)])
        ctr = p.mean(0)
        if len(p) > 3:
            ax = np.linalg.eigh(np.cov((p - ctr).T))[1][:, -1]
            t = (p - ctr) @ ax
            length = float(t.max() - t.min())
        else:
            ax, length = np.array([0, 0, 1.0]), 0.0
        b2 = _bbox(e)
        touches = any(s.start + c0.start == 0 or s.stop + c0.start == nn for s, c0, nn in zip(b2, sl, shape_full))
        hv = h[e]
        out.append({"k": k, "volume_cm3": round((c + m) * vox / 1000, 3), "cortical_cm3": round(c * vox / 1000, 3),
                    "medullary_cm3": round(m * vox / 1000, 3), "cortical_fraction": round(c / max(c + m, 1), 3),
                    "metal_in_or_on_envelope_cm3": round(float(menv.sum()) * vox / 1000, 3),
                    "length_mm": round(length, 1), "centroid_lps_mm": [round(float(v), 1) for v in ctr],
                    "axis_lps": [round(float(v), 3) for v in ax], "mean_hu": round(float(hv.mean()), 1),
                    "cortical_mean_hu": round(float(h[cm].mean()), 1) if c else None,
                    "touches_scan_edge": bool(touches)})
    return out


def _compact(lab: np.ndarray, names: dict[int, str], sp) -> tuple[np.ndarray, dict[int, str]]:
    """Renumber bone labels to 0..n-1 by decreasing volume, carrying names along."""
    present = [k for k in range(L.MAX_BONES) if L.cortical(k) in names]
    cnt = np.bincount(lab.ravel(), minlength=256)
    vol = {k: cnt[L.cortical(k)] + cnt[L.medullary(k)] for k in present}
    order = [k for k in sorted(present, key=lambda k: -vol[k]) if vol[k] > 0]
    lut = np.arange(256, dtype=np.uint8)
    new = {v: nm for v, nm in names.items() if not L.is_bone(v)}
    for j, k in enumerate(order):
        lut[L.cortical(k)], lut[L.medullary(k)] = L.cortical(j), L.medullary(j)
        base = names[L.cortical(k)][:-len("_cortical")]
        base = f"bone_{j + 1}" if base == f"bone_{k + 1}" else base
        new[L.cortical(j)], new[L.medullary(j)] = f"{base}_cortical", f"{base}_medullary"
    return lut[lab], new


def _name_bones(image, sl, body, bones, lab, sp, params):
    """Forearm naming layer. Names only when the evidence agrees; otherwise generic names and the reason."""
    n = len(bones)
    names = L.default_names(n)
    info: dict = {"enabled": True, "result": "generic"}
    if n < 2:
        info["reason"] = "fewer than two bones"
        return names, info
    # limb axis from the body (subsampled voxels)
    bidx = np.argwhere(body[::2, ::2, ::2]) * 2 + [s.start for s in sl]
    pb = _phys(image, bidx[:: max(1, len(bidx) // 300000)])
    cb = pb.mean(0)
    u = np.linalg.eigh(np.cov((pb - cb).T))[1][:, -1]
    tb = (pb - cb) @ u
    span = float(tb.max() - tb.min())
    info["limb_axis_lps"] = [round(float(v), 3) for v in u]
    info["body_extent_mm"] = round(span, 1)
    longs = []
    for b in bones:
        L_ = b["length_mm"]
        v = b["volume_cm3"] * 1000 + b["metal_in_or_on_envelope_cm3"] * 1000
        deq = 2 * np.sqrt(v / (np.pi * max(L_, 1e-3)))
        ang = float(np.degrees(np.arccos(min(1.0, abs(float(np.dot(b["axis_lps"], u)))))))
        b["elongation"] = round(L_ / max(deq, 1e-3), 2)
        b["angle_to_limb_axis_deg"] = round(ang, 1)
        if b["elongation"] >= params.long_bone_min_elongation and ang <= 30 and L_ >= params.long_bone_min_span * span:
            longs.append(b)
    info["long_bone_candidates"] = [b["k"] + 1 for b in longs]
    if len(longs) != 2:
        info["reason"] = f"{len(longs)} long bones parallel to the limb axis (a forearm has exactly two)"
        return names, info
    # profiles of envelope cross-section along u for both long bones
    prof = {}
    for b in longs:
        k = b["k"]
        m = (lab == L.cortical(k)) | (lab == L.medullary(k))
        idx = np.argwhere(m)
        idx = idx[:: max(1, len(idx) // 400000)]
        t = (_phys(image, idx + [s.start for s in sl]) - cb) @ u
        prof[k] = t
    t0 = [np.percentile(prof[b["k"]], 0.5) for b in longs]
    t1 = [np.percentile(prof[b["k"]], 99.5) for b in longs]
    lo, hi = max(t0), min(t1)
    if hi - lo < 0.5 * min(t1[0] - t0[0], t1[1] - t0[1]):
        info["reason"] = "the two long bones do not overlap along the limb axis"
        return names, info
    taper = {}
    for b, a0, a1 in zip(longs, t0, t1):
        t = prof[b["k"]]
        ln = a1 - a0
        e_lo = ((t >= a0 + 0.03 * ln) & (t < a0 + 0.18 * ln)).sum()
        e_hi = ((t > a1 - 0.18 * ln) & (t <= a1 - 0.03 * ln)).sum()
        taper[b["k"]] = float(np.log(max(e_lo, 1) / max(e_hi, 1)))  # > 0: large at low-t end
    info["end_area_log_ratio_low_over_high"] = {f"bone_{k + 1}": round(v, 3) for k, v in taper.items()}
    # cue S: soft-tissue taper of the forearm (muscle bellies proximal, tendons distal)
    q25, q75 = lo + 0.25 * (hi - lo), lo + 0.75 * (hi - lo)
    w = 0.05 * (hi - lo)
    a25 = float(((tb > q25 - w) & (tb < q25 + w)).sum())
    a75 = float(((tb > q75 - w) & (tb < q75 + w)).sum())
    rs = a25 / max(a75, 1)
    info["limb_area_ratio_25_over_75"] = round(rs, 3)
    votes = []
    if rs >= params.limb_taper_min_ratio:
        votes.append(("soft-tissue taper", -1))  # proximal at low t
    elif 1 / max(rs, 1e-6) >= params.limb_taper_min_ratio:
        votes.append(("soft-tissue taper", +1))
    # cue N: other bones beyond the ends
    big_ends = {-1: [], +1: []}
    for b in bones:
        if b in longs:
            continue
        tc = float((np.array(b["centroid_lps_mm"]) - cb) @ u)
        if tc < min(t0):  # beyond the end of BOTH long bones (a piece next to one bone's end is not grouped)
            big_ends[-1].append(b)
        elif tc > max(t1):
            big_ends[+1].append(b)
    vol = {s: sum(b["volume_cm3"] for b in big_ends[s]) for s in big_ends}
    cnt = {s: sum(1 for b in big_ends[s] if b["volume_cm3"] >= 0.1) for s in big_ends}
    info["bones_beyond_ends"] = {"low_t": [b["k"] + 1 for b in big_ends[-1]], "high_t": [b["k"] + 1 for b in big_ends[+1]]}
    for s in (-1, +1):
        o = -s
        # a single dominant large bone at one end (humerus) and >= 3 small bones (carpals) at the other
        dom = big_ends[s] and max(b["volume_cm3"] for b in big_ends[s]) >= 0.6 * vol[s] and vol[s] >= 5.0
        many = cnt[o] >= 3 and (not big_ends[o] or max(b["volume_cm3"] for b in big_ends[o]) < 0.6 * vol[o])
        if dom and many:
            votes.append(("humerus-like bone at one end, carpal-like cluster at the other", s))
    info["proximal_votes"] = [{"cue": c, "proximal_end": "low_t" if s < 0 else "high_t"} for c, s in votes]
    sides = {s for _, s in votes}
    if len(sides) != 1:
        info["reason"] = "no cue for the proximal end" if not votes else "cues for the proximal end disagree"
        return names, info
    prox = sides.pop()
    # taper cue: the ulna is large proximally, the radius large distally
    ka, kb = longs[0]["k"], longs[1]["k"]
    big = {}
    for k in (ka, kb):
        r = taper[k] * (-prox)  # > 0: large at the proximal end
        big[k] = +1 if r >= np.log(params.taper_min_ratio) else (-1 if r <= -np.log(params.taper_min_ratio) else 0)
    if sorted(big.values()) != [-1, 1]:
        info["reason"] = ("bone end sizes do not show one bone large proximally (ulna) and one large distally (radius): "
                          f"{big}")
        return names, info
    ulna = ka if big[ka] > 0 else kb
    radius = kb if ulna == ka else ka
    new = {ulna: "ulna", radius: "radius"}
    lab = lab.copy()
    merges = {}
    for s, nm in ((prox, "humerus_distal"), (-prox, "carpals_hand")):
        grp = [b for b in big_ends[s] if b["volume_cm3"] > 0]
        if grp:
            tgt = min(b["k"] for b in grp)
            for b in grp:
                if b["k"] != tgt:
                    lab[lab == L.cortical(b["k"])] = L.cortical(tgt)
                    lab[lab == L.medullary(b["k"])] = L.medullary(tgt)
                    merges[b["k"] + 1] = tgt + 1
            new[tgt] = nm
    names = L.default_names(n)
    for k, nm in new.items():
        names[L.cortical(k)] = f"{nm}_cortical"
        names[L.medullary(k)] = f"{nm}_medullary"
    for kk in merges:  # merged pieces no longer exist
        names.pop(L.cortical(kk - 1), None)
        names.pop(L.medullary(kk - 1), None)
    info.update(result="forearm", proximal_end="low_t" if prox < 0 else "high_t", ulna=ulna + 1, radius=radius + 1,
                merged_into=merges, _lab=lab)
    return names, info
