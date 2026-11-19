"""Known-answer tests for emgmirror.ct.validate (synthetic data only)."""

import math

import numpy as np
import pytest
import SimpleITK as sitk
import trimesh
from skimage.measure import marching_cubes

from emgmirror.ct import labels as L
from emgmirror.ct import validate as V
from emgmirror.ct.phantom import make_phantom


def _rot(axis: str, deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def _grid(size, spacing, centre=(0.0, 0.0, 0.0), direction=np.eye(3)) -> sitk.Image:
    """Empty uint8 grid whose centre voxel sits at `centre` (LPS mm)."""
    size, spacing, d = np.asarray(size), np.asarray(spacing, float), np.asarray(direction, float)
    img = sitk.Image([int(v) for v in size], sitk.sitkUInt8)
    img.SetSpacing(tuple(spacing))
    img.SetDirection(tuple(d.ravel()))
    img.SetOrigin(tuple(np.asarray(centre) - d @ ((size - 1) / 2 * spacing)))
    return img


def _fill(img: sitk.Image, inside) -> sitk.Image:
    """Voxel = 1 where its centre satisfies inside(points)."""
    nx, ny, nz = img.GetSize()
    z, y, x = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    p = V._to_phys(img, np.stack([x.ravel(), y.ravel(), z.ravel()], 1).astype(float))
    out = sitk.GetImageFromArray(inside(p).reshape(nz, ny, nx).astype(np.uint8))
    out.CopyInformation(img)
    return out


def _sphere(c, r):
    c = np.asarray(c, float)
    return lambda p: np.sum((p - c) ** 2, axis=1) <= r * r


FINE = _grid((90, 90, 140), (0.47, 0.47, 0.30), (0.0, 0.0, 0.0))
R_COARSE = _rot("x", 90) @ _rot("z", 20)  # coronal-like: slices along LPS y, rotated 20 degrees
COARSE = _grid((60, 8, 70), (0.47, 2.0, 0.47), (3.0, 1.0, -8.0), R_COARSE)  # 16 mm slab, cuts the sphere


def test_accuracy_floor():
    f = V.accuracy_floor((0.468, 0.468, 2.0))
    assert f["max_spacing_mm"] == 2.0 and f["half_max_spacing_mm"] == 1.0
    assert f["half_diagonal_mm"] == pytest.approx(0.5 * math.sqrt(2 * 0.468 ** 2 + 4.0))
    assert f["voxel_volume_mm3"] == pytest.approx(0.468 * 0.468 * 2.0)


def test_identical_masks_are_exact():
    a = _fill(_grid((60, 60, 70), (0.5, 0.5, 0.4)), _sphere((0, 0, 0), 9.0))
    m = V.overlap_metrics(a, a)
    assert m["dice"] == 1.0 and m["dice_nearest"] == 1.0
    assert m["msd_mm"] == 0.0 and m["hausdorff_mm"] == 0.0 and m["hd95_mm"] == 0.0 and m["signed_bias_mm"] == 0.0
    assert m["volume_diff_pct"] == 0.0


def test_two_spheres_known_distance_and_dice():
    """Spheres r and r + d on two different anisotropic grids: distance ~ d, bias +d, analytic Dice."""
    r, d = 10.0, 1.5
    g_ref = _grid((80, 80, 100), (0.4, 0.4, 0.33))
    g_test = _grid((50, 50, 30), (0.7, 0.7, 1.2))
    m = V.overlap_metrics(_fill(g_ref, _sphere((0, 0, 0), r)), _fill(g_test, _sphere((0, 0, 0), r + d)))
    floor = V.accuracy_floor(g_test.GetSpacing())["half_diagonal_mm"]  # 0.78 mm
    assert m["voxel_floor_mm"] == pytest.approx(floor)
    # Tolerances: a centre-sampled smooth surface is assumed to have a mean radius error of at most
    # 1/10 of the grid's largest spacing (eps). Volume tolerance 3*eps/r; Dice tolerance = eps propagated
    # through D = 2 r1^3 / (r1^3 + r2^3).
    e1, e2 = 0.1 * 0.4, 0.1 * 1.2
    r2 = r + d
    v1, v2 = 4 / 3 * math.pi * r ** 3, 4 / 3 * math.pi * r2 ** 3
    assert m["volume_ref_mm3"] == pytest.approx(v1, rel=3 * e1 / r)
    assert m["volume_test_mm3"] == pytest.approx(v2, rel=3 * e2 / r2)
    dice = 2 * r ** 3 / (r ** 3 + r2 ** 3)
    s = (r ** 3 + r2 ** 3) ** 2
    tol = 6 * r ** 2 * r2 ** 3 / s * e1 + 6 * r ** 3 * r2 ** 2 / s * e2  # ~0.02
    assert abs(m["dice"] - dice) < tol
    # Distances between two staircase surfaces: within half the coarse floor of d.
    assert abs(m["msd_mm"] - d) < floor / 2
    assert abs(m["signed_bias_mm"] - d) < floor / 2 and m["signed_bias_mm"] > 0
    assert d - floor / 2 < m["hausdorff_mm"] < d + floor
    assert m["hd95_mm"] <= m["hausdorff_mm"]
    # Swapping roles flips the bias sign only.
    m2 = V.overlap_metrics(_fill(g_test, _sphere((0, 0, 0), r + d)), _fill(g_ref, _sphere((0, 0, 0), r)))
    assert m2["signed_bias_mm"] == pytest.approx(-m["signed_bias_mm"], abs=0.05)


def test_same_sphere_fine_vs_rotated_coarse_partial_fov():
    """Same object, fine axial vs oblique 2 mm slab cutting through it: agreement below the floor."""
    ref = _fill(FINE, _sphere((0, 0, 0), 12.0))
    test = _fill(COARSE, _sphere((0, 0, 0), 12.0))
    m = V.overlap_metrics(ref, test)
    floor = m["voxel_floor_mm"]
    assert floor == pytest.approx(0.5 * math.sqrt(2 * 0.47 ** 2 + 4.0))
    assert m["dice_grid"] == "test"
    assert min(m["n_surface_points"]) > 1000
    assert min(m["common_fov_mm"]) < 18.0  # the 16 mm oblique slab really limits the comparison
    assert m["msd_mm"] < floor / 2 and m["hd95_mm"] < floor and m["hausdorff_mm"] < 1.5 * floor
    assert abs(m["signed_bias_mm"]) < 0.1 * floor
    assert m["dice"] > 0.97 and m["dice"] >= m["dice_nearest"] - 0.005


def test_shift_and_mirror_are_detected():
    ref = _fill(FINE, _sphere((0, 0, 0), 12.0))
    floor = V.accuracy_floor(COARSE.GetSpacing())["half_diagonal_mm"]
    shifted = V.overlap_metrics(ref, _fill(COARSE, _sphere((3.0, 0, 0), 12.0)))
    assert shifted["hd95_mm"] > 2 * floor and shifted["hausdorff_mm"] > 2.5
    # Asymmetric object (ball with a bump at +x) against its left-right mirror image.
    obj = lambda p: _sphere((0, 0, 0), 9.0)(p) | _sphere((8.0, 0, -4.0), 5.0)(p)  # noqa: E731
    mir = lambda p: obj(p * np.array([-1.0, 1.0, 1.0]))  # noqa: E731
    ok = V.overlap_metrics(_fill(FINE, obj), _fill(COARSE, obj))
    bad = V.overlap_metrics(_fill(FINE, obj), _fill(COARSE, mir))
    assert ok["hd95_mm"] < floor
    # Most of the ball still overlaps, so the mean barely moves; the tail (HD95) and Dice show the flip.
    assert bad["hd95_mm"] > 3 * floor and bad["msd_mm"] > 2 * ok["msd_mm"] and bad["dice"] < ok["dice"] - 0.05


def test_empty_and_disjoint_masks():
    g = _grid((30, 30, 30), (1.0, 1.0, 1.0))
    empty = _fill(g, lambda p: np.zeros(len(p), bool))
    ball = _fill(g, _sphere((0, 0, 0), 6.0))
    m = V.overlap_metrics(empty, empty)
    assert math.isnan(m["dice"]) and math.isnan(m["msd_mm"]) and "empty" in m["note"]
    m = V.overlap_metrics(ball, empty)
    assert m["dice"] == 0.0 and m["volume_test_mm3"] == 0.0 and math.isnan(m["msd_mm"])
    far = _grid((10, 10, 10), (1.0, 1.0, 1.0), (500.0, 0.0, 0.0))
    m = V.overlap_metrics(ball, _fill(far, lambda p: np.ones(len(p), bool)))
    assert m["common_fov_volume_mm3"] == 0.0 and "overlap" in m["note"]


def test_threshold_sweep_analytic():
    """Sphere whose radius shrinks 0.2 mm per 100 HU: shift and % change are known exactly."""
    r0, k = 10.0, -0.002  # mm per HU

    def seg(t):
        r = r0 + k * (t - 300.0)
        return {"bone": 4 / 3 * math.pi * r ** 3, "half": 2 / 3 * math.pi * r ** 3}

    out = V.threshold_sweep(seg, [200, 250, 350, 400], 300, areas_mm2={"bone": 4 * math.pi * r0 ** 2})
    assert out["thresholds_hu"] == [200.0, 250.0, 300.0, 350.0, 400.0] and len(out["table"]) == 5
    b = out["structures"]["bone"]
    assert b["volume_at_reference_mm3"] == pytest.approx(4 / 3 * math.pi * 1000)
    assert b["pct_per_100hu"] == pytest.approx(100 * 3 * k * 100 / r0, rel=1e-3)  # dV/V = 3 dr/r
    assert b["shift_mm_per_100hu"] == pytest.approx(k * 100, rel=1e-3)
    assert b["range_shift_mm"] == pytest.approx(0.4, rel=0.01)
    assert b["range_pct"] == pytest.approx(100 * ((10.2 ** 3 - 9.8 ** 3) / 1000), rel=1e-9)
    assert "shift_mm_per_100hu" not in out["structures"]["half"]


@pytest.fixture(scope="module")
def phantom():
    return make_phantom()


def test_hu_sanity_phantom_passes_and_shift_fails(phantom):
    h = V.hu_sanity(phantom.image, phantom.truth)
    assert h["ok"]
    assert h["air"]["value"] == pytest.approx(-1000, abs=10)
    assert h["fat"]["value"] == pytest.approx(-100, abs=10) and h["muscle"]["value"] == pytest.approx(55, abs=10)
    assert h["max"]["scale"] == "extended" and not h["max"]["clipped"]
    assert V.hu_sanity(phantom.image)["ok"]  # no labels: body from HU > -500
    bad = V.hu_sanity(phantom.image + 200, phantom.truth)
    assert not bad["ok"] and not bad["air"]["ok"] and not bad["fat"]["ok"] and not bad["muscle"]["ok"]
    clipped = V.hu_sanity(sitk.Clamp(phantom.image, sitk.sitkInt16, -1024, 3071), phantom.truth)
    assert clipped["max"]["clipped"] and not clipped["max"]["ok"]


def test_bone_sanity_phantom(phantom):
    names = L.default_names(2, ["radius", "ulna"])
    b = V.bone_sanity(phantom.truth, names)
    sx, _, sz = phantom.truth.GetSpacing()
    for k, name in enumerate(["radius", "ulna"]):
        row, truth = b[name], phantom.analytic["bones"][k]
        assert abs(row["length_mm"] - phantom.analytic["bone_length_mm"]) <= sz
        # Volume: mean outline error of a quarter pixel on a disc of radius r -> relative 2 (sx/4) / r.
        assert row["volume_cm3"] * 1000 == pytest.approx(truth["volume_mm3"], rel=2 * (sx / 4) / truth["outer_radius_mm"])
        assert abs(row["median_diameter_mm"] - 2 * truth["outer_radius_mm"]) < sx / 2
        assert row["n_components"] == 1 and row["touches_border"] == [] and 0 < row["cortical_fraction"] < 1
        assert abs(abs(row["axis_lps"][2]) - 1) < 1e-6
        assert not row["expected"]["length_mm"]["in_band"]  # 48 mm phantom vs adult band: information only
    cut = V.bone_sanity(phantom.truth[:, :, 10:30], names)  # crop in z through the bones
    assert set(cut["radius"]["touches_border"]) == {"z_min", "z_max"}
    assert any("lower bound" in f for f in cut["radius"]["flags"])
    assert cut["radius"]["length_mm"] == pytest.approx(20 * sz)


def test_bone_sanity_flags_missing_medullary_and_split(phantom):
    a = sitk.GetArrayFromImage(phantom.truth)
    a[a == L.medullary(0)] = L.cortical(0)
    a[18:20][np.isin(a[18:20], [L.cortical(0)])] = L.MUSCLE  # cut bone 0 into two pieces
    img = sitk.GetImageFromArray(a)
    img.CopyInformation(phantom.truth)
    r = V.bone_sanity(img, L.default_names(2, ["radius", "ulna"]))["radius"]
    assert r["n_components"] == 2 and "medullary volume is zero" in r["flags"]
    assert any("2 connected pieces" in f for f in r["flags"])


def _mc_mesh(mask: sitk.Image):
    a = sitk.GetArrayViewFromImage(mask).astype(np.float32)
    s = np.asarray(mask.GetSpacing())
    v, f, _, _ = marching_cubes(a, 0.5, spacing=tuple(s[::-1]))
    return V._to_phys(mask, v[:, ::-1] / s), f


def test_mesh_vs_mask(phantom):
    a = np.pad((sitk.GetArrayFromImage(phantom.truth) >= L.BONE_BASE).astype(np.uint8), 1)
    mask = sitk.GetImageFromArray(a)
    s = np.asarray(phantom.truth.GetSpacing())
    mask.SetSpacing(tuple(s))
    mask.SetOrigin(tuple(np.asarray(phantom.truth.GetOrigin()) - s))
    v, f = _mc_mesh(mask)
    m = V.mesh_vs_mask(v, f, mask)
    assert m["vertex_max_mm"] < 1e-9  # marching-cubes vertices sit on the voxel faces
    assert m["mean_abs_mm"] < m["voxel_floor_mm"] / 4 and abs(m["signed_mean_mm"]) < 0.05
    n = trimesh.Trimesh(v, f, process=False).vertex_normals
    infl = V.mesh_vs_mask(v + 1.0 * n, f, mask)
    assert 0.75 < infl["signed_mean_mm"] < 1.05 and 0.75 < infl["vertex_signed_mean_mm"] < 1.05
    shrunk = V.mesh_vs_mask(v - 1.0 * n, f, mask)
    assert -1.05 < shrunk["signed_mean_mm"] < -0.75


def test_mesh_vs_mask_counts_the_cap_where_the_scan_cuts_a_solid():
    # A rod that runs through the whole array is cut at both ends. The mesh is closed there (zero border before
    # marching cubes, as in mesh.mask_to_mesh), so the mask's surface must include its end faces too. Before the
    # fix, the cap's centre was measured to the side wall: about the rod radius (6 mm), not zero.
    img = _fill(_grid((40, 40, 30), (0.5, 0.5, 0.5)), lambda p: p[:, 0] ** 2 + p[:, 1] ** 2 <= 36.0)
    a = np.pad(sitk.GetArrayFromImage(img).astype(np.float32), 1)
    s = np.asarray(img.GetSpacing())
    v, f, _, _ = marching_cubes(a, 0.5, spacing=tuple(s[::-1]))
    v = V._to_phys(img, v[:, ::-1] / s - 1.0)
    m = V.mesh_vs_mask(v, f, img)
    assert m["max_mm"] < m["voxel_floor_mm"]
    assert m["vertex_max_mm"] < 1e-9
    # the same mask surface without the end faces misses the caps
    arr = sitk.GetArrayViewFromImage(img)
    full = tuple(slice(0, n) for n in arr.shape)
    open_pts, _, _ = V._surface_samples(arr, V._fg_slices(arr, full, 1), img, 0.25, odd=True, closed=False)
    from scipy.spatial import cKDTree
    assert cKDTree(open_pts).query(v)[0].max() > 4.0


def test_enclosed_tissue_finds_a_box_inside_the_bone_and_ignores_metal():
    a = np.zeros((40, 30, 30), np.uint8)
    zz, yy, xx = np.mgrid[:40, :30, :30]
    r2 = (yy - 15) ** 2 + (xx - 15) ** 2
    a[(r2 <= 100) & (zz >= 2) & (zz < 38)] = L.cortical(0)
    a[(r2 <= 49) & (zz >= 4) & (zz < 36)] = L.medullary(0)
    a[10:16, 12:18, 12:18] = L.MUSCLE  # a 6 x 6 x 6 box of tissue inside the marrow: the radius defect
    a[25:30, 14:16, 14:16] = L.METAL  # metal inside the bone is not tissue
    img = sitk.GetImageFromArray(a)
    img.SetSpacing((0.5, 0.5, 0.5))
    res = V.enclosed_tissue(img, L.default_names(1, ["radius"]))
    r = res["radius"]
    assert abs(r["enclosed_cm3"] - 216 * 0.125 / 1000) < 1e-9 and r["n_regions"] == 1
    assert r["largest"][0]["box_fill"] == 1.0 and r["largest"][0]["labels"] == [L.MUSCLE]
    assert r["flags"] and "straight-edged" in r["flags"][0]


def test_markdown_report(phantom):
    a = _fill(_grid((40, 40, 40), (0.5, 0.5, 0.5)), _sphere((0, 0, 0), 6.0))
    res = {"accuracy_floor": {"primary": V.accuracy_floor((0.468, 0.468, 0.3))},
           "overlap": {"axial vs coronal 1": V.overlap_metrics(a, a)},
           "threshold_sweep": V.threshold_sweep(lambda t: {"bone": 1000.0 - t}, [200, 400], 300),
           "hu_sanity": V.hu_sanity(phantom.image, phantom.truth),
           "bone_sanity": V.bone_sanity(phantom.truth, L.default_names(2, ["radius", "ulna"]))}
    md = V.format_validation_markdown(res)
    for h in ("## Voxel-size floor", "## Agreement between reconstructions", "## Threshold sensitivity",
              "## HU calibration", "## Bone plausibility"):
        assert h in md
    assert md.count("does not") + md.count("do not") >= 5
    assert "(mm)" in md and "(cm3)" in md and " HU" in md
