import SimpleITK as sitk

from emgmirror.ctseg.benchmark import _same_physical_grid


def test_equal_dimensions_with_different_physical_geometry_are_not_same_grid():
    a = sitk.Image([8, 9, 10], sitk.sitkUInt8)
    b = sitk.Image([8, 9, 10], sitk.sitkUInt8)
    assert _same_physical_grid(a, b)

    b.SetOrigin((10.0, 0.0, 0.0))
    assert not _same_physical_grid(a, b)

    b.SetOrigin(a.GetOrigin())
    b.SetSpacing((1.0, 1.0, 2.0))
    assert not _same_physical_grid(a, b)

    b.SetSpacing(a.GetSpacing())
    b.SetDirection((0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    assert not _same_physical_grid(a, b)
