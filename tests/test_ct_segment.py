"""Segmentation tests on synthetic CT only (phantom.make_phantom and a configurable limb generator).

Tolerances are set from measured behaviour on these phantoms plus the discretisation error they imply:
a half-voxel error of the bone surface is ~ +/-4 % of a 5-6 mm radius tube at 0.9 mm voxels (8 % area), so bone volume
must be within 8 % of the analytic value; Dice per class must exceed values ~0.02-0.05 below what the method reaches
at noise 12 HU (measured: body 0.998, fat 0.988, lean 0.998, bone 0.998, cortical 0.996, medullary 1.0, metal 1.0).
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from emgmirror.ct import labels as L
from emgmirror.ct import segment as S
from emgmirror.ct.phantom import HU, make_phantom


# ------------------------------------------------------------------------------------------------------ helpers

def _arr(img):
    return sitk.GetArrayFromImage(img)


def _dice(a, b):
    return 2 * (a & b).sum() / max(a.sum() + b.sum(), 1)


def _with_array(img: sitk.Image, arr: np.ndarray) -> sitk.Image:
    out = sitk.GetImageFromArray(arr.astype(np.int16))
    out.CopyInformation(img)
    return out


def _grid(img):
    return img.GetSize(), img.GetSpacing(), img.GetOrigin(), img.GetDirection()


def _physical_mesh(img):
    """Physical x, y, z (mm) of every voxel centre, as broadcastable [z, y, x] arrays (identity direction)."""
    nx, ny, nz = img.GetSize()
    sx, sy, sz = img.GetSpacing()
    ox, oy, oz = img.GetOrigin()
    z = (oz + np.arange(nz) * sz)[:, None, None]
    y = (oy + np.arange(ny) * sy)[None, :, None]
    x = (ox + np.arange(nx) * sx)[None, None, :]
    return x, y, z


def _limb(length=120.0, sp=(0.9, 0.9, 1.5), ab=(24.0, 20.0), taper=0.0, bones=(), noise=12.0, seed=1, ss=2,
          fat_t=3.0):
    """Elliptical limb along z (semi-axes `ab` at z=0, scaled by (1 - taper * t) with t = z / length) with hollow bones.

    Each bone: dict(x=(x0, x1), y=(y0, y1), r=(r0, rmid, r1), t=cortical thickness, z=(zlo, zhi)); centre and radius
    are linear in z (radius piecewise through rmid at the middle). Supersampled `ss` times per axis for partial volume.
    Returns (image, truth labels, analytic dict)."""
    sx, sy, sz = sp
    a0, b0 = ab
    W = 2 * a0 + 16
    nx, ny, nz = int(W / sx), int((2 * b0 + 16) / sy), int(length / sz)
    lab = np.zeros((nz, ny, nx), np.uint8)
    hu = np.zeros((nz, ny, nx), np.float32)
    off = (np.arange(ss) + 0.5) / ss - 0.5
    xs = ((np.arange(nx)[:, None] + off[None]) * sx - W / 2).ravel()
    ys = ((np.arange(ny)[:, None] + off[None]) * sy - (b0 + 8)).ravel()
    X, Y = np.meshgrid(xs, ys, indexing="xy")
    vol = {"body_mm3": 0.0, "bones": [dict(volume_mm3=0.0) for _ in bones]}
    for k in range(nz):
        acc = np.zeros(X.shape, np.float32)
        lab_hi = np.zeros(X.shape, np.uint8)
        for q in off:
            zz = (k + 0.5 + q) * sz
            t = zz / length
            s = 1 - taper * t
            a, b = a0 * s, b0 * s
            m = np.full(X.shape, HU["air"], np.float32)
            lb = np.zeros(X.shape, np.uint8)
            e = (X / a) ** 2 + (Y / b) ** 2
            m[e <= 1] = HU["fat"]
            lb[e <= 1] = L.FAT
            ei = (X / (a - fat_t)) ** 2 + (Y / (b - fat_t)) ** 2
            m[ei <= 1] = HU["muscle"]
            lb[ei <= 1] = L.MUSCLE
            vol["body_mm3"] += np.pi * a * b * sz / ss
            for j, bn in enumerate(bones):
                zl, zh = bn.get("z", (-1e9, 1e9))
                if not (zl <= zz <= zh):
                    continue
                u = np.clip((zz - max(zl, 0)) / (min(zh, length) - max(zl, 0)), 0, 1)
                cx = bn["x"][0] + (bn["x"][1] - bn["x"][0]) * u
                cy = bn["y"][0] + (bn["y"][1] - bn["y"][0]) * u
                r0, rm, r1 = bn["r"]
                r = r0 + (rm - r0) * 2 * u if u < 0.5 else rm + (r1 - rm) * (2 * u - 1)
                d2 = (X - cx) ** 2 + (Y - cy) ** 2
                m[d2 <= r * r] = HU["cortical"]
                lb[d2 <= r * r] = L.cortical(j)
                ri = r - bn["t"]
                m[d2 <= ri * ri] = HU["marrow"]
                lb[d2 <= ri * ri] = L.medullary(j)
                vol["bones"][j]["volume_mm3"] += np.pi * r * r * sz / ss
            acc += m / ss
            if abs(q - off[len(off) // 2]) < 1e-9:
                lab_hi = lb
        hu[k] = acc.reshape(ny, ss, nx, ss).mean(axis=(1, 3))
        lab[k] = lab_hi[ss // 2::ss, ss // 2::ss]
    hu += np.random.default_rng(seed).normal(0, noise, hu.shape).astype(np.float32)
    img = sitk.GetImageFromArray(np.clip(np.rint(hu), -1024, 32000).astype(np.int16))
    tr = sitk.GetImageFromArray(lab)
    for im in (img, tr):
        im.SetSpacing(sp)
        im.SetOrigin((-W / 2 + sx / 2, -(b0 + 8) + sy / 2, sz / 2))
    return img, tr, vol


def _forearm(flip_z=False, mirror_x=False, **kw):
    """Forearm-like limb: soft tissue tapering distally, an 'ulna' large proximally and a 'radius' large distally.

    Proximal is z = 0 unless flip_z. Returns image, truth and the x position of the ulna centre at mid-length."""
    ulna = dict(x=(-9.0, -7.0), y=(2.0, 1.0), r=(8.0, 4.5, 3.5), t=1.5, z=(8.0, 112.0))
    radius = dict(x=(7.0, 9.0), y=(-1.0, 0.0), r=(3.5, 4.5, 8.0), t=1.5, z=(14.0, 116.0))
    img, tr, vol = _limb(length=124.0, sp=(1.0, 1.0, 2.0), ab=(26.0, 21.0), taper=0.35, bones=(ulna, radius), **kw)
    a, t = _arr(img), _arr(tr)
    if flip_z:
        a, t = a[::-1], t[::-1]
    if mirror_x:
        a, t = a[:, :, ::-1], t[:, :, ::-1]
    img2, tr2 = sitk.GetImageFromArray(np.ascontiguousarray(a)), sitk.GetImageFromArray(np.ascontiguousarray(t))
    for im in (img2, tr2):
        im.CopyInformation(img)
    ux = -8.0 * (-1 if mirror_x else 1)
    return img2, tr2, vol, ux


def _permuted(img: sitk.Image, perm=(2, 0, 1)) -> tuple[sitk.Image, tuple]:
    """Same physical voxels with image axis a = old axis perm[a] (array transposed, direction/spacing permuted)."""
    arr = _arr(img)
    axes = [2 - perm[2], 2 - perm[1], 2 - perm[0]]
    out = sitk.GetImageFromArray(np.ascontiguousarray(np.transpose(arr, axes)))
    d = np.array(img.GetDirection()).reshape(3, 3)
    out.SetDirection(tuple(d[:, list(perm)].ravel()))
    out.SetSpacing(tuple(img.GetSpacing()[p] for p in perm))
    out.SetOrigin(img.GetOrigin())
    return out, tuple(axes)


def _scores(res, truth):
    lab, tr = _arr(res.labels), _arr(truth)
    f = {"body": lambda x: x > 0, "fat": lambda x: x == L.FAT, "lean": lambda x: x == L.MUSCLE,
         "metal": lambda x: x == L.METAL, "bone": lambda x: x >= L.BONE_BASE,
         "cortical": lambda x: (x >= L.BONE_BASE) & (x % 2 == 0), "medullary": lambda x: (x >= L.BONE_BASE) & (x % 2 == 1)}
    return {k: _dicef(g(lab), g(tr)) for k, g in f.items()}


def _dicef(a, b):
    return float(_dice(a, b)) if (a.any() or b.any()) else 1.0


def _bone_vols(res):
    vox = float(np.prod(res.labels.GetSpacing()))
    lab = _arr(res.labels)
    n = max((L.bone_index(int(v)) + 1 for v in np.unique(lab) if L.is_bone(int(v))), default=0)
    return [float(((lab == L.cortical(k)) | (lab == L.medullary(k))).sum()) * vox for k in range(n)]


# ------------------------------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def base():
    ph = make_phantom()
    return ph, S.segment(ph.image)


# -------------------------------------------------------------------------------------------------- tests

def test_base_phantom_dice_and_volumes(base):
    ph, res = base
    assert _grid(res.labels) == _grid(ph.image)
    assert res.labels.GetPixelID() == sitk.sitkUInt8
    sc = _scores(res, ph.truth)
    for k, lim in {"body": 0.99, "fat": 0.95, "lean": 0.97, "bone": 0.96, "cortical": 0.95, "medullary": 0.95,
                   "metal": 0.9}.items():
        assert sc[k] >= lim, (k, sc)
    vols = _bone_vols(res)
    assert len(vols) == 2
    for v, a in zip(sorted(vols, reverse=True), [b["volume_mm3"] for b in ph.analytic["bones"]]):
        assert abs(v - a) / a < 0.08
    # metal: partial-volume volume within 25 % of the analytic plate (measured +15 %: the plate lies on cortex, and
    # each 1400 HU bone voxel within the 2 mm integration shell adds ~0.17 voxel), voxel count within one voxel layer
    m = res.info["metal"]
    assert abs(m["partial_volume_mm3"] - ph.analytic["metal_volume_mm3"]) / ph.analytic["metal_volume_mm3"] < 0.25
    assert abs(m["volume_mm3"] - ph.analytic["metal_volume_mm3"]) / ph.analytic["metal_volume_mm3"] < 0.35
    lab = _arr(res.labels)
    tr = _arr(ph.truth)
    assert not (L.is_bone(0) or (lab[tr == L.METAL] >= L.BONE_BASE).any())  # metal never labelled bone
    # cradle fully excluded; body volume vs analytic elliptic cylinder
    a, b = ph.analytic["limb_semi_axes_mm"]
    nz = ph.image.GetSize()[2]
    body_analytic = np.pi * a * b * nz * ph.image.GetSpacing()[2]
    body = (lab > 0).sum() * np.prod(ph.image.GetSpacing())
    assert abs(body - body_analytic) / body_analytic < 0.015
    assert res.names[L.cortical(0)] == "bone_1_cortical" and res.names[L.cortical(1)] == "bone_2_cortical"
    assert res.info["naming"]["result"] == "generic"


def test_marrow_is_medullary_and_envelope_solid(base):
    ph, res = base
    lab = _arr(res.labels)
    tr = _arr(ph.truth)
    for k in range(2):
        med = tr == L.medullary(k)
        got = lab[med]
        assert (L.is_bone(int(v)) for v in got)
        assert np.mean(got % 2 == 1) > 0.85  # measured 0.90: the endosteal edge moves ~half a 0.9 mm voxel
        bone = (lab == L.cortical(k)) | (lab == L.medullary(k))
        from scipy import ndimage as ndi
        filled = np.stack([ndi.binary_fill_holes(s) for s in bone])
        assert (filled & ~bone).sum() <= 0.01 * bone.sum()  # solid (measured 0.36-0.53 %: end-slice partial volume)


def test_no_metal_no_cradle():
    ph = make_phantom(metal=False, cradle=False)
    res = S.segment(ph.image)
    sc = _scores(res, ph.truth)
    assert sc["bone"] > 0.96 and sc["body"] > 0.99
    assert (_arr(res.labels) == L.METAL).sum() == 0
    assert res.info["metal"]["present"] is False


def test_cradle_touching_padding_and_clipped_scale():
    ph = make_phantom(cradle=False)
    a = _arr(ph.image).astype(np.int32)
    x, y, z = _physical_mesh(ph.image)
    nx, ny, _ = ph.image.GetSize()
    sx, sy, _ = ph.image.GetSpacing()
    ox, oy, _ = ph.image.GetOrigin()
    cx, cy = ox + nx * sx / 2 - sx / 2, oy + ny * sy / 2 - sy / 2
    la, lb = ph.analytic["limb_semi_axes_mm"]
    # cradle shell (2 mm, 400 HU) centred 60 mm above the limb centre so it touches the bottom of the limb (+y)
    # over a contact patch (gap < 2 mm for |x| < ~14 mm)
    R = lb + 60.0
    rr = np.sqrt((x - cx) ** 2 + (y - (cy - 60.0)) ** 2)
    shell = np.broadcast_to((rr >= R - 0.3) & (rr <= R + 2.0) & (y > cy), a.shape)
    a[shell] = np.maximum(a[shell], 400)
    # padding outside a circular field of view, and a scale clipped at 3071
    fov = np.broadcast_to(np.sqrt((x - cx) ** 2 + (y - cy) ** 2) > 0.49 * nx * sx, a.shape)
    a[fov] = -2048
    a = np.minimum(a, 3071)
    img = _with_array(ph.image, a)
    res = S.segment(img)
    lab = _arr(res.labels)
    assert res.info["padding"].get("pad_value") == -2048
    assert res.info["scale"]["clipped"] is True and any("clipped" in f for f in res.info["flags"])
    assert not (lab[fov] > 0).any()
    away = shell & ~np.broadcast_to(np.abs(x - cx) < 18.0, a.shape)  # cradle away from the contact patch
    assert not (lab[away] > 0).any()
    body = (lab > 0).sum() * np.prod(img.GetSpacing())
    body_analytic = np.pi * la * lb * img.GetSize()[2] * img.GetSpacing()[2]
    # measured +2.3 %: cradle within the 2 mm body margin over the ~28 x 60 mm contact patch (~2.5 cm3) is kept
    assert abs(body - body_analytic) / body_analytic < 0.03
    assert _scores(res, ph.truth)["bone"] > 0.95
    assert (lab == L.METAL).sum() > 0 and not (lab[_arr(ph.truth) == L.METAL] >= L.BONE_BASE).any()


def test_coarse_anisotropic_spacing():
    ph = make_phantom(shape=(24, 48, 48), spacing=(1.5, 1.5, 2.5), metal=False)
    res = S.segment(ph.image)
    vols = _bone_vols(res)
    assert len(vols) == 2
    for v, a in zip(sorted(vols, reverse=True), [b["volume_mm3"] for b in ph.analytic["bones"]]):
        assert abs(v - a) / a < 0.12  # 1.5 mm voxels: half a voxel is 12-15 % of a 5-6 mm radius
    assert _scores(res, ph.truth)["body"] > 0.98


def test_axis_permutation_invariance(base):
    ph, res = base
    img2, axes = _permuted(ph.image)
    res2 = S.segment(img2)
    assert _grid(res2.labels) == _grid(img2)
    back = np.transpose(_arr(res2.labels), np.argsort(axes))
    lab = _arr(res.labels)
    for g in (lambda x: x > 0, lambda x: x >= L.BONE_BASE, lambda x: x == L.FAT, lambda x: x == L.METAL,
              lambda x: (x >= L.BONE_BASE) & (x % 2 == 1)):
        assert _dice(g(lab), g(back)) > 0.97


def test_bone_mask_monotone(base):
    ph, _ = base
    body = S.body_mask(ph.image)
    b1 = S.bone_mask(ph.image, body, threshold_hu=200)
    b2 = S.bone_mask(ph.image, body, threshold_hu=600)
    assert b2.sum() <= b1.sum() and not (b2 & ~b1).any()
    assert b1.shape == _arr(ph.image).shape


def test_bones_cut_by_edge_near_contact_open_marrow():
    """Bones running out of the volume, converging to < 1 voxel apart at one end, one with a 2 mm hole to its canal."""
    b1 = dict(x=(-9.0, -6.2), y=(0.0, 0.0), r=(5.0, 5.0, 5.0), t=1.6, z=(-1e9, 1e9))
    b2 = dict(x=(9.0, 6.2), y=(0.0, 0.0), r=(5.0, 5.0, 5.0), t=1.6, z=(-1e9, 1e9))
    img, tr, vol = _limb(length=60.0, sp=(0.8, 0.8, 1.2), bones=(b1, b2), noise=12.0)
    a = _arr(img).astype(np.int32)
    x, y, z = _physical_mesh(img)
    hole = np.broadcast_to((np.abs(z - 30.0) < 1.0) & (np.abs(y) < 1.0) & (x < -12.0) & (x > -16.0), a.shape)
    a[hole & (a > 600)] = HU["muscle"]  # a 2 mm channel through the cortex of bone 1 into its canal
    img = _with_array(img, a)
    res = S.segment(img)
    vols = sorted(_bone_vols(res), reverse=True)
    assert len(vols) == 2, vols
    for v in vols:
        assert abs(v - vol["bones"][0]["volume_mm3"]) / vol["bones"][0]["volume_mm3"] < 0.08
    assert any("cut by the scan boundary" in f for f in res.info["flags"])
    sc = _scores(res, tr)
    assert sc["medullary"] > 0.9 and sc["bone"] > 0.95  # measured medullary 0.918 (contact end, hole channel)
    lab = _arr(res.labels)
    canal = _arr(tr) == L.medullary(0)
    # canal at the hole and at the cut ends is medullary; measured 0.86: ~8 of the ~27 endosteal-ring voxels per
    # slice fall on the cortical side (a sub-voxel endosteal shift), uniformly along z, i.e. not a fill failure
    assert np.mean(lab[canal] % 2 == 1) > 0.8


def test_strong_noise():
    ph = make_phantom(noise_hu=60.0)
    res = S.segment(ph.image)
    sc = _scores(res, ph.truth)
    assert sc["bone"] > 0.9 and sc["body"] > 0.98 and sc["fat"] > 0.8 and sc["lean"] > 0.9
    assert len(_bone_vols(res)) == 2


@pytest.mark.parametrize("flip_z,mirror_x", [(False, False), (False, True), (True, False)])
def test_forearm_naming(flip_z, mirror_x):
    img, tr, vol, ux = _forearm(flip_z=flip_z, mirror_x=mirror_x)
    res = S.segment(img)
    nm = res.info["naming"]
    assert nm["result"] == "forearm", nm
    lab = _arr(res.labels)
    x, _, _ = _physical_mesh(img)
    k_ulna = next(L.bone_index(v) for v, n in res.names.items() if n == "ulna_cortical")
    k_rad = next(L.bone_index(v) for v, n in res.names.items() if n == "radius_cortical")
    xx = np.broadcast_to(x, lab.shape)
    xu = xx[(lab == L.cortical(k_ulna)) | (lab == L.medullary(k_ulna))].mean()
    xr = xx[(lab == L.cortical(k_rad)) | (lab == L.medullary(k_rad))].mean()
    assert np.sign(xu) == np.sign(ux) and np.sign(xr) == -np.sign(ux)


def test_phantom_not_named_forearm(base):
    _, res = base
    assert not any(n.startswith(("radius", "ulna")) for n in res.names.values())
    assert res.info["naming"]["reason"]


def test_slice_fill_leaves_no_rectangular_prism():
    """A marrow cavity with a foramen in one end and a cortical slit in one wall: a single union of per-axis slice
    fills leaves a box (open rows x open columns x open slices) inside it; the repeated fill and _pockets close it."""
    c = np.zeros((40, 40, 40), bool)
    c[5:35, 5:35, 5:35] = True
    c[7:33, 7:33, 7:33] = False          # hollow shell, walls 2 voxels
    c[33:35, 15:21, 15:21] = False       # foramen through the top cap (so 3D filling cannot close it)
    c[12:23, 10:26, 33:35] = False       # slit through one side wall (so those axial slices are open rings)
    inner = np.zeros_like(c)
    inner[7:33, 7:33, 7:33] = True
    one = c.copy()  # the former rule: one union of per-slice fills of c along each axis
    for ax in range(3):
        o, m = np.moveaxis(one, ax, 0), np.moveaxis(c, ax, 0)
        for i in range(m.shape[0]):
            o[i] |= S._fill_holes(m[i])
    prism = inner & ~one
    assert prism.sum() == 11 * 16 * 6  # z 12..22 x y 10..25 x x 15..20: the artefact the single pass makes
    full = S._fill_2d_any(c)
    assert (full[inner]).all()
    f3 = S._fill_capped(c)
    assert not f3[inner].any()
    pk, rows = S._pockets(c, f3, (1.0, 1.0, 1.0), 0.15)
    assert pk[inner].all()
    assert len(rows) == 1 and rows[0]["filled"] and rows[0]["open_share"] < 0.1
    x = c | f3 | pk
    assert (S._fill_2d_any(x, max_passes=1) == x).all()


def test_edge_sensitivity_has_the_right_sign_and_size():
    # A bone edge ramping from lean tissue (0 HU) to a 1000 HU plateau. Half-maximum = 500 HU. Moving the edge to
    # 40 % (400 HU) keeps one more voxel column as bone; moving it to 60 % (600 HU) removes one more.
    sp = np.array([0.2, 0.2, 0.2])
    ramp = [100, 320, 480, 580, 780] + [1000] * 15
    h = np.broadcast_to(np.array(ramp, np.int16), (10, 10, 20)).copy()
    env = h >= 300
    peel, info = S._local_surface(h, env, np.zeros_like(env), sp, 0.0, 2000.0, S.SegParams(), 300.0)
    col = 10 * 10 * 0.008 / 1000  # one voxel column in cm3
    assert int(peel[0, 0].sum()) == 2  # 320 and 480 HU are below the half-maximum
    ch = info["edge_fraction_volume_change_cm3"]
    assert ch["0.4"] == pytest.approx(col, abs=1e-4) and ch["0.6"] == pytest.approx(-col, abs=1e-4)


def test_edge_sensitivity_in_the_pipeline_is_volume_over_area():
    from emgmirror.ct.pipeline import _edge_sensitivity
    r = _edge_sensitivity({"surface": {"edge_fraction_volume_change_cm3": {"0.4": 3.0, "0.6": -4.0}}}, 40000.0)
    assert r["fractions"]["0.4"]["shift_mm"] == pytest.approx(0.075) and r["max_abs_shift_mm"] == pytest.approx(0.1)
    assert _edge_sensitivity({}, 40000.0) is None and _edge_sensitivity({"surface": {}}, 0.0) is None


def test_skin_edge_overshoot_is_not_bone():
    """A sharp kernel leaves a thin 250-400 HU shell at the skin/air edge; with a low threshold below it, that shell
    must not become bone, while a real bone 5 mm under the skin stays."""
    rng = np.random.default_rng(3)
    zz, yy, xx = np.mgrid[0:40, 0:96, 0:96]
    r = np.hypot(yy - 47.5, xx - 47.5) * 0.47
    h = np.full(r.shape, -1000.0)
    h[r <= 20.0] = 40.0
    shell = (r > 19.5) & (r <= 20.0)
    h[shell] = 400.0                              # overshoot shell, one voxel at the skin
    h[shell & (xx > 80)] = 1000.0                 # a brighter stretch that seeds the hysteresis
    h[r <= 8.0] = 1500.0                          # bone
    h[r <= 6.0] = 60.0                            # marrow
    h += rng.normal(0, 15, h.shape)
    img = sitk.GetImageFromArray(h.astype(np.int16))
    img.SetSpacing((0.47, 0.47, 0.6))
    body = r <= 20.0
    m = S.bone_mask(img, body, S.SegParams(), threshold_hu=300.0)
    assert not m[(r > 19.0) & body].any()
    assert m[(r > 6.5) & (r < 7.5)].mean() > 0.95
    m0 = S.bone_mask(img, body, S.SegParams(skin_bone_gap_mm=0.0), threshold_hu=300.0)
    assert m0[(r > 19.6) & body].mean() > 0.5  # without the rule the shell is bone: the test sees the effect


def test_envelope_keeps_seam_under_dotted_cortex_but_returns_concavity():
    """Thin cortex that thresholding left dotted, over a 2-voxel seam, over solid bone: the seam is bone. A 4 mm wide
    notch cut into the surface is a concavity and must not be swallowed."""
    sp = (0.3, 0.468, 0.468)
    zz, yy, xx = np.mgrid[0:60, 0:48, 0:48]
    r = np.hypot((yy - 23.5) * sp[1], (xx - 23.5) * sp[2])
    ang = np.arctan2(yy - 23.5, xx - 23.5)
    core = r <= 6.0
    seam = (r > 6.0) & (r <= 6.9)
    shell = (r > 6.9) & (r <= 7.4) & (np.floor((ang + np.pi) / (2 * np.pi) * 90) % 3 != 0)  # every 3rd step a gap
    notch = (np.abs((yy - 23.5) * sp[1]) < 2.0) & (xx > 23.5) & (r > 4.0)
    m = (core | shell) & ~notch
    e, _ = S._envelope(m, sp, 1.5, 0.15, 0.75)
    seam_in = seam & ~notch & (zz > 4) & (zz < 55)
    assert e[seam_in].mean() > 0.97
    notch_out = notch & (r <= 6.0) & (r > 4.6) & (zz > 4) & (zz < 55)
    assert e[notch_out].mean() < 0.2
