import json

import numpy as np
import pytest
import trimesh

from emgmirror.ctmesh import DISCLAIMER, LPS_TO_GLTF, decimate, gltf
from emgmirror.ctmesh.geometry import gltf_to_lps


def test_glb_round_trip_both_encodings(tmp_path):
    m = trimesh.creation.icosphere(4, 20.0)
    m.apply_translation([10.0, -50.0, 300.0])
    for compress in (False, True):
        p = gltf.write_glb(tmp_path / f"s{compress}.glb", [gltf.GltfNode("sphere", m, (200, 30, 30), {"id": 7})],
                           compress=compress)
        doc, nodes = gltf.read_glb(p)
        P = gltf_to_lps(nodes[0]["positions_gltf"])
        back = trimesh.Trimesh(P, nodes[0]["faces"], process=False)
        assert back.is_watertight and np.isclose(back.volume, m.volume, rtol=1e-3)
        assert np.allclose(back.bounds, m.bounds, atol=0.01)  # within 10 micrometres
        assert nodes[0]["extras"]["id"] == 7
        assert doc["asset"]["extras"]["disclaimer"] == DISCLAIMER
        assert np.allclose(doc["asset"]["extras"]["lps_to_gltf"], LPS_TO_GLTF)
        if compress:
            assert set(doc["extensionsRequired"]) == {"EXT_meshopt_compression", "KHR_mesh_quantization"}
            assert all("EXT_meshopt_compression" in v["extensions"] for v in doc["bufferViews"])


def test_decimation_keeps_the_mesh_closed_and_close():
    m = trimesh.creation.icosphere(5, 30.0)
    d, rep = decimate.repair(decimate.simplify(m, 2000, 0.5))
    assert len(d.faces) <= 2000 and d.is_watertight and rep["manifold3d_ok"]
    assert abs(d.volume - m.volume) / m.volume < 0.01


def test_repair_drops_folded_fin():
    m = trimesh.creation.icosphere(2, 10.0)
    f = np.vstack([m.faces, m.faces[:1][:, ::-1], m.faces[:1]])  # a coincident, opposite pair
    r, rep = decimate.repair(trimesh.Trimesh(m.vertices, f, process=False))
    assert rep["removed_faces"] >= 2


def test_manifest_follows_the_s3_contract(pipeline_out, oblique):
    out, man = pipeline_out
    disk = json.loads((out / "manifest.json").read_text())
    assert disk == json.loads(json.dumps({k: v for k, v in man.items() if k != "_run_log"}))
    assert man["version"] == 1 and man["units"] == "m" and man["axes"] == "glTF (+Y up, from patient LPS)"
    assert np.allclose(man["lps_to_gltf"], LPS_TO_GLTF)
    assert man["combined"]["web"] == "combined_web.glb" and man["combined"]["print"] == "combined_print.glb"
    assert len(man["structures"]) == len(oblique.labelmap.labels)
    for s in man["structures"]:
        for k in ("id", "name", "display", "rgb", "files", "volume_ml", "surface_area_cm2", "qc"):
            assert k in s
        for k in ("watertight", "self_intersections", "hd95_mm", "mean_dist_mm", "volume_change_pct",
                  "cut_by_scan_edge"):
            assert k in s["qc"]
        assert s["qc"]["passed"], s
        assert s["qc"]["watertight"] and s["qc"]["self_intersections"] == 0
        for f in s["files"].values():
            assert (out / f).stat().st_size > 0
    for f in ("labels.nii.gz", "labels.json", "segmentation.seg.dcm", "combined_web.glb", "combined_print.glb",
              "combined_print.3mf", "qc.json"):
        assert (out / f).exists(), f


def test_marker_lands_in_the_right_place_in_every_export(pipeline_out, oblique):
    out, man = pipeline_out
    c_lps = oblique.truth["marker_sphere"]["centre"]
    s = next(x for x in man["structures"] if x["name"] == "marker_sphere")
    # STL: LPS mm
    stl = trimesh.load(out / s["files"]["stl"])
    assert np.linalg.norm(stl.center_mass - c_lps) < 0.4
    assert (out / s["files"]["stl"]).read_bytes()[:80].decode("ascii").startswith(DISCLAIMER)
    # 3MF: mm, LPS
    import zipfile
    assert DISCLAIMER in zipfile.ZipFile(out / s["files"]["3mf"]).read("3D/3dmodel.model").decode()
    tm = trimesh.load(out / s["files"]["3mf"], force="mesh")
    assert np.linalg.norm(tm.center_mass - c_lps) < 0.4
    # GLBs: metres, glTF axes
    c_gltf = (LPS_TO_GLTF @ np.r_[c_lps, 1])[:3]
    for name in ("combined_web.glb", "combined_print.glb", s["files"]["glb"]):
        doc, nodes = gltf.read_glb(out / name)
        n = next(x for x in nodes if x["name"] == "marker_sphere")
        mesh = trimesh.Trimesh(n["positions_gltf"], n["faces"], process=False)
        assert np.linalg.norm(mesh.center_mass - c_gltf) < 0.0004, name  # 0.4 mm
        assert n["extras"]["rgb"] == list(oblique.labelmap.label(2).rgb)


def test_dicom_seg_round_trips_labels_in_patient_space(pipeline_out, oblique):
    hd = pytest.importorskip("highdicom")
    out, _ = pipeline_out
    seg = hd.seg.segread(str(out / "segmentation.seg.dcm"))
    lm = oblique.labelmap
    assert len(seg.SegmentSequence) == len(lm.labels)
    vol = seg.get_volume(combine_segments=True)
    idx = np.argwhere(vol.array > 0)
    lps = idx @ vol.affine[:3, :3].T + vol.affine[:3, 3]
    ijk = np.rint((lps - lm.index_to_lps[:3, 3]) @ np.linalg.inv(lm.index_to_lps[:3, :3]).T).astype(int)
    ours = lm.array[ijk[:, 2], ijk[:, 1], ijk[:, 0]]
    names = {i + 1: seg.SegmentSequence[i].SegmentLabel for i in range(len(seg.SegmentSequence))}
    got = np.array([names[v] for v in vol.array[tuple(idx.T)]])
    want = np.array([lm.label(int(v)).display for v in ours])
    assert (got == want).mean() == 1.0
    assert np.count_nonzero(vol.array) == np.count_nonzero(lm.array)


def test_forearm_phantom_end_to_end(tmp_path):
    from emgmirror.ctmesh import fixtures
    from emgmirror.ctmesh.contract import save_labelmap
    from emgmirror.ctmesh.pipeline import PipelineParams, run
    p = fixtures.forearm_phantom(shape=(30, 60, 60))
    save_labelmap(p.labelmap, tmp_path / "in")
    man = run(tmp_path / "in" / "labels.nii.gz", tmp_path / "out", params=PipelineParams(dicom_seg=False),
              log=lambda *a: None)
    by = {s["name"]: s for s in man["structures"]}
    assert by["fat"]["qc"]["cut_by_scan_edge"] and by["muscle"]["qc"]["cut_by_scan_edge"]
    assert not by["radius_cortical"]["qc"]["cut_by_scan_edge"]
    assert all(s["qc"]["watertight"] and s["qc"]["self_intersections"] == 0 for s in man["structures"])
    assert all(s["qc"]["passed"] for s in man["structures"]), [s["qc"]["issues"] for s in man["structures"]]
