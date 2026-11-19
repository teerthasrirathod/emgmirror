"""Tests for emgmirror.ct.mesh (synthetic data only)."""

import numpy as np
import pytest
import SimpleITK as sitk
import trimesh

from emgmirror.ct import labels as L
from emgmirror.ct import mesh as M
from emgmirror.ct.phantom import make_phantom

NO_DECIM = dict(max_faces=None)


@pytest.fixture(scope="module")
def phantom():
    return make_phantom()


@pytest.fixture(scope="module")
def bone0(phantom):
    ids = [L.cortical(0), L.medullary(0)]
    mesh, info = M.labels_to_mesh(phantom.truth, ids, M.MeshParams(**NO_DECIM))
    raw, raw_info = M.labels_to_mesh(phantom.truth, ids, M.MeshParams(smooth_iterations=0, **NO_DECIM))
    return mesh, info, raw, raw_info


def test_bone_mesh_quality_and_volume(phantom, bone0):
    mesh, info, raw, raw_info = bone0
    qc = M.mesh_qc(mesh, info["voxel_volume_mm3"], raw)
    assert qc["watertight"] and qc["winding_consistent"]
    assert qc["euler_number"] == 2 and qc["n_components"] == 1
    assert qc["self_intersections"] == 0
    assert mesh.volume > 0
    # Truth grid is 0.9 x 0.9 x 1.5 mm for a 6 mm radius cylinder. Measured: raw -0.3 %, smoothed +0.3 % vs the analytic
    # volume; the voxelisation itself is within 0.1 % of it. 2 % leaves room for platform differences yet would
    # still catch a half-voxel offset error (about 9 % in volume at this radius).
    an = phantom.analytic["bones"][0]["volume_mm3"]
    assert abs(raw.volume - an) / an < 0.02
    assert abs(mesh.volume - an) / an < 0.02
    assert abs(qc["volume_vs_voxels_pct"]) < 1.5
    assert abs(info["volume_change_pct"]) < 1.5
    assert info["smoothing_displacement_max_mm"] < 1.0  # under one in-plane voxel
    assert 0 < info["smoothing_displacement_mean_mm"] <= info["smoothing_displacement_max_mm"]
    assert qc["hausdorff_to_raw_mm"] < 1.0
    # physical bounding box of the volume (origin is not zero)
    img = phantom.truth
    lo = np.array(img.GetOrigin()) - 1e-6
    hi = lo + np.array(img.GetSize()) * np.array(img.GetSpacing()) + 2e-6
    assert (mesh.vertices >= lo).all() and (mesh.vertices <= hi).all()
    assert (mesh.bounds[0] > lo + 5).all()  # clearly inside, i.e. not shifted to a zero origin


def test_taubin_shrinks_less_than_laplacian(bone0):
    mesh, info, raw, _ = bone0
    lap = M.taubin_smooth(raw.vertices, raw.faces, 20, 0.5, 0.0)  # positive steps only
    v_lap = trimesh.Trimesh(lap, raw.faces, process=False).volume
    lap_change = (v_lap - raw.volume) / raw.volume * 100
    assert abs(info["volume_change_pct"]) < 1.5
    assert lap_change < -5.0  # measured about -12 %
    assert abs(lap_change) > 5 * abs(info["volume_change_pct"])


def test_taubin_zero_iterations_and_shapes():
    v = np.random.default_rng(0).normal(size=(10, 3))
    assert np.array_equal(M.taubin_smooth(v, np.zeros((0, 3), int), 5), v)
    s = trimesh.creation.icosphere(subdivisions=2)
    out = M.taubin_smooth(s.vertices, s.faces, 10)
    assert out.shape == s.vertices.shape and np.isfinite(out).all()


def test_non_identity_direction():
    # 90-degree rotation about z: index x -> physical y, index y -> physical -x; anisotropic spacing
    D = (0, 1, 0, -1, 0, 0, 0, 0, 1)
    shape = (30, 40, 50)  # z, y, x
    img = sitk.Image(shape[2], shape[1], shape[0], sitk.sitkUInt8)
    img.SetSpacing((0.5, 0.7, 1.2))
    img.SetOrigin((10.0, -20.0, 5.0))
    img.SetDirection(D)
    mask = np.zeros(shape, bool)
    mask[8:20, 10:28, 5:35] = True
    mesh, info = M.mask_to_mesh(mask, img, M.MeshParams(smooth_iterations=0, **NO_DECIM))
    zs, ys, xs = np.nonzero(mask)
    pts = np.array([img.TransformIndexToPhysicalPoint((int(x), int(y), int(z))) for x, y, z in zip(xs, ys, zs)])
    centroid = pts.mean(axis=0)
    assert np.allclose(mesh.vertices.mean(axis=0), centroid, atol=0.25)  # mesh vertices are not area-uniform
    assert np.allclose(mesh.centroid, centroid, atol=1e-6) or np.linalg.norm(mesh.center_mass - centroid) < 1e-6
    assert mesh.volume > 0 and mesh.is_watertight and mesh.is_winding_consistent
    assert abs(info["raw_volume_mm3"] - info["voxel_volume_mm3"]) / info["voxel_volume_mm3"] < 0.02
    # a reflecting (det = -1) direction must still yield outward normals
    img.SetDirection((0, 1, 0, 1, 0, 0, 0, 0, 1))
    mesh2, _ = M.mask_to_mesh(mask, img, M.MeshParams(smooth_iterations=0, **NO_DECIM))
    assert mesh2.volume > 0 and mesh2.is_winding_consistent


def test_anisotropic_sphere_extents():
    sp = (0.468, 0.468, 0.3)
    r = 6.0
    n = (int(2 * r / sp[2]) + 10, int(2 * r / sp[1]) + 10, int(2 * r / sp[0]) + 10)
    img = sitk.Image(n[2], n[1], n[0], sitk.sitkUInt8)
    img.SetSpacing(sp)
    c = np.array([(n[2] - 1) / 2, (n[1] - 1) / 2, (n[0] - 1) / 2]) * sp
    z, y, x = np.ogrid[:n[0], :n[1], :n[2]]
    mask = (x * sp[0] - c[0]) ** 2 + (y * sp[1] - c[1]) ** 2 + (z * sp[2] - c[2]) ** 2 <= r * r
    mesh, info = M.mask_to_mesh(mask, img, M.MeshParams(**NO_DECIM))
    ext = mesh.extents
    assert np.all(np.abs(ext - 2 * r) <= max(sp)), ext
    assert np.ptp(ext) <= max(sp)
    assert abs(mesh.volume - 4 / 3 * np.pi * r ** 3) / (4 / 3 * np.pi * r ** 3) < 0.02
    assert np.allclose(mesh.bounds.mean(axis=0), c, atol=max(sp))


def test_empty_mask(phantom):
    mask = np.zeros(sitk.GetArrayViewFromImage(phantom.truth).shape, bool)
    mesh, info = M.mask_to_mesh(mask, phantom.truth)
    assert len(mesh.vertices) == 0 and len(mesh.faces) == 0
    assert info["voxel_count"] == 0 and info["volume_mm3"] == 0 and info["n_faces"] == 0 and not info["decimated"]
    mesh, info = M.labels_to_mesh(phantom.truth, [99])
    assert len(mesh.faces) == 0
    assert M.mesh_qc(mesh)["watertight"] is False


def test_border_touching_mask_is_closed(phantom):
    mask = np.zeros(sitk.GetArrayViewFromImage(phantom.truth).shape, bool)
    mask[:, :20, :20] = True
    mesh, info = M.mask_to_mesh(mask, phantom.truth, M.MeshParams(smooth_iterations=0, **NO_DECIM))
    assert mesh.is_watertight and mesh.volume > 0
    assert abs(mesh.volume - info["voxel_volume_mm3"]) / info["voxel_volume_mm3"] < 0.05


def test_min_component_voxels(phantom):
    shape = sitk.GetArrayViewFromImage(phantom.truth).shape
    mask = np.zeros(shape, bool)
    mask[5:25, 10:30, 10:30] = True
    mask[35, 60, 60] = True  # one-voxel island
    p = dict(smooth_iterations=0, **NO_DECIM)
    assert M.mesh_qc(M.mask_to_mesh(mask, phantom.truth, M.MeshParams(**p))[0])["n_components"] == 2
    mesh, _ = M.mask_to_mesh(mask, phantom.truth, M.MeshParams(min_component_voxels=10, **p))
    assert M.mesh_qc(mesh)["n_components"] == 1


def test_self_intersections_negative_and_positive():
    s = trimesh.creation.icosphere(subdivisions=3)
    assert M.self_intersections(s) == 0
    # two interpenetrating spheres in one mesh (offset off-axis so no symmetric degeneracy)
    s2 = s.copy()
    s2.apply_translation([0.43, 0.21, 0.11])
    both = trimesh.util.concatenate([s, s2])
    assert M.self_intersections(both, max_pairs=10 ** 6) > 20
    assert M.self_intersections(both, max_pairs=5) == 5  # early stop at the cap
    # vertex pulled through the opposite wall
    bad = s.copy()
    k = int(np.argmax(bad.vertices[:, 0]))
    bad.vertices[k] = [-1.5, 0.05, 0.02]
    assert M.self_intersections(bad) > 0
    # disjoint spheres do not intersect
    s3 = s.copy()
    s3.apply_translation([3, 0, 0])
    assert M.self_intersections(trimesh.util.concatenate([s, s3])) == 0


def test_self_intersections_chunking_matches():
    s = trimesh.creation.icosphere(subdivisions=3)
    s2 = s.copy()
    s2.apply_translation([0.43, 0.21, 0.11])
    both = trimesh.util.concatenate([s, s2])
    a = M.self_intersections(both, max_pairs=10 ** 6)
    b = M.self_intersections(both, max_pairs=10 ** 6, chunk_pairs=500)
    assert a == b > 0


def test_decimation(phantom):
    ids = [L.cortical(0), L.medullary(0)]
    full, info0 = M.labels_to_mesh(phantom.truth, ids, M.MeshParams(**NO_DECIM))
    target = len(full.faces) // 3
    mesh, info = M.labels_to_mesh(phantom.truth, ids, M.MeshParams(max_faces=target))
    assert info["decimated"] and not info0["decimated"]
    assert len(mesh.faces) <= target * 1.05 and len(mesh.faces) < len(full.faces)
    assert info["n_faces"] == len(mesh.faces)
    qc = M.mesh_qc(mesh, info["voxel_volume_mm3"], full)
    assert qc["watertight"] and qc["winding_consistent"]
    assert qc["self_intersections"] == 0
    assert abs(info["decimation_volume_change_pct"]) < 2.0
    assert abs(info["volume_change_pct"]) < 2.5
    assert info["volume_mm3"] == pytest.approx(mesh.volume)
    assert "decimation" in info["timings_s"]


def test_export_round_trip(tmp_path, phantom, bone0):
    bone, _, _, _ = bone0
    mus, _ = M.labels_to_mesh(phantom.truth, [L.cortical(1), L.medullary(1)], M.MeshParams(max_faces=20000))
    meshes = {"bone_1": bone, "muscle": mus}
    res = M.export_meshes(meshes, tmp_path, {"bone_1": "#efe9dc", "muscle": "#b5524c"})
    for name, m in meshes.items():
        for ext in ("stl", "obj", "ply"):
            assert (tmp_path / f"{name}.{ext}").stat().st_size > 0
            assert res[name][ext] == str(tmp_path / f"{name}.{ext}")
        with open(res[name]["stl"], "rb") as f:
            head = f.read(80)
        assert not head.startswith(b"solid")  # binary STL
        stl = trimesh.load(res[name]["stl"])
        assert np.allclose(stl.bounds, m.bounds, atol=1e-3)
        assert stl.volume == pytest.approx(m.volume, rel=1e-4)
        ply = trimesh.load(res[name]["ply"])
        assert ply.volume == pytest.approx(m.volume, rel=1e-4)
        obj = trimesh.load(res[name]["obj"])
        assert np.allclose(obj.bounds, m.bounds, atol=1e-3)
    scene = trimesh.load(res["combined_glb"])
    assert set(scene.geometry) == {"bone_1", "muscle"} or len(scene.geometry) == 2
    by_name = {}
    for node in scene.graph.nodes_geometry:
        tf, gname = scene.graph[node]
        g = scene.geometry[gname].copy()
        g.apply_transform(tf)
        by_name[node] = g
    assert set(by_name) == {"bone_1", "muscle"}
    for name, m in meshes.items():
        g = by_name[name]
        assert np.allclose(g.bounds, m.bounds, atol=1e-3), (name, g.bounds, m.bounds)  # float32 in glTF
        assert g.volume == pytest.approx(m.volume, rel=1e-4)
        assert not np.allclose(m.bounds[0], 0)  # patient coordinates, not recentred
    mat = scene.geometry[scene.graph[list(by_name)[0]][1]].visual.material
    assert mat.baseColorFactor is not None
    readme = (tmp_path / "README.txt").read_text()
    assert "mm" in readme and "LPS" in readme
    assert "Research use, not for diagnosis or surgical planning." in readme


def test_edge_pieces_count_links_through_non_manifold_edges():
    # three triangles on one shared edge (a non-manifold "fan") plus one triangle that only touches at a vertex
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [1, 1, 1], [2, 1, 1]], float)
    f = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4], [1, 5, 6]])
    q = M.mesh_qc(trimesh.Trimesh(v, f, process=False))
    assert q["n_components"] == 1  # all joined through vertex 1
    assert q["n_components_edge"] == 2  # the fan is one piece through its shared edge; the last face is separate


def test_every_mesh_file_carries_the_disclaimer(tmp_path, bone0):
    """A mesh copied out of the results folder keeps its label: STL header, OBJ/PLY comment, glTF asset extras."""
    import json
    import struct

    from emgmirror.ct import DISCLAIMER
    bone, _, _, _ = bone0
    res = M.export_meshes({"bone_1": bone}, tmp_path)
    assert DISCLAIMER.encode() in (tmp_path / "bone_1.stl").read_bytes()[:80]
    assert (tmp_path / "bone_1.obj").read_text().splitlines()[0] == f"# {M.FILE_NOTE}"
    ply = (tmp_path / "bone_1.ply").read_bytes()
    assert b"comment " + DISCLAIMER.encode() in ply[:ply.index(b"end_header")]
    glb = (tmp_path / "combined.glb").read_bytes()
    n = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:20 + n])
    assert doc["asset"]["extras"]["disclaimer"] == DISCLAIMER and doc["asset"]["version"] == "2.0"
    assert struct.unpack_from("<I", glb, 8)[0] == len(glb) and n % 4 == 0
    for ext in ("stl", "obj", "ply"):  # still valid files with the same geometry
        assert trimesh.load(res["bone_1"][ext]).volume == pytest.approx(bone.volume, rel=1e-4)
    assert len(trimesh.load(res["combined_glb"]).geometry) == 1
