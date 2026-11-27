import pytest
import SimpleITK as sitk
import numpy as np

from emgmirror.ctapp import intake


def test_intake_memory_estimate_respects_configured_budget(monkeypatch):
    monkeypatch.setattr(intake, "MAX_WORKING_BYTES", 1024)
    monkeypatch.setattr(intake, "MAX_INPUT_VOXELS", 1024)
    estimate = intake._check_volume_size((4, 4, 2), "test volume")
    assert estimate["voxel_count"] == 32
    assert estimate["estimated_working_bytes"] == 32 * intake.ESTIMATED_WORKING_BYTES_PER_VOXEL

    with pytest.raises(ValueError, match="estimated working memory"):
        intake._check_volume_size((8, 8, 8), "test volume")


def test_legacy_voxel_cap_still_applies(monkeypatch):
    monkeypatch.setattr(intake, "MAX_WORKING_BYTES", 1024 * 1024)
    monkeypatch.setattr(intake, "MAX_INPUT_VOXELS", 10)
    with pytest.raises(ValueError, match="has 12 voxels"):
        intake._check_volume_size((3, 2, 2), "test volume")


def test_volume_adapter_records_and_preserves_source_geometry(tmp_path):
    source = tmp_path / "scan.nii.gz"
    output = tmp_path / "ct.nii.gz"
    image = sitk.Image([8, 7, 5], sitk.sitkInt16)
    image.SetSpacing((0.7, 0.8, 1.6))
    image.SetOrigin((12.0, -20.0, 35.0))
    image.SetDirection((0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    sitk.WriteImage(image, str(source))

    info = intake.normalize_upload(source, tmp_path, output, max_expanded_bytes=1024 * 1024)
    canonical = sitk.ReadImage(str(output))
    source_read = sitk.ReadImage(str(source))
    assert canonical.GetSize() == source_read.GetSize()
    assert np.allclose(canonical.GetSpacing(), source_read.GetSpacing(), atol=1e-7)
    assert np.allclose(canonical.GetOrigin(), source_read.GetOrigin(), atol=1e-7)
    assert np.allclose(canonical.GetDirection(), source_read.GetDirection(), atol=1e-7)
    assert info["conversion_history"] == [{"operation": "container decode and NIfTI serialization", "resampled": False}]
    assert info["resource_estimate"]["estimate_scope"].startswith("intake-side buffers")
