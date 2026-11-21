import numpy as np
import SimpleITK as sitk

from emgmirror.ctseg import ontology
from emgmirror.ctseg.inputs import ScanInfo
from emgmirror.ctseg.qc import hu_band, structure_qc


def _img(arr, spacing=(1.0, 1.0, 1.0)):
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing(spacing)
    return img


def _scan(img, **kw):
    s = ScanInfo(img.GetSize(), img.GetSpacing(), img.GetOrigin(), img.GetDirection(), **kw)
    s.slice_mm = max(img.GetSpacing())
    return s


def cid(n):
    return ontology.label(n).id


def run(lab, hu, **kw):
    li, hi = _img(lab.astype(np.uint16)), _img(hu.astype(np.int16))
    scan = _scan(hi, **{k: v for k, v in kw.items() if k in ("modality", "metal_voxels")})
    return {v["name"]: v for v in structure_qc(li, hi, scan, label_sources={}, part_of={}).values()}


def test_edge_volume_and_hu_flags():
    lab = np.zeros((30, 30, 30), np.uint16)
    hu = np.full(lab.shape, -1000, np.int16)
    lab[10:14, 10:14, 10:14] = cid("liver")  # 64 mL? no: 64 voxels of 1 mm3 -> tiny liver
    hu[10:14, 10:14, 10:14] = 1200  # bone density inside a 'liver'
    lab[0:5, 0:5, 5:9] = cid("femur_left")  # touches z_min, y_min
    hu[0:5, 0:5, 5:9] = 700
    q = run(lab, hu)
    assert set(q["liver"]["flags"]) >= {"volume_out_of_range", "hu_out_of_range"}
    assert "cut_by_scan_edge" not in q["liver"]["flags"]
    assert q["femur_left"]["flags"] == ["cut_by_scan_edge"]  # cut: no volume check, HU fine
    assert q["femur_left"]["stats"]["touches_edge"] == ["y_min", "z_min"]


def test_fragmented_and_very_small():
    lab = np.zeros((20, 20, 20), np.uint16)
    hu = np.full(lab.shape, 50, np.int16)
    for k in range(4):
        lab[2 + 4 * k:4 + 4 * k, 5:8, 5:8] = cid("spleen")  # four equal pieces
    lab[15, 15, 15] = cid("pancreas")
    q = run(lab, hu)
    assert "fragmented" in q["spleen"]["flags"] and q["spleen"]["stats"]["components"] == 4
    assert "very_small" in q["pancreas"]["flags"]


def test_near_metal_and_thick_slices():
    lab = np.zeros((20, 20, 20), np.uint16)
    hu = np.full(lab.shape, 50, np.int16)
    lab[5:10, 5:10, 5:10] = cid("urinary_bladder")
    lab[5:10, 5:10, 15:19] = cid("internal_carotid_artery_left")
    hu[7, 7, 12] = 3500
    li, hi = _img(lab), _img(hu, (1.0, 1.0, 4.0))
    li.SetSpacing((1.0, 1.0, 4.0))
    scan = _scan(hi, metal_voxels=1)
    q = {v["name"]: v for v in structure_qc(li, hi, scan, label_sources={}, part_of={},
                                            metal_margin_mm=4.0).values()}
    assert "near_metal" in q["urinary_bladder"]["flags"]
    assert "thick_slices" in q["urinary_bladder"]["flags"]
    assert "thin_structure_thick_slices" in q["internal_carotid_artery_left"]["flags"]
    assert "thin_structure_thick_slices" not in q["urinary_bladder"]["flags"]


def test_mr_skips_hu_checks():
    lab = np.zeros((10, 10, 10), np.uint16)
    lab[3:6, 3:6, 3:6] = cid("liver")
    q = run(lab, np.full(lab.shape, 5000, np.int16), modality="MR")
    assert "hu_out_of_range" not in q["liver"]["flags"] and "mr_no_hu_check" in q["liver"]["flags"]
    assert "mean_hu" not in q["liver"]["stats"]


def test_hu_bands_by_tissue():
    assert hu_band("lung_upper_lobe_left", "other") == (-1000, -350)
    assert hu_band("vertebrae_L1", "bone")[0] == 100
    assert hu_band("body_trunc", "other") is None
    assert hu_band("colon", "other")[1] >= 1000  # oral contrast
