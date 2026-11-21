import numpy as np
import pydicom
import pytest
import SimpleITK as sitk
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from emgmirror.ctseg import inputs as I

from ctseg_helpers import make_phantom


def _desc(img, **kw):
    info = I.ScanInfo(img.GetSize(), img.GetSpacing(), img.GetOrigin(), img.GetDirection())
    return I.describe(img, info, **kw)


def test_ct_is_recognised_and_clean(phantom):
    info = _desc(phantom[0])
    assert info.modality == "CT" and info.modality_source == "intensity"
    assert info.flags == [] and info.slice_mm == 2.0


def test_mr_like_intensities_route_to_mr():
    img = sitk.GetImageFromArray(np.random.default_rng(0).uniform(0, 800, (20, 30, 30)).astype(np.float32))
    info = _desc(img)
    assert info.modality == "MR" and "mr_no_hu_checks" in info.flags
    assert _desc(img, modality="ct").modality == "CT"


def test_unrescaled_ct_is_warned_about():
    hu = make_phantom()[0]
    raw = sitk.Cast(hu, sitk.sitkInt32) + 1024
    info = _desc(raw)
    assert "possibly_unrescaled_ct" in info.flags


def test_slice_thickness_warns_then_refuses(phantom):
    img = phantom[0]
    img.SetSpacing((0.8, 0.8, 4.0))
    assert "thick_slices" in _desc(img).flags
    img.SetSpacing((0.8, 0.8, 6.0))
    with pytest.raises(I.InputRefused, match="thicker than 5"):
        _desc(img)


def test_metal_is_flagged():
    img, _ = make_phantom(metal=True)
    info = _desc(img)
    assert "metal_present" in info.flags and info.metal_ml > 0.1


def test_oversized_nifti_is_resampled_slabwise_before_full_load(tmp_path, phantom):
    img = phantom[0]
    p = tmp_path / "big.nii"  # uncompressed: the reader streams slabs
    sitk.WriteImage(img, str(p))
    out, info = I.load_nifti(p, max_voxels=20_000, refuse_voxels=10**9)
    assert info.resampled and "resampled_input" in info.flags
    assert int(np.prod(out.GetSize())) <= 20_000
    # same physical box (within one output voxel) and the same tissue at the same physical point
    ext_in = np.array(img.GetSize()) * np.array(img.GetSpacing())
    ext_out = np.array(out.GetSize()) * np.array(out.GetSpacing())
    assert np.all(np.abs(ext_out - ext_in) <= np.array(out.GetSpacing()) + 1e-6)
    pt = img.TransformIndexToPhysicalPoint((25, 30, 12))  # inside the liver (60 HU)
    v = out.GetPixel(out.TransformPhysicalPointToIndex(pt))
    assert 30 <= v <= 70


def test_refuse_from_header_alone(tmp_path, phantom):
    p = tmp_path / "ct.nii.gz"
    sitk.WriteImage(phantom[0], str(p))
    with pytest.raises(I.InputRefused, match="above the limit"):
        I.load_nifti(p, max_voxels=100, refuse_voxels=1000)


def test_not_an_image_is_refused(tmp_path):
    p = tmp_path / "x.nii.gz"
    p.write_bytes(b"not an image")
    with pytest.raises(I.InputRefused):
        I.load_nifti(p, 10**8, 10**9)


def _write_dicom(folder, hu, spacing, origin):
    uid = generate_uid()
    folder.mkdir()
    for k, sl in enumerate(hu):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = Dataset()
        ds.file_meta = meta
        ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
        ds.Modality, ds.SeriesInstanceUID, ds.SeriesNumber = "CT", uid, 3
        ds.SeriesDescription, ds.ConvolutionKernel = "phantom", "B30f"
        ds.Rows, ds.Columns = sl.shape
        ds.PixelSpacing, ds.SliceThickness = [spacing[1], spacing[0]], spacing[2]
        ds.ImageOrientationPatient = [1.0, 0, 0, 0, 1.0, 0]
        ds.ImagePositionPatient = [origin[0], origin[1], origin[2] + k * spacing[2]]
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit, ds.PixelRepresentation = 15, 1
        ds.RescaleSlope, ds.RescaleIntercept = 1, 0
        ds.PixelData = sl.astype(np.int16).tobytes()
        ds.save_as(folder / f"im{k:03d}", enforce_file_format=True)


def test_dicom_folder_matches_the_nifti_geometry(tmp_path, phantom):
    img = phantom[0]
    hu = sitk.GetArrayFromImage(img)
    _write_dicom(tmp_path / "dcm", hu, img.GetSpacing(), img.GetOrigin())
    out, info = I.load_dicom_folder(tmp_path / "dcm", 10**8, 10**9)
    assert info.modality == "CT" and info.modality_source == "dicom_tag"
    assert out.GetSize() == img.GetSize()
    assert np.allclose(out.GetSpacing(), img.GetSpacing()) and np.allclose(out.GetOrigin(), img.GetOrigin())
    assert np.array_equal(sitk.GetArrayFromImage(out), hu)
    small, sinfo = I.load_dicom_folder(tmp_path / "dcm", max_voxels=30_000, refuse_voxels=10**9)
    assert sinfo.resampled and int(np.prod(small.GetSize())) <= 30_000
