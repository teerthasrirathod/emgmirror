"""Hazard tests (docs/ct_platform/REGULATORY_PATH.md section 4): left/right flip, wrong scale, wrong series,
structure cut by the scan edge, model change detected through the provenance fingerprint."""

import json

import numpy as np
import pytest
import SimpleITK as sitk

from emgmirror.ctseg import device as D
from emgmirror.ctseg import ontology
from emgmirror.ctseg.config import SegConfig
from emgmirror.ctseg.engine import segment
from emgmirror.ctseg.inputs import InputRefused, load_dicom_folder
from emgmirror.ctseg.qc import laterality

from ctseg_helpers import FakeRunner, make_phantom, write_dicom
from test_ctseg_device import DeviceFake, make_weights


@pytest.fixture
def dev_cfg(tmp_path, ts_home):
    w = make_weights(tmp_path / "weights")
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps(D.build_manifest(w, [297], "2.18.0")))
    return lambda **kw: SegConfig(build="device", fast=True, weights_dir=str(w), weights_manifest=str(m),
                                  totalseg_home=str(ts_home), **kw)


def _save(img, path):
    sitk.WriteImage(img, str(path))
    return path


# ---------------------------------------------------------------- left/right flip

def test_mirrored_image_fails_the_laterality_check(tmp_path, dev_cfg):
    img, truth = make_phantom()
    ok = segment(_save(img, tmp_path / "ok.nii.gz"), tmp_path / "ok", dev_cfg(), runner=DeviceFake(truth))
    assert ok.labels_json["qc"]["device"]["laterality_failed"] == []
    # pixel data mirrored left-right while the header stays the same: the model still calls the bone
    # "femur_left" by its appearance, but it now lies on the patient's right
    flipped = sitk.GetImageFromArray(sitk.GetArrayFromImage(img)[:, :, ::-1].copy())
    flipped.CopyInformation(img)
    ftruth = {k: v[:, :, ::-1] for k, v in truth.items()}
    bad = segment(_save(flipped, tmp_path / "flip.nii.gz"), tmp_path / "flip", dev_cfg(), runner=DeviceFake(ftruth))
    assert bad.labels_json["qc"]["device"]["laterality_failed"] == ["femur_left"]
    assert any("LATERALITY CHECK FAILED" in w for w in bad.labels_json["qc"]["warnings"])
    e = next(e for e in bad.labels_json["labels"] if e["name"] == "femur_left")
    assert "laterality_suspect" in e["flags"]


def test_swapped_pair_is_caught_in_either_build():
    a = np.zeros((10, 10, 40), np.uint16)
    a[2:8, 2:8, 30:36] = ontology.label("hip_right").id  # +x (patient left) side holds the "right" hip
    a[2:8, 2:8, 4:10] = ontology.label("hip_left").id
    a[2:8, 4:6, 19:21] = ontology.label("sacrum").id
    lat = laterality(sitk.GetImageFromArray(a))
    assert lat["failed"] == ["hip_left", "hip_right"] and lat["midline_from"].startswith("^(vertebrae")


def test_header_with_negated_x_axis_is_caught():
    """A left/right header error (direction x negated, data unchanged) moves the left femur to the right."""
    img, truth = make_phantom()
    lab = np.zeros(truth["femur_left"].shape, np.uint16)
    lab[truth["femur_left"]] = ontology.label("femur_left").id
    lab[truth["liver"]] = ontology.label("liver").id
    li = sitk.GetImageFromArray(lab)
    li.CopyInformation(img)
    assert laterality(li)["failed"] == []
    li.SetDirection((-1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0))
    assert laterality(li)["failed"] == ["femur_left"]


# ---------------------------------------------------------------- wrong scale / spacing

@pytest.mark.parametrize("spacing", [(15.0, 15.0, 20.0), (0.15, 0.15, 0.2)])
def test_grossly_wrong_spacing_is_refused(tmp_path, spacing):
    img, truth = make_phantom()
    img.SetSpacing(spacing)
    with pytest.raises(InputRefused, match="spacing|scale"):
        segment(_save(img, tmp_path / "s.nii.gz"), tmp_path / "o", SegConfig(), runner=FakeRunner(truth))


def test_moderately_wrong_scale_is_flagged_and_refused_by_the_device_build(tmp_path, ts_home, dev_cfg):
    img, truth = make_phantom()
    img.SetSpacing((0.5, 0.5, 0.66))  # true spacing 1.5 x 1.5 x 2.0: 3x too small
    p = _save(img, tmp_path / "s.nii.gz")
    res = segment(p, tmp_path / "r", SegConfig(totalseg_home=str(ts_home)), runner=FakeRunner(truth))
    assert "implausible_scale" in res.labels_json["qc"]["scan"]["flags"]
    with pytest.raises(InputRefused, match="scale"):
        segment(p, tmp_path / "d", dev_cfg(), runner=DeviceFake(truth))


def test_wrong_scale_shows_as_implausible_volume(tmp_path, ts_home):
    img, truth = make_phantom(shape=(40, 64, 80))
    img.SetSpacing((4.5, 4.5, 4.0))  # 3x / 2x too large: everything ~18x too big
    big = {"liver": truth["liver"]}

    class LiverOnly(FakeRunner):
        def run(self, source, image_path, image, workdir):
            if source == "totalsegmentator:total":
                return self._map(source, big, image)
            return super().run(source, image_path, image, workdir)

    res = segment(_save(img, tmp_path / "s.nii.gz"), tmp_path / "o",
                  SegConfig(totalseg_home=str(ts_home), profile="minimal", warn_slice_mm=5.0), runner=LiverOnly(truth))
    st = res.labels_json["qc"]["structures"]["liver"]
    assert st["volume_ml"] > 500 or "volume_out_of_range" in next(
        e["flags"] for e in res.labels_json["labels"] if e["name"] == "liver")


# ---------------------------------------------------------------- wrong series

def test_finest_series_is_chosen_and_recorded(tmp_path):
    img, _ = make_phantom()
    hu = sitk.GetArrayFromImage(img)
    folder = tmp_path / "study"
    write_dicom(folder, hu, img.GetSpacing(), img.GetOrigin(), number=2, description="thick 6mm", step=3)
    write_dicom(folder, hu, img.GetSpacing(), img.GetOrigin(), number=3, description="thin 2mm")
    write_dicom(folder, hu[:1], img.GetSpacing(), img.GetOrigin(), number=1, description="localizer")
    out, info = load_dicom_folder(folder, 10**8, 10**9)
    s = info.series
    assert s["chosen"]["number"] == 3 and s["chosen"]["n_slices"] == 40
    assert len(s["chosen"]["uid_sha256"]) == 16 and "finest voxel volume" in s["reason"]
    assert sorted(c["number"] for c in s["candidates"]) == [1, 2]
    assert out.GetSize()[2] == 40


def test_series_record_reaches_labels_json(tmp_path, ts_home):
    img, truth = make_phantom()
    folder = tmp_path / "study"
    write_dicom(folder, sitk.GetArrayFromImage(img), img.GetSpacing(), img.GetOrigin(), number=7, description="ax")
    res = segment(folder, tmp_path / "o", SegConfig(totalseg_home=str(ts_home), profile="minimal"),
                  runner=FakeRunner(truth), dicom=True)
    assert res.labels_json["qc"]["series"]["chosen"]["number"] == 7


def test_localizer_only_folder_is_refused(tmp_path):
    img, _ = make_phantom()
    write_dicom(tmp_path / "loc", sitk.GetArrayFromImage(img)[:2], img.GetSpacing(), img.GetOrigin())
    with pytest.raises(InputRefused, match="volumetric"):
        load_dicom_folder(tmp_path / "loc", 10**8, 10**9)


# ---------------------------------------------------------------- cut by the scan edge

def test_cut_structure_is_listed_for_the_reviewer(tmp_path, dev_cfg):
    img, truth = make_phantom()  # the femur rod starts at slice 0
    res = segment(_save(img, tmp_path / "ct.nii.gz"), tmp_path / "o", dev_cfg(), runner=DeviceFake(truth))
    assert res.labels_json["qc"]["device"]["cut_by_scan_edge"] == ["femur_left"]
    assert res.labels_json["qc"]["structures"]["femur_left"]["touches_edge"] == ["z_min"]


# ---------------------------------------------------------------- model change via provenance

def test_model_change_changes_the_provenance_fingerprint(tmp_path, ts_home):
    img, truth = make_phantom()
    p = _save(img, tmp_path / "ct.nii.gz")
    w = make_weights(tmp_path / "w")
    cfg = SegConfig(totalseg_home=str(ts_home), weights_dir=str(w), fast=True, profile="minimal")
    a = segment(p, tmp_path / "a", cfg, runner=DeviceFake(truth)).provenance["model_fingerprint"]
    b = segment(p, tmp_path / "b", cfg, runner=DeviceFake(truth)).provenance["model_fingerprint"]
    assert a["sha256"] == b["sha256"] and a["datasets"]["297"]
    make_weights(tmp_path / "w", content=b"retrained")
    c = segment(p, tmp_path / "c", cfg, runner=DeviceFake(truth)).provenance["model_fingerprint"]
    assert c["sha256"] != a["sha256"]
