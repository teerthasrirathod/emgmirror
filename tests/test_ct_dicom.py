"""DICOM loading tests on synthetic data only."""

from __future__ import annotations

import numpy as np
import nibabel as nib
import pydicom
import pytest
import SimpleITK as sitk
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from emgmirror.ct import dicom as D
from emgmirror.ct.phantom import make_phantom

SP = (0.9, 0.9, 1.5)
ORG = (-30.0, 12.0, 100.0)


def _write_slice(path, arr2d, *, uid, number, desc, kernel, iop, ipp, pixel_spacing, thickness, slope, icpt, signed):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.Modality, ds.SeriesInstanceUID, ds.SeriesNumber = "CT", uid, number
    ds.SeriesDescription, ds.ConvolutionKernel = desc, kernel
    ds.Rows, ds.Columns = arr2d.shape
    ds.PixelSpacing, ds.SliceThickness = [float(v) for v in pixel_spacing], thickness
    ds.ImageOrientationPatient = [float(v) for v in iop]
    if ipp is not None:
        ds.ImagePositionPatient = [float(v) for v in ipp]
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit, ds.PixelRepresentation = 15, int(signed)
    ds.RescaleSlope, ds.RescaleIntercept = slope, icpt
    ds.PixelData = arr2d.astype(np.int16 if signed else np.uint16).tobytes()
    ds.save_as(path, enforce_file_format=True)


def write_volume(root, hu, *, number, desc, kernel, iop, ipps, pixel_spacing, signed=False, vary_intercept=False,
                 skip=(), thickness=5.0, rng=None):
    """hu is [slice, row, col]; ipps one per slice. Files get no extension and shuffled names."""
    uid = generate_uid()
    rng = rng or np.random.default_rng(1)
    root.mkdir(parents=True, exist_ok=True)
    names = rng.permutation(len(hu))
    for k in range(len(hu)):
        if k in skip:
            continue
        icpt = (-1100.0 if k % 2 else -1024.0) if vary_intercept else (0.0 if signed else -1024.0)
        stored = hu[k].astype(np.int32) - int(icpt)
        _write_slice(root / f"f{names[k]:04d}", stored, uid=uid, number=number, desc=desc, kernel=kernel, iop=iop,
                     ipp=ipps[k], pixel_spacing=pixel_spacing, thickness=thickness, slope=1.0, icpt=icpt,
                     signed=signed)


def axial_ipps(nz, sp=SP, org=ORG, step=1):
    return [(org[0], org[1], org[2] + k * step * sp[2]) for k in range(nz)]


@pytest.fixture(scope="module")
def phantom():
    return make_phantom()


@pytest.fixture(scope="module")
def study(tmp_path_factory, phantom):
    root = tmp_path_factory.mktemp("study")
    hu = sitk.GetArrayFromImage(phantom.image)
    nz = hu.shape[0]
    iop = (1, 0, 0, 0, 1, 0)
    ps = (SP[1], SP[0])
    write_volume(root / "a" / "deep", hu, number=3, desc="Bone 1.5", kernel="FC30", iop=iop, ipps=axial_ipps(nz),
                 pixel_spacing=ps)
    write_volume(root / "b", hu, number=4, desc="Sft Tissue 1.5", kernel="FC03", iop=iop, ipps=axial_ipps(nz),
                 pixel_spacing=ps, vary_intercept=True)
    write_volume(root / "c", hu[::4], number=5, desc="Bone 6.0", kernel="FC30", iop=iop,
                 ipps=axial_ipps(nz // 4, step=4), pixel_spacing=ps, signed=True)
    write_volume(root / "scout", hu[:3], number=9, desc="Scout", kernel="FC03", iop=iop, ipps=[None] * 3,
                 pixel_spacing=ps)
    (root / "readme").write_text("not a dicom file")
    (root / "a" / "junk.dcm").write_bytes(b"\x00" * 300)
    (root / "DICOMDIR").write_bytes(b"\x00" * 200)
    return root


@pytest.fixture(scope="module")
def series(study):
    return D.scan_series(study)


def test_scan_classifies(series):
    assert [s.number for s in series] == [3, 4, 5, 9]
    a, b, c, scout = series
    assert (a.n_slices, b.n_slices, c.n_slices, scout.n_slices) == (40, 40, 10, 3)
    assert a.orientation == "axial" and a.volumetric and a.kernel == "FC30" and (a.rows, a.cols) == (80, 80)
    assert a.slice_spacing == pytest.approx(1.5) and a.slice_thickness_tag == 5.0  # positions, not the tag
    assert c.slice_spacing == pytest.approx(6.0)
    assert a.pixel_spacing == (0.9, 0.9) and a.stacks == [list(range(40))]
    assert not scout.volumetric and any("ImagePositionPatient" in p for p in scout.problems)
    assert any("SliceThickness tag" in p for p in a.problems)
    assert a.positions == sorted(a.positions)


def test_choices(series):
    a, b, c, _ = series
    p, reason = D.choose_primary(series)
    assert p is a and "1.5" in reason and "tie" in reason
    assert D.choose_soft(series, p) is b
    assert D.choose_coarse(series, p) == [c]
    with pytest.raises(ValueError, match="no volumetric"):
        D.choose_primary([series[3]])


def test_load_matches_phantom(series, phantom):
    ref = sitk.GetArrayFromImage(phantom.image)
    for s in series[:2]:  # includes slice-varying rescale intercept
        img = D.load_series(s)
        assert img.GetPixelID() == sitk.sitkInt16
        np.testing.assert_array_equal(sitk.GetArrayViewFromImage(img), ref)
        assert img.GetOrigin() == pytest.approx(ORG)
        assert img.GetSpacing() == pytest.approx(SP)
        assert img.GetDirection() == pytest.approx((1, 0, 0, 0, 1, 0, 0, 0, 1))
    c = D.load_series(series[2])  # signed storage
    np.testing.assert_array_equal(sitk.GetArrayViewFromImage(c), ref[::4])
    assert c.GetSpacing()[2] == pytest.approx(6.0)
    with pytest.raises(ValueError):
        D.load_series(series[3])


def test_gap_splits_stacks(tmp_path, phantom):
    hu = sitk.GetArrayFromImage(phantom.image)
    write_volume(tmp_path, hu, number=1, desc="Bone", kernel="FC30", iop=(1, 0, 0, 0, 1, 0),
                 ipps=axial_ipps(40), pixel_spacing=(0.9, 0.9), skip=set(range(15, 20)))
    (s,) = D.scan_series(tmp_path)
    assert s.n_slices == 35 and [len(k) for k in s.stacks] == [15, 20]
    assert s.slice_spacing == pytest.approx(1.5) and s.volumetric
    assert any("2 separate stacks" in p for p in s.problems)
    img = D.load_series(s)  # longest stack
    assert img.GetSize()[2] == 20 and img.GetOrigin()[2] == pytest.approx(100 + 20 * 1.5)
    np.testing.assert_array_equal(sitk.GetArrayViewFromImage(img), hu[20:])
    first = D.load_series(s, stack=0)
    assert first.GetSize()[2] == 15 and first.GetOrigin()[2] == pytest.approx(100.0)
    np.testing.assert_array_equal(sitk.GetArrayViewFromImage(first), hu[:15])


def test_duplicates_dropped(tmp_path, phantom):
    hu = sitk.GetArrayFromImage(phantom.image)[:12]
    ipps = axial_ipps(12)
    ipps[5] = ipps[4]  # slice 5 duplicates the position of slice 4
    write_volume(tmp_path, hu, number=1, desc="x", kernel="K", iop=(1, 0, 0, 0, 1, 0), ipps=ipps,
                 pixel_spacing=(0.9, 0.9))
    (s,) = D.scan_series(tmp_path)
    assert s.n_slices == 11 and any("duplicate" in p for p in s.problems)


def test_coronal_reformat_same_physical_point(tmp_path, phantom):
    hu = sitk.GetArrayFromImage(phantom.image)  # [z, y, x]
    nz, ny, nx = hu.shape
    # coronal slice j: rows run from head to feet (row direction -z), columns along +x
    cor = np.stack([hu[::-1, j, :] for j in range(ny)])
    z_top = ORG[2] + (nz - 1) * SP[2]
    ipps = [(ORG[0], ORG[1] + j * SP[1], z_top) for j in range(ny)]
    write_volume(tmp_path, cor, number=2, desc="Bone cor", kernel="FC30", iop=(1, 0, 0, 0, 0, -1), ipps=ipps,
                 pixel_spacing=(SP[2], SP[0]))
    (s,) = D.scan_series(tmp_path)
    assert s.orientation == "coronal" and s.slice_spacing == pytest.approx(0.9)
    assert s.pixel_spacing == (1.5, 0.9)
    img = D.load_series(s)
    axial = phantom.image
    assert img.GetDirection() == pytest.approx((1, 0, 0, 0, 0, 1, 0, -1, 0))
    assert img.GetSize() == (nx, nz, ny) and img.GetSpacing() == pytest.approx((0.9, 1.5, 0.9))
    rng = np.random.default_rng(3)
    for _ in range(200):
        i, j, k = int(rng.integers(nx)), int(rng.integers(ny)), int(rng.integers(nz))
        pt = axial.TransformIndexToPhysicalPoint((i, j, k))
        idx = img.TransformPhysicalPointToIndex(pt)
        assert img.GetPixel(idx) == axial.GetPixel((i, j, k))


def test_nifti_roundtrip_and_affine(tmp_path, series, phantom):
    # use a coronal-like oblique-free but non-identity direction to make the affine check meaningful
    img = D.load_series(series[0])
    img = sitk.PermuteAxes(img, [0, 2, 1])  # non-identity direction, still int16
    p = tmp_path / "x.nii.gz"
    D.write_nifti(img, p)
    back = D.read_nifti(p)
    np.testing.assert_array_equal(sitk.GetArrayViewFromImage(back), sitk.GetArrayViewFromImage(img))
    assert back.GetOrigin() == pytest.approx(img.GetOrigin(), abs=1e-4)
    assert back.GetSpacing() == pytest.approx(img.GetSpacing(), abs=1e-4)
    assert back.GetDirection() == pytest.approx(img.GetDirection(), abs=1e-4)
    D_ = np.array(img.GetDirection()).reshape(3, 3)
    lps = np.eye(4)
    lps[:3, :3] = D_ * np.array(img.GetSpacing())
    lps[:3, 3] = img.GetOrigin()
    ras = np.diag([-1.0, -1.0, 1.0, 1.0]) @ lps
    nii = nib.load(str(p))
    np.testing.assert_allclose(nii.affine, ras, atol=1e-4)
    assert nii.shape == img.GetSize()
    np.testing.assert_array_equal(np.asarray(nii.dataobj).transpose(2, 1, 0), sitk.GetArrayViewFromImage(img))
    from emgmirror.ct import DISCLAIMER
    assert nii.header["descrip"].tobytes().rstrip(b"\0").decode() == DISCLAIMER  # the label travels with the file


def test_inventory_markdown(series):
    md = D.inventory_markdown(series)
    assert "| 3 | Bone 1.5 | axial | 40 | 80 x 80 |" in md and "Problems:" in md
    assert "UID" not in md


def _small_image():
    img = sitk.GetImageFromArray(np.arange(4 * 5 * 6, dtype=np.int16).reshape(4, 5, 6))
    img.SetSpacing((0.9, 0.8, 1.5))
    img.SetOrigin((-30.0, 12.0, 100.0))
    return img


@pytest.mark.parametrize("short_names", [True, False])
def test_nifti_write_and_read_under_a_non_ascii_folder(tmp_path, monkeypatch, short_names):
    """The ITK NIfTI writer cannot open non-ASCII paths on Windows; a user folder like C:/Users/José must still work.
    Without 8.3 short names the file goes through an ASCII temporary folder."""
    if not short_names:
        monkeypatch.setattr(D, "_short_path", lambda p: None)
    img = _small_image()
    p = tmp_path / "Ergebnis ü 日本" / "volume.nii.gz"
    D.write_nifti(img, p)
    assert p.is_file() and [f.name for f in p.parent.iterdir()] == ["volume.nii.gz"]  # no temporary left behind
    back = D.read_nifti(p)
    np.testing.assert_array_equal(sitk.GetArrayViewFromImage(back), sitk.GetArrayViewFromImage(img))
    assert back.GetOrigin() == pytest.approx(img.GetOrigin(), abs=1e-4)
    assert back.GetSpacing() == pytest.approx(img.GetSpacing(), abs=1e-4)


def test_gantry_tilted_series_is_reported_and_not_loaded(tmp_path, phantom):
    """Slice positions that drift sideways (20 degree gantry tilt) would be sheared by the loader: the series is
    reported as tilted, is not a usable volume, and the build stops with a message that says why."""
    hu = sitk.GetArrayFromImage(phantom.image)
    nz = hu.shape[0]
    dy = SP[2] * np.tan(np.radians(20))
    ipps = [(ORG[0], ORG[1] + k * dy, ORG[2] + k * SP[2]) for k in range(nz)]
    write_volume(tmp_path, hu, number=2, desc="Tilted", kernel="FC30", iop=(1, 0, 0, 0, 1, 0), ipps=ipps,
                 pixel_spacing=(SP[1], SP[0]))
    (s,) = D.scan_series(tmp_path)
    assert not s.volumetric
    assert any(p.startswith("gantry tilt") and "20.0 degrees" in p for p in s.problems), s.problems
    with pytest.raises(ValueError, match="gantry tilt"):
        D.choose_primary([s])
    with pytest.raises(ValueError, match="gantry tilt"):
        D.load_series(s)
    assert "gantry tilt" in D.inventory_markdown([s])


def test_small_position_rounding_is_not_tilt(tmp_path, phantom):
    hu = sitk.GetArrayFromImage(phantom.image)
    rng = np.random.default_rng(5)
    ipps = [(round(x + rng.uniform(-0.004, 0.004), 3), round(y + rng.uniform(-0.004, 0.004), 3), z)
            for x, y, z in axial_ipps(hu.shape[0])]
    write_volume(tmp_path, hu, number=2, desc="Bone", kernel="FC30", iop=(1, 0, 0, 0, 1, 0), ipps=ipps,
                 pixel_spacing=(SP[1], SP[0]))
    (s,) = D.scan_series(tmp_path)
    assert s.volumetric and not any("tilt" in p for p in s.problems)


def test_one_decimal_position_tags_flipping_by_0_1_mm_are_not_tilt(tmp_path, phantom):
    """A scanner that prints positions with one decimal, near a rounding boundary, flips x by 0.1 mm."""
    hu = sitk.GetArrayFromImage(phantom.image)
    ipps = [(-30.0 if k % 3 else -30.1, ORG[1], ORG[2] + k * SP[2]) for k in range(hu.shape[0])]
    write_volume(tmp_path, hu, number=2, desc="Bone", kernel="FC30", iop=(1, 0, 0, 0, 1, 0), ipps=ipps,
                 pixel_spacing=(SP[1], SP[0]))
    (s,) = D.scan_series(tmp_path)
    assert s.volumetric and not any("tilt" in p for p in s.problems), s.problems


def test_stacks_with_their_own_in_plane_origin_are_not_tilt(tmp_path, phantom):
    """Two straight stacks of one series, the second shifted 3 mm in x: each loads with its own origin."""
    hu = sitk.GetArrayFromImage(phantom.image)
    nz = hu.shape[0]
    half = nz // 2
    ipps = [(ORG[0] + (3.0 if k >= half else 0.0), ORG[1], ORG[2] + k * SP[2] + (5.0 if k >= half else 0.0))
            for k in range(nz)]
    write_volume(tmp_path, hu, number=2, desc="Bone", kernel="FC30", iop=(1, 0, 0, 0, 1, 0), ipps=ipps,
                 pixel_spacing=(SP[1], SP[0]))
    (s,) = D.scan_series(tmp_path)
    assert len(s.stacks) == 2 and s.tilted_stacks == []
    assert s.volumetric and not any("tilt" in p for p in s.problems), s.problems
    assert D.load_series(s, 1).GetOrigin()[0] == pytest.approx(ORG[0] + 3.0)


def test_only_the_tilted_stack_is_refused(tmp_path, phantom):
    hu = sitk.GetArrayFromImage(phantom.image)
    nz = hu.shape[0]
    half = nz // 2
    dy = SP[2] * np.tan(np.radians(20))
    ipps = [(ORG[0], ORG[1] + (k - half) * dy if k >= half else ORG[1], ORG[2] + k * SP[2] + (5.0 if k >= half else 0.0))
            for k in range(nz)]
    write_volume(tmp_path, hu, number=2, desc="Bone", kernel="FC30", iop=(1, 0, 0, 0, 1, 0), ipps=ipps,
                 pixel_spacing=(SP[1], SP[0]))
    (s,) = D.scan_series(tmp_path)
    assert s.tilted_stacks == [1]
    assert any(p.startswith("gantry tilt") and "(stack 1)" in p for p in s.problems), s.problems
    D.load_series(s, 0)  # the straight stack still loads
    with pytest.raises(ValueError, match="gantry tilt"):
        D.load_series(s, 1)
