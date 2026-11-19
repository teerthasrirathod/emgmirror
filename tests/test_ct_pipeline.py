"""End-to-end run of the CT pipeline on a tiny synthetic study (no real data)."""

from __future__ import annotations

import json

import numpy as np
import pytest
import SimpleITK as sitk
import trimesh

from emgmirror.ct import DISCLAIMER, labels as L, pipeline
from emgmirror.ct.dicom import read_nifti
from emgmirror.ct.phantom import make_phantom
from test_ct_dicom import SP, axial_ipps, write_volume


@pytest.fixture(scope="module")
def phantom():
    return make_phantom()


@pytest.fixture(scope="module")
def built(tmp_path_factory, phantom):
    """A study with a sharp series, a soft series on the same grid and a coarse series; built once."""
    root, out = tmp_path_factory.mktemp("study"), tmp_path_factory.mktemp("out")
    hu = sitk.GetArrayFromImage(phantom.image)
    nz, iop, ps = hu.shape[0], (1, 0, 0, 0, 1, 0), (SP[1], SP[0])
    write_volume(root / "bone", hu, number=3, desc="Bone 1.5", kernel="FC30", iop=iop, ipps=axial_ipps(nz),
                 pixel_spacing=ps)
    write_volume(root / "soft", hu, number=4, desc="Sft Tissue 1.5", kernel="FC03", iop=iop, ipps=axial_ipps(nz),
                 pixel_spacing=ps)
    write_volume(root / "coarse", hu[::4], number=5, desc="Bone 6.0", kernel="FC30", iop=iop,
                 ipps=axial_ipps(nz // 4, step=4), pixel_spacing=ps)
    (root / "notes.txt").write_text("not a dicom file")
    for stale in ("meshes/radius_old.stl", "meshes/oldmetal.ply", "meshes/oldmetal.obj",  # an earlier build's
                  "validation/labels_coarse_s99_k0.nii.gz"):
        (out / stale).parent.mkdir(parents=True, exist_ok=True)
        (out / stale).write_bytes(b"old")
    (out / "results.json").write_text(json.dumps({"files": ["meshes/radius_old.stl", "meshes/oldmetal.ply",
                                                            "meshes/oldmetal.obj", "../outside.stl"]}), encoding="utf-8")
    (out.parent / "outside.stl").write_bytes(b"not in meshes/")
    (out / "meshes" / "my_notes.md").write_text("not ours", encoding="utf-8")
    (out / "meshes" / "my_edited_radius.stl").write_bytes(b"a person's own mesh")
    status = out / "progress.json"
    code = pipeline.main(["--input", str(root), "--out", str(out), "--status", str(status)])
    assert code == 0
    return out, json.loads((out / "results.json").read_text(encoding="utf-8")), status


def test_deliverables_exist(built):
    out, res, status = built
    for name in ("volume.nii.gz", "labels.nii.gz", "labels.json", "viewer.html", "report.md", "summary.json",
                 "results.json", "inventory.md", "meshes/combined.glb", "meshes/README.txt"):
        assert (out / name).is_file() and (out / name).stat().st_size > 0, name
    assert res["structures"], "no structure was meshed"
    for name in res["structures"]:
        for ext in ("stl", "obj", "ply"):
            assert (out / "meshes" / f"{name}.{ext}").is_file(), f"{name}.{ext}"
    assert json.loads(status.read_text(encoding="utf-8"))["progress"] == 1.0


def test_chose_the_sharp_series_and_used_positions(built):
    _, res, _ = built
    assert res["primary"]["number"] == 3 and res["soft"]["number"] == 4
    assert res["geometry"]["spacing_mm"] == pytest.approx(list(SP))  # 1.5 mm from positions, not the 5.0 mm tag
    assert res["primary"]["slice_thickness_tag_mm"] == pytest.approx(5.0)


def test_labels_share_the_volume_grid(built, phantom):
    out, _, _ = built
    vol, lab = read_nifti(out / "volume.nii.gz"), read_nifti(out / "labels.nii.gz")
    assert vol.GetSize() == lab.GetSize() == phantom.image.GetSize()
    for a, b in ((vol.GetSpacing(), lab.GetSpacing()), (vol.GetOrigin(), lab.GetOrigin()),
                 (vol.GetDirection(), lab.GetDirection()), (vol.GetOrigin(), phantom.image.GetOrigin())):
        assert a == pytest.approx(b, abs=1e-4)
    assert np.array_equal(sitk.GetArrayFromImage(vol), sitk.GetArrayFromImage(phantom.image))
    ids = set(np.unique(sitk.GetArrayFromImage(lab)).tolist())
    assert {L.FAT, L.MUSCLE, L.METAL, L.cortical(0), L.cortical(1)} <= ids


def test_bone_meshes_are_closed_and_the_right_size(built, phantom):
    out, res, _ = built
    bones = [n for n, s in res["structures"].items() if s["kind"] == "bone" and not s.get("part")]
    assert len(bones) == 2
    truth = sorted(b["volume_mm3"] for b in phantom.analytic["bones"])
    got = []
    for n in bones:
        s = res["structures"][n]
        assert s["qc"]["watertight"] and s["qc"]["self_intersections"] == 0, n
        m = trimesh.load(out / "meshes" / f"{n}.stl")
        assert m.is_watertight
        lo, hi = m.bounds  # patient millimetres: inside the phantom's physical box, not at the array origin
        org, ext = np.array(phantom.image.GetOrigin()), np.array(phantom.image.GetSize()) * np.array(SP)
        assert (lo >= org - 1).all() and (hi <= org + ext + 1).all()
        got.append(float(m.volume))
    # 1.5 mm slices on a 48 mm bone: one slice of partial volume at each end is +-6 %; allow 12 %
    for g, t in zip(sorted(got), truth):
        assert abs(g - t) / t < 0.12, (g, t)


def test_validation_numbers_are_present(built):
    _, res, _ = built
    val = res["validation"]
    assert val["accuracy_floor"]["half_diagonal_mm"] > 0
    assert val["hu_sanity"]["air"]["ok"]
    over = {k: v for k, v in val.get("overlap", {}).items() if k.startswith("all bone")}
    assert over, res.get("notes")
    for r in over.values():
        assert r["dice"] > 0.6 and r["hd95_mm"] is not None  # 6 mm slices against 1.5 mm: loose on purpose
    sweep = val["threshold_sweep"]["structures"]["bone above threshold"]
    assert sweep["pct_per_100hu"] <= 0  # a higher threshold never adds bone
    assert set(val["mesh_vs_mask"]) == set(res["structures"])


def test_outputs_are_offline_and_labelled(built):
    out, _, _ = built
    viewer = (out / "viewer.html").read_text(encoding="utf-8")
    assert "http://" not in viewer and "https://" not in viewer
    assert DISCLAIMER in viewer
    report = (out / "report.md").read_text(encoding="utf-8")
    assert report.count(DISCLAIMER) >= 2 and "None" not in report and "nan" not in report.lower().replace("nano", "")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["disclaimer"] == DISCLAIMER and summary["numbers"] and summary["plain"]
    assert DISCLAIMER in (out / "meshes" / "README.txt").read_text(encoding="utf-8")


def test_unreadable_folder_fails_with_a_plain_message(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "holiday.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 500)
    status = tmp_path / "progress.json"
    assert pipeline.main(["--input", str(tmp_path / "in"), "--out", str(tmp_path / "out"), "--status", str(status)]) == 1
    assert "DICOM" in json.loads(status.read_text(encoding="utf-8"))["error"]


def test_a_build_that_cannot_read_its_input_keeps_the_last_good_build(tmp_path):
    out = tmp_path / "out"
    (out / "meshes").mkdir(parents=True)
    (out / "meshes" / "radius.stl").write_bytes(b"last good build")
    (out / "results.json").write_text(json.dumps({"files": ["meshes/radius.stl"]}), encoding="utf-8")
    (tmp_path / "in").mkdir()
    assert pipeline.main(["--input", str(tmp_path / "in"), "--out", str(out), "--status", str(tmp_path / "p.json")]) == 1
    assert (out / "meshes" / "radius.stl").read_bytes() == b"last good build"
    assert (out / "results.json").is_file()


def test_coarse_bones_are_matched_by_overlap_not_by_name():
    """A coarse run that splits one bone in two (and names one piece generically) is compared piece-wise against
    the whole bone; a coarse 'bone' lying in soft tissue matches nothing."""
    lab = np.zeros((40, 30, 30), np.uint8)
    lab[2:38, 10:20, 10:20] = L.cortical(0)
    lab[4:36, 13:17, 13:17] = L.medullary(0)
    primary = sitk.GetImageFromArray(lab)
    primary.SetSpacing((0.5, 0.5, 0.5))
    lc = np.zeros((20, 30, 30), np.uint8)  # coarse grid: 1 mm along z, same origin
    lc[1:9, 10:20, 10:20] = L.cortical(0)
    lc[11:19, 10:20, 10:20] = L.cortical(1)
    lc[5:15, 25:28, 25:28] = L.cortical(2)  # false bone in soft tissue
    coarse = sitk.GetImageFromArray(lc)
    coarse.SetSpacing((0.5, 0.5, 1.0))
    names = {L.cortical(0): "ulna_cortical", L.medullary(0): "ulna_medullary",
             L.cortical(1): "bone_2_cortical", L.medullary(1): "bone_2_medullary",
             L.cortical(2): "bone_3_cortical", L.medullary(2): "bone_3_medullary"}
    m = pipeline._match_bones(primary, coarse, lc, names)
    assert m == {L.cortical(0): [L.cortical(0), L.cortical(1)]}


def test_meshes_from_an_earlier_build_are_removed_and_not_listed(built):
    out, res, _ = built
    for stale in ("meshes/radius_old.stl", "meshes/oldmetal.ply", "meshes/oldmetal.obj",
                  "validation/labels_coarse_s99_k0.nii.gz"):
        assert not (out / stale).exists(), stale
        assert stale not in res["files"]
    assert (out / "meshes" / "my_notes.md").is_file()  # files the pipeline does not write are left alone
    assert (out / "meshes" / "my_edited_radius.stl").is_file()  # even a mesh, when no build listed it
    assert (out.parent / "outside.stl").is_file()  # never outside meshes/, whatever results.json says
    assert "meshes/my_notes.md" not in res["files"] and "meshes/my_edited_radius.stl" not in res["files"]
    expected = {f"meshes/{n}.{e}" for n in res["structures"] for e in ("stl", "obj", "ply")}
    assert set(res["files"]) == expected | {"meshes/combined.glb", "meshes/README.txt"}


def test_scan_with_no_body_fails_with_a_plain_message_in_a_non_ascii_folder(tmp_path):
    """All air: a clear message instead of trimesh's "Can't export empty scenes!". The output folder has non-ASCII
    characters, which the NIfTI writer cannot open directly on Windows."""
    hu = np.full((12, 32, 32), -1000, np.int16)
    write_volume(tmp_path / "in", hu, number=1, desc="Air", kernel="FC30", iop=(1, 0, 0, 0, 1, 0),
                 ipps=axial_ipps(12), pixel_spacing=(SP[1], SP[0]))
    out = tmp_path / "Ergebnis ü 日本"
    status = tmp_path / "progress.json"
    assert pipeline.main(["--input", str(tmp_path / "in"), "--out", str(out), "--status", str(status),
                          "--no-coarse", "--no-sweep"]) == 1
    assert (out / "volume.nii.gz").is_file() and read_nifti(out / "volume.nii.gz").GetSize() == (32, 32, 12)
    assert "No body was found" in json.loads(status.read_text(encoding="utf-8"))["error"]


def test_a_dicomdir_file_reads_its_folder(tmp_path, monkeypatch):
    (tmp_path / "study").mkdir()
    dicomdir = tmp_path / "study" / "DICOMDIR"
    dicomdir.write_bytes(bytes(200))
    seen = []

    def scan(path):
        seen.append(path)
        return []
    monkeypatch.setattr(pipeline.dicom, "scan_series", scan)
    with pytest.raises(ValueError, match="No DICOM"):
        pipeline.build(dicomdir, tmp_path / "out")
    assert seen == [tmp_path / "study"]
