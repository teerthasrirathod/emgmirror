"""Hazard tests (docs/ct_platform/REGULATORY_PATH.md section 4; requirement ids in
docs/ct_platform/device/S2_software_requirements.md): left/right flip, wrong scale / unit errors, anisotropic
spacing, oblique and improper direction cosines, structures cut by the scan edge, determinism."""

import json

import numpy as np
import pytest
import trimesh

from emgmirror.ctmesh import LPS_TO_GLTF, fixtures, gltf
from emgmirror.ctmesh.contract import Label, LabelMap, save_labelmap
from emgmirror.ctmesh.pipeline import PipelineParams, run

QUIET = dict(log=lambda *a: None)


def _bilateral(direction=np.eye(3), spacing=(1.0, 1.0, 1.0), flip_storage=False):
    """A 'left' ball at LPS x = +30 mm and a smaller 'right' ball at x = -30 mm, both defined in patient space and
    voxelised on the given grid. ``flip_storage`` stores the same patient with the x index axis reversed
    (direction column negated), as a RAS/LPS-flipped NIfTI would: the anatomy must not move."""
    D = np.array(direction, float)
    if flip_storage:
        D = D @ np.diag([-1.0, 1.0, 1.0])
    n = tuple(int(np.ceil(100.0 / sp)) for sp in spacing[::-1])  # z, y, x: 100 mm on every axis
    origin = -0.5 * (D @ ((np.array(n[::-1]) - 1) * spacing))

    def inside(P):
        lab = np.zeros(len(P), np.uint16)
        lab[((P - [30.0, 0, 0]) ** 2).sum(1) <= 12.0 ** 2] = 1
        lab[((P - [-30.0, 0, 0]) ** 2).sum(1) <= 9.0 ** 2] = 2
        return lab

    arr = fixtures._voxelise(n, spacing, origin, D, inside, f=2)
    labels = [Label(1, "femur_left", rgb=(200, 0, 0)), Label(2, "femur_right", rgb=(0, 0, 200))]
    return LabelMap(arr, tuple(spacing), tuple(origin), D, labels)


def _run(tmp_path, lm, name="r", **kw):
    save_labelmap(lm, tmp_path / f"in_{name}")
    man = run(tmp_path / f"in_{name}" / "labels.nii.gz", tmp_path / f"out_{name}",
              params=PipelineParams(dicom_seg=kw.pop("dicom_seg", False), workers=kw.pop("workers", 1)), **QUIET)
    return tmp_path / f"out_{name}", man


def _centroids_lps(out, man):
    res = {}
    for s in man["structures"]:
        m = trimesh.load(out / s["files"]["stl"])
        res[s["name"]] = m.center_mass
    return res


@pytest.mark.parametrize("flip", [False, True], ids=["as_stored", "x_axis_flipped_in_storage"])
def test_HAZ01_left_right_flip(tmp_path, flip):
    """Left stays on the patient's left (LPS +x, glTF +X) whatever the storage order of the voxels."""
    out, man = _run(tmp_path, _bilateral(flip_storage=flip))
    c = _centroids_lps(out, man)
    assert c["femur_left"][0] == pytest.approx(30.0, abs=0.3)
    assert c["femur_right"][0] == pytest.approx(-30.0, abs=0.3)
    assert all(s["qc"]["laterality"] == "consistent" for s in man["structures"])
    assert man["self_checks"]["laterality_inconsistent"] == []
    _, nodes = gltf.read_glb(out / "combined_web.glb")
    gx = {n["name"]: n["positions_gltf"].mean(0)[0] for n in nodes}
    assert gx["femur_left"] > 0.025 and gx["femur_right"] < -0.025  # metres, glTF +X = patient left


def test_HAZ01_swapped_names_are_flagged(tmp_path):
    lm = _bilateral()
    lm.labels = [Label(1, "femur_right"), Label(2, "femur_left")]  # labels lie about the side
    _, man = _run(tmp_path, lm)
    assert sorted(man["self_checks"]["laterality_inconsistent"]) == ["femur_left", "femur_right"]
    assert all(s["qc"]["laterality"] == "inconsistent" for s in man["structures"])


def test_HAZ02_scale_and_units(tmp_path):
    """Sizes are exact in every format: STL/3MF in mm, GLB in m; a 24 mm ball is 0.024 m wide in the GLB."""
    out, man = _run(tmp_path, _bilateral())
    s = next(x for x in man["structures"] if x["name"] == "femur_left")
    stl = trimesh.load(out / s["files"]["stl"])
    assert np.allclose(stl.extents, 24.0, atol=1.2)
    assert s["volume_ml"] == pytest.approx(4 / 3 * np.pi * 12.0 ** 3 / 1000, rel=0.03)
    for name in ("combined_print.glb", "combined_web.glb", s["files"]["glb"]):
        doc, nodes = gltf.read_glb(out / name)
        n = next(x for x in nodes if x["name"] == "femur_left")
        ext = n["positions_gltf"].max(0) - n["positions_gltf"].min(0)
        assert np.allclose(ext, 0.024, atol=0.0012), (name, ext)  # metres, not millimetres
        assert doc["asset"]["extras"]["units"] == "m"
    assert man["units"] == "m" and np.allclose(man["lps_to_gltf"], LPS_TO_GLTF)
    assert all(v["ok"] for v in man["self_checks"]["glb_units"].values())
    tm = trimesh.load(out / s["files"]["3mf"], force="mesh")
    assert np.allclose(tm.extents, 24.0, atol=1.2)


@pytest.mark.parametrize("spacing", [(0.6, 0.6, 3.0), (2.0, 0.7, 1.2)], ids=["thick_slices", "mixed"])
def test_HAZ03_anisotropic_spacing(tmp_path, spacing):
    out, man = _run(tmp_path, _bilateral(spacing=spacing, direction=fixtures.rotation((1, 0, 1), 15)))
    c = _centroids_lps(out, man)
    assert c["femur_left"] == pytest.approx([30.0, 0, 0], abs=0.6)
    assert c["femur_right"] == pytest.approx([-30.0, 0, 0], abs=0.6)
    assert all(s["qc"]["passed"] for s in man["structures"]), [s["qc"]["issues"] for s in man["structures"]]


@pytest.mark.parametrize("axis,deg", [((0, 0, 1), 90), ((1, 0, 0), 20), ((0.4, 1, 0.3), 55), ((1, 1, 1), 180)])
def test_HAZ04_oblique_direction_cosines(tmp_path, axis, deg):
    out, man = _run(tmp_path, _bilateral(direction=fixtures.rotation(axis, deg)))
    c = _centroids_lps(out, man)
    assert c["femur_left"] == pytest.approx([30.0, 0, 0], abs=0.4)
    assert c["femur_right"] == pytest.approx([-30.0, 0, 0], abs=0.4)


def test_HAZ05_scan_edge_cut_is_flagged(tmp_path):
    lm = _bilateral()
    # crop the grid at LPS x = +25 mm: the left ball (x = +30 mm, r = 12) is cut by the new scan edge
    lm.array = np.ascontiguousarray(lm.array[:, :, :75])
    _, man = _run(tmp_path, lm)
    by = {s["name"]: s for s in man["structures"]}
    assert by["femur_left"]["qc"]["cut_by_scan_edge"] is True
    assert by["femur_right"]["qc"]["cut_by_scan_edge"] is False
    assert by["femur_left"]["qc"]["watertight"]  # capped, still printable, and flagged


def test_HAZ06_deterministic_output(tmp_path):
    """Same input + same version: byte-identical manifest and every output file, also with parallel workers."""
    lm = fixtures.oblique_phantom(shape=(30, 40, 40)).labelmap
    out1, m1 = _run(tmp_path, lm, "a", dicom_seg=True, workers=1)
    out2, m2 = _run(tmp_path, lm, "b", dicom_seg=True, workers=3)
    b1, b2 = (out1 / "manifest.json").read_bytes(), (out2 / "manifest.json").read_bytes()
    assert b1 == b2
    prov = json.loads(b1)["provenance"]
    assert {"software", "input_sha256", "parameters_sha256", "mesh_sha256", "file_sha256"} <= set(prov)
    assert prov["software"]["vtk"] and prov["software"]["emgmirror.ctmesh"]
    for f, h in prov["file_sha256"].items():
        assert (out2 / f).read_bytes() == (out1 / f).read_bytes(), f
    assert "timings_s" in json.loads((out1 / "run_log.json").read_text())
