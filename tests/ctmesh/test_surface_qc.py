import numpy as np
import pytest
import trimesh

from emgmirror.ctmesh import qc, surface
from emgmirror.ctmesh.contract import Label, LabelMap
from emgmirror.ctmesh.decimate import manifold_ok


def _lm(arr, spacing=(1.0, 1.0, 1.0), direction=None):
    ids = [int(i) for i in np.unique(arr) if i]
    return LabelMap(arr.astype(np.uint16), spacing, (0.0, 0.0, 0.0),
                    np.eye(3) if direction is None else direction, [Label(i, f"l{i}") for i in ids])


def test_every_phantom_label_is_closed_manifold_and_within_one_voxel(phantom):
    """The surface the pipeline keeps (SurfaceNets, or the flying-edges fallback where SurfaceNets leaves an
    unsplittable non-manifold edge) is closed, manifold, free of self-intersections and within one voxel."""
    from emgmirror.ctmesh.pipeline import PipelineParams, _choose_native
    lm = phantom.labelmap
    surfs = surface.surfacenets_all(lm, [lab.id for lab in lm.labels])
    assert set(surfs) == {lab.id for lab in lm.labels}
    vmm = qc.voxel_mm(lm.spacing)
    methods = []
    for lid, s0 in surfs.items():
        B = qc.label_boundary(lm.array, lid, lm.index_to_lps)
        s, rep, _ = _choose_native(lm, lid, s0, B, PipelineParams())
        methods.append(s.method)
        m = s.mesh
        assert rep["passed"], (lm.label(lid).name, rep["issues"])
        assert m.is_watertight and m.is_winding_consistent, lm.label(lid).name
        assert m.volume > 0  # outward normals, also on the improper (det -1) grid
        assert manifold_ok(m)
        assert qc.self_intersections(m) == 0
        d = qc.surface_distance(m.vertices, m.faces, *B, vmm)
        assert d["hd95_mm"] <= vmm, (lm.label(lid).name, d)
        dv = (m.volume - s.voxel_volume_mm3) / s.voxel_volume_mm3 * 100
        assert abs(dv) < 8.0, (lm.label(lid).name, dv)
    assert methods.count("surfacenets") >= len(methods) - 1


def test_known_point_lands_in_patient_lps(phantom):
    """The marker sphere's centre is defined in LPS mm; the mesh centroid must land there on any grid."""
    lm = phantom.labelmap
    s = surface.surfacenets_all(lm, [2])[2]
    c = s.mesh.center_mass
    assert np.linalg.norm(c - phantom.truth["marker_sphere"]["centre"]) < 0.35
    e = surface.surfacenets_all(lm, [1])[1].mesh
    assert np.linalg.norm(e.center_mass - phantom.truth["ellipsoid"]["centre"]) < 0.3
    assert abs(e.volume - phantom.truth["ellipsoid"]["volume_mm3"]) / phantom.truth["ellipsoid"]["volume_mm3"] < 0.03


def test_smoothing_removes_terraces_without_shrinking():
    """Sphere on a 1.5 mm grid: smoothed normals follow the true sphere, volume stays put."""
    n, h, r = 30, 1.5, 15.0
    g = (np.arange(n) - (n - 1) / 2) * h
    Z, Y, X = np.meshgrid(g, g, g, indexing="ij")
    arr = (X ** 2 + Y ** 2 + Z ** 2 <= r * r).astype(np.uint16)
    lm = _lm(arr, (h, h, h))
    lm.origin = (g[0], g[0], g[0])
    for params, max_err in ((surface.SurfaceParams(smoothing_iterations=0), 30.0), (surface.SurfaceParams(), 10.0)):
        m = surface.surfacenets_all(lm, [1], params)[1].mesh
        radial = m.triangles_center / np.linalg.norm(m.triangles_center, axis=1, keepdims=True)
        ang = np.degrees(np.arccos(np.clip((m.face_normals * radial).sum(1), -1, 1)))
        if max_err > 20:
            assert ang.mean() > max_err  # unsmoothed = staircase
        else:
            assert ang.mean() < max_err
            assert abs(m.volume - 4 / 3 * np.pi * r ** 3) / (4 / 3 * np.pi * r ** 3) < 0.02


def test_diagonal_voxels_are_split_into_a_manifold():
    arr = np.zeros((6, 6, 6), np.uint16)
    arr[2, 2, 2] = arr[3, 3, 2] = 1  # edge contact
    arr[2, 2, 4] = arr[3, 3, 5] = 2  # corner contact... plus a face neighbour to make it non-trivial
    arr[2, 3, 4] = 2
    s = surface.surfacenets_all(_lm(arr), [1, 2], surface.SurfaceParams(smoothing_iterations=0))
    assert s[1].split_vertices > 0
    for lid in (1, 2):
        m = s[lid].mesh
        assert m.is_watertight and m.volume > 0
        assert qc.self_intersections(m) == 0


def test_touching_labels_meet_without_gap_or_overlap(oblique):
    """Touching labels come from one SurfaceNets run, so their interface starts from identical vertices; after
    per-label smoothing (clamped at 0.45 voxel per axis) the two sides stay within 0.9 voxel of each other."""
    lm = oblique.labelmap
    s = surface.surfacenets_all(lm, [5, 6])
    raw = surface.surfacenets_all(lm, [5, 6], surface.SurfaceParams(smoothing_iterations=0))
    a, b = s[5].mesh, s[6].mesh
    assert len(raw[5].mesh.vertices) == len(a.vertices)  # same vertices, only moved
    on_iface = qc.distance_to_surface(raw[5].mesh.vertices, raw[6].mesh.vertices, raw[6].mesh.faces) < 1e-6
    assert on_iface.sum() > 20
    d_ab = qc.distance_to_surface(a.vertices[on_iface], b.vertices, b.faces)
    assert d_ab.max() <= 2 * 0.45 * np.linalg.norm(lm.spacing)
    vox = sum(np.count_nonzero(lm.array == i) for i in (5, 6)) * lm.voxel_volume_mm3
    assert abs(a.volume + b.volume - vox) / vox < 0.05  # no net overlap or gap


def test_scan_edge_is_capped_and_flagged():
    arr = np.zeros((10, 10, 10), np.uint16)
    arr[:5, 3:7, 3:7] = 1  # touches z = 0
    arr[6:9, 3:6, 3:6] = 2
    s = surface.surfacenets_all(_lm(arr), [1, 2])
    assert s[1].cut_by_scan_edge and not s[2].cut_by_scan_edge
    assert s[1].mesh.is_watertight


def test_flying_edges_fallback_is_closed(oblique):
    lm = oblique.labelmap
    fe = surface.flyingedges_one(lm, 3)
    assert fe.method == "flyingedges" and fe.mesh.is_watertight and fe.mesh.volume > 0
    B = qc.label_boundary(lm.array, 3, lm.index_to_lps)
    assert qc.surface_distance(fe.mesh.vertices, fe.mesh.faces, *B, 1.0)["hd95_mm"] <= qc.voxel_mm(lm.spacing)


def test_label_depth():
    m = np.zeros((7, 7, 7), bool)
    m[3, 1:6, 1:6] = True
    assert surface.label_depth_voxels(m) == 1.0
    m[2:5, 1:6, 1:6] = True
    assert surface.label_depth_voxels(m) == 2.0


def test_ported_self_intersection_count():
    a = trimesh.creation.box((10, 10, 10))
    b = trimesh.creation.box((10, 10, 10))
    b.apply_translation([5, 5, 5])
    assert qc.self_intersections(a) == 0
    assert qc.self_intersections(trimesh.util.concatenate([a, b])) > 0


def test_surface_distance_and_gate():
    a = trimesh.creation.icosphere(3, 10.0)
    b = a.copy()
    b.apply_translation([0.5, 0, 0])
    d = qc.surface_distance(a.vertices, a.faces, b.vertices, b.faces, 1.0)
    assert 0.3 < d["hd95_mm"] <= 0.51 and d["mean_dist_mm"] < 0.5
    ok, why = qc.gate({"watertight": True, "self_intersections": 0, "hd95_mm": 0.9}, (1, 1, 1))
    assert ok and not why
    ok, why = qc.gate({"watertight": False, "self_intersections": 2, "hd95_mm": 1.5}, (1, 1, 1))
    assert not ok and len(why) == 3


def test_voxel_boundary_of_one_voxel():
    m = np.zeros((3, 3, 3), bool)
    m[1, 1, 1] = True
    V, F = qc.voxel_boundary(m, np.diag([2.0, 3.0, 4.0, 1.0]))
    assert len(F) == 12 and len(V) == 8
    t = trimesh.Trimesh(V, F, process=False)
    assert np.isclose(t.area, 2 * (6 + 8 + 12))


@pytest.mark.parametrize("vox", [0.5, 1.0])
def test_old_and_new_qc_port_agree(vox):
    from emgmirror.ct import mesh as old
    m = trimesh.creation.icosphere(3, 5.0)
    assert qc.mesh_qc(m)["self_intersections"] == old.mesh_qc(m)["self_intersections"] == 0
    assert qc.mesh_qc(m)["watertight"] and qc.mesh_qc(m, vox)["volume_vs_voxels_pct"] is not None
