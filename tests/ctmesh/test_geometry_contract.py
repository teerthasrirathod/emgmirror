import json

import numpy as np
import pytest

from emgmirror.ctmesh import LPS_TO_GLTF, fixtures
from emgmirror.ctmesh.contract import Label, LabelMap, load_labelmap, save_labelmap, snomed_triplet
from emgmirror.ctmesh.geometry import GLTF_TO_LPS, gltf_to_lps, index_to_lps_matrix, lps_to_gltf


def test_lps_to_gltf_is_a_scaled_proper_rotation():
    R = LPS_TO_GLTF[:3, :3] / 0.001
    assert np.allclose(R @ R.T, np.eye(3))
    assert np.isclose(np.linalg.det(R), 1.0)
    assert np.allclose(LPS_TO_GLTF[:3, 3], 0)


def test_anatomical_directions_map_to_gltf_axes():
    # 100 mm to the patient's left, superior, anterior (= -y in LPS)
    assert np.allclose(lps_to_gltf([[100, 0, 0]]), [[0.1, 0, 0]])  # +X
    assert np.allclose(lps_to_gltf([[0, 0, 100]]), [[0, 0.1, 0]])  # +Y up
    assert np.allclose(lps_to_gltf([[0, -100, 0]]), [[0, 0, 0.1]])  # +Z toward the viewer
    p = np.array([[12.5, -40.0, 1234.0]])
    assert np.allclose(gltf_to_lps(lps_to_gltf(p)), p)
    assert np.allclose(GLTF_TO_LPS @ LPS_TO_GLTF, np.eye(4))


def test_index_to_lps_matches_simpleitk():
    import SimpleITK as sitk
    D = fixtures.rotation((1, 2, 3), 33.0)
    im = sitk.Image(5, 6, 7, sitk.sitkUInt16)
    im.SetSpacing((0.7, 0.9, 2.5))
    im.SetOrigin((-10.0, 20.0, 300.0))
    im.SetDirection(tuple(D.ravel()))
    M = index_to_lps_matrix(im.GetOrigin(), im.GetSpacing(), D)
    for idx in [(0, 0, 0), (4, 5, 6), (1.5, 2.25, 3.0)]:
        assert np.allclose(M[:3, :3] @ idx + M[:3, 3], im.TransformContinuousIndexToPhysicalPoint(idx))


def test_contract_round_trip_keeps_oblique_geometry(tmp_path, oblique):
    lm = oblique.labelmap
    lm.labels[0].snomed = {"category": {"scheme": "SCT", "value": "123037004", "meaning": "Anatomical Structure"},
                           "type": {"scheme": "SCT", "value": "10200004", "meaning": "Liver"}, "modifier": None}
    save_labelmap(lm, tmp_path)
    back = load_labelmap(tmp_path / "labels.nii.gz")
    assert np.array_equal(back.array, lm.array)
    assert np.allclose(back.index_to_lps, lm.index_to_lps, atol=1e-4)
    assert [lab.name for lab in back.labels] == [lab.name for lab in lm.labels]
    assert back.labels[0].snomed["type"]["value"] == "10200004"


def test_unlisted_ids_get_an_entry_and_bad_input_is_refused(tmp_path):
    arr = np.zeros((4, 4, 4), np.uint16)
    arr[1, 1, 1], arr[2, 2, 2] = 3, 9
    lm = LabelMap(arr, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), np.eye(3), [Label(3, "liver")])
    save_labelmap(lm, tmp_path)
    back = load_labelmap(tmp_path / "labels.nii.gz")
    assert {lab.id for lab in back.labels} == {3, 9}
    assert "unlisted" in back.label(9).flags
    js = tmp_path / "labels.json"
    doc = json.loads(js.read_text())
    doc["labels"].append(dict(doc["labels"][0]))
    js.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="duplicate"):
        load_labelmap(tmp_path / "labels.nii.gz")
    js.write_text(json.dumps({"version": 2, "labels": []}))
    with pytest.raises(ValueError, match="version"):
        load_labelmap(tmp_path / "labels.nii.gz")


def test_snomed_triplet_accepts_dcmqi_keys():
    t = snomed_triplet({"Type": {"CodeValue": "64033007", "CodingSchemeDesignator": "SCT", "CodeMeaning": "Kidney"},
                        "TypeModifier": {"CodeValue": "24028007", "CodingSchemeDesignator": "SCT",
                                         "CodeMeaning": "Right"}})
    assert t["type"]["value"] == "64033007" and t["modifier"]["meaning"] == "Right"
    assert t["category"]["value"] == "123037004"
    assert snomed_triplet(None) is None and snomed_triplet({"type": None}) is None


def test_totalseg_table_has_names_codes_and_colours():
    t = fixtures.totalseg_table()
    assert t["liver"]["snomed"]["type"]["value"] == "10200004"
    assert t["kidney_left"]["snomed"]["modifier"]["meaning"] == "Left"
    assert len(t["spleen"]["rgb"]) == 3
    assert t["spleen"]["order"] < t["liver"]["order"]


def test_totalseg_to_contract(tmp_path):
    import SimpleITK as sitk
    seg = tmp_path / "s9999" / "segmentations"
    seg.mkdir(parents=True)
    for name, sl in (("liver", np.s_[2:5, 2:5, 2:5]), ("spleen", np.s_[6:8, 6:8, 6:8]), ("empty_thing", None)):
        a = np.zeros((10, 10, 10), np.uint8)
        if sl is not None:
            a[sl] = 1
        im = sitk.GetImageFromArray(a)
        im.SetSpacing((1.5, 1.5, 1.5))
        sitk.WriteImage(im, str(seg / f"{name}.nii.gz"))
    lm = fixtures.totalseg_to_contract(tmp_path / "s9999", tmp_path / "out")
    names = {lab.name: lab for lab in lm.labels}
    assert set(names) == {"liver", "spleen"}  # empty masks are dropped
    assert names["spleen"].id < names["liver"].id  # TotalSegmentator class order
    back = load_labelmap(tmp_path / "out" / "labels.nii.gz")
    assert np.count_nonzero(back.array == names["liver"].id) == 27
    assert back.label(names["liver"].id).snomed["type"]["meaning"] == "Liver"
