"""Load the input scan safely and describe it (modality, slice thickness, metal, size).

* Size caps are checked from the header **before** the voxels are allocated. Above ``max_voxels`` the image
  is read in slabs and resampled onto a coarser grid that fits (peak memory: one slab plus the output); above
  ``refuse_voxels`` it is refused.
* DICOM folders go through :mod:`emgmirror.ct.dicom` (geometry from IOP/IPP only, tilt detection, series
  choice), so the same rules apply as in the research pipeline.
* Modality routing: the DICOM ``Modality`` tag when there is one, otherwise intensity statistics (CT has air
  near -1000 HU; MR intensities are non-negative and arbitrary). ``--modality`` overrides both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import SimpleITK as sitk


class InputRefused(ValueError):
    """The scan cannot be segmented; the message is meant for the user."""


@dataclass
class ScanInfo:
    size: tuple[int, int, int]  # x, y, z
    spacing: tuple[float, float, float]
    origin: tuple[float, float, float]
    direction: tuple[float, ...]
    modality: str = "CT"
    modality_source: str = "assumed"
    slice_mm: float = 0.0
    resampled: bool = False
    original_size: tuple[int, int, int] | None = None
    original_spacing: tuple[float, float, float] | None = None
    metal_voxels: int = 0
    metal_ml: float = 0.0
    hu_p01: float | None = None
    hu_p99: float | None = None
    warnings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    source: str = ""  # "nifti" | "dicom"

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["voxels"] = int(np.prod(self.size))
        return d


def _target_spacing(size, spacing, max_voxels: int) -> tuple[float, float, float]:
    """Smallest isotropic-ish coarsening that brings the voxel count under the cap.

    Fine axes are coarsened first (a 0.3 mm in-plane grid loses less than a 1 mm slice axis)."""
    sp = np.array(spacing, float)
    sz = np.array(size, float)
    extent = sp * sz
    lo, hi = float(sp.min()), float(extent.max())
    for _ in range(60):  # bisection on a floor spacing s: new spacing = max(old, s)
        mid = 0.5 * (lo + hi)
        n = np.prod(np.ceil(extent / np.maximum(sp, mid)))
        lo, hi = (mid, hi) if n > max_voxels else (lo, mid)
    return tuple(float(x) for x in np.maximum(sp, hi))


def _out_grid(size, spacing, origin, direction, new_sp) -> sitk.Image:
    extent = np.array(size) * np.array(spacing)
    new_size = [max(1, int(math.ceil(e / s - 1e-6))) for e, s in zip(extent, new_sp)]
    D = np.array(direction).reshape(3, 3)
    # keep the physical box: first voxel centre moves by half the spacing change
    shift = D @ ((np.array(new_sp) - np.array(spacing)) / 2.0)
    ref = sitk.Image(new_size, sitk.sitkInt16)
    ref.SetSpacing(new_sp)
    ref.SetDirection(direction)
    ref.SetOrigin(tuple(float(v) for v in np.array(origin) + shift))
    return ref


def _resample_slabwise(read_slab, size, spacing, origin, direction, new_sp, slab_out: int = 32) -> sitk.Image:
    """Assemble the coarse image chunk by chunk along z; ``read_slab(z0, z1)`` returns input slices [z0, z1)."""
    ref = _out_grid(size, spacing, origin, direction, new_sp)
    out = np.full(sitk.GetArrayViewFromImage(ref).shape, -1024, np.int16)
    nz_out = ref.GetSize()[2]
    for z0 in range(0, nz_out, slab_out):
        z1 = min(nz_out, z0 + slab_out)
        sub = sitk.RegionOfInterest(ref, [ref.GetSize()[0], ref.GetSize()[1], z1 - z0], [0, 0, z0])
        # input slices covering this chunk (+ 1 slice margin for linear interpolation)
        # input z-index of output slice j: ((new - old) / 2 + j * new) / old (the boxes share their first face)
        zz = ((new_sp[2] - spacing[2]) / 2 + np.array([z0, z1 - 1]) * new_sp[2]) / spacing[2]
        i0, i1 = max(0, int(math.floor(zz[0])) - 1), min(size[2], int(math.ceil(zz[1])) + 2)
        slab = read_slab(i0, i1)
        res = sitk.Resample(slab, sub, sitk.Transform(), sitk.sitkLinear, -1024, sitk.sitkInt16)
        out[z0:z1] = sitk.GetArrayViewFromImage(res)
    img = sitk.GetImageFromArray(out)
    img.CopyInformation(ref)
    return img


def _check_size(size, spacing, max_voxels: int, refuse_voxels: int) -> tuple[float, float, float] | None:
    n = int(np.prod(size))
    if n > refuse_voxels:
        raise InputRefused(f"the scan has {n:,} voxels ({' x '.join(map(str, size))}), above the limit of "
                           f"{refuse_voxels:,}; export a smaller field of view or a coarser reconstruction")
    if n > max_voxels:
        return _target_spacing(size, spacing, max_voxels)
    return None


def load_nifti(path: Path | str, max_voxels: int, refuse_voxels: int) -> tuple[sitk.Image, ScanInfo]:
    path = Path(path)
    if not path.is_file():
        raise InputRefused(f"input file not found: {path.name}")
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    try:
        reader.ReadImageInformation()
    except RuntimeError as e:
        if "orthonormal" in str(e):
            return _load_nifti_repaired(path, max_voxels, refuse_voxels)
        raise InputRefused(f"cannot read '{path.name}' as an image ({str(e).splitlines()[0][:120]})") from None
    if reader.GetDimension() != 3 or reader.GetNumberOfComponents() != 1:
        raise InputRefused(f"expected a single-channel 3D image, got {reader.GetDimension()}D with "
                           f"{reader.GetNumberOfComponents()} component(s)")
    size, spacing = tuple(reader.GetSize()), tuple(reader.GetSpacing())
    origin, direction = tuple(reader.GetOrigin()), tuple(reader.GetDirection())
    new_sp = _check_size(size, spacing, max_voxels, refuse_voxels)
    if new_sp is None:
        img = sitk.ReadImage(str(path))
        if img.GetPixelID() not in (sitk.sitkInt16, sitk.sitkFloat32):
            img = sitk.Cast(img, sitk.sitkFloat32)
        info = ScanInfo(size, spacing, origin, direction, source="nifti")
        return img, info

    def slab(z0, z1):
        r = sitk.ImageFileReader()
        r.SetFileName(str(path))
        r.SetExtractIndex([0, 0, z0])
        r.SetExtractSize([size[0], size[1], z1 - z0])
        return sitk.Cast(r.Execute(), sitk.sitkFloat32)

    img = _resample_slabwise(slab, size, spacing, origin, direction, new_sp)
    info = ScanInfo(tuple(img.GetSize()), tuple(img.GetSpacing()), tuple(img.GetOrigin()), direction,
                    resampled=True, original_size=size, original_spacing=spacing, source="nifti")
    info.flags.append("resampled_input")
    info.warnings.append(f"the scan ({' x '.join(map(str, size))} voxels) was resampled to "
                         f"{' x '.join(f'{s:.2f}' for s in new_sp)} mm before segmentation to fit the memory cap; "
                         "labels are on the resampled grid (image.nii.gz)")
    return img, info


def _load_nifti_repaired(path: Path, max_voxels: int, refuse_voxels: int,
                         max_dev: float = 1e-2) -> tuple[sitk.Image, ScanInfo]:
    """Read a NIfTI whose direction cosines are slightly non-orthonormal (ITK refuses those).

    Rounding in the writer's affine is common (TotalSegmentator dataset files deviate by ~2e-4). The direction
    is replaced by the nearest orthonormal matrix (polar decomposition); deviations above ``max_dev`` are
    refused because the geometry is then genuinely sheared."""
    import nibabel as nib

    im = nib.load(str(path))
    if len(im.shape) != 3:
        raise InputRefused(f"expected a single-channel 3D image, got shape {im.shape}")
    A = im.affine[:3, :3]
    sp = np.linalg.norm(A, axis=0)
    D = A / sp
    dev = float(np.abs(D.T @ D - np.eye(3)).max())
    if dev > max_dev:
        raise InputRefused(f"the image axes are not perpendicular (deviation {dev:.3g}); sheared or tilted volumes "
                           "are not supported")
    size = tuple(int(v) for v in im.shape)
    if _check_size(size, tuple(float(v) for v in sp), max_voxels, refuse_voxels) is not None:
        raise InputRefused("the image is too large for the memory cap and its header needs repair; export a "
                           "coarser reconstruction")
    u, _, vt = np.linalg.svd(D)
    D_ras = u @ vt
    flip = np.diag([-1.0, -1.0, 1.0])  # RAS -> LPS
    arr = np.asanyarray(im.dataobj)
    arr = np.rint(arr).astype(np.int16) if np.issubdtype(arr.dtype, np.floating) else arr.astype(np.int16)
    img = sitk.GetImageFromArray(np.ascontiguousarray(arr.transpose(2, 1, 0)))
    img.SetSpacing(tuple(float(v) for v in sp))
    img.SetOrigin(tuple(float(v) for v in flip @ im.affine[:3, 3]))
    img.SetDirection(tuple(float(v) for v in (flip @ D_ras).ravel()))
    info = ScanInfo(size, tuple(img.GetSpacing()), tuple(img.GetOrigin()), tuple(img.GetDirection()),
                    source="nifti", resampled=True)
    info.flags.append("direction_repaired")
    info.warnings.append(f"the image header had slightly non-perpendicular axes (deviation {dev:.1e}); they were "
                         "made exactly perpendicular, and labels are on the repaired grid (image.nii.gz)")
    return img, info


def load_dicom_folder(folder: Path | str, max_voxels: int, refuse_voxels: int) -> tuple[sitk.Image, ScanInfo]:
    """Pick the primary series with the research pipeline's rules and load it (with the same size caps)."""
    from emgmirror.ct import dicom

    series = dicom.scan_series(Path(folder))
    if not series:
        raise InputRefused("no DICOM images found in the folder")
    try:
        primary, _why = dicom.choose_primary(series)
    except ValueError as e:
        mr = [s for s in series if s.volumetric and s.modality.upper() == "MR" and s.slice_spacing]
        if not mr:
            raise InputRefused(str(e)) from None
        primary = min(mr, key=lambda s: s.pixel_spacing[0] * s.pixel_spacing[1] * s.slice_spacing)
    k = max(range(len(primary.stacks)), key=lambda j: len(primary.stacks[j]))
    idx = primary.stacks[k]
    if k in primary.tilted_stacks:
        raise InputRefused("the series has gantry tilt (sheared slices); ask for a reconstruction without tilt")
    pos = np.array([primary.positions[i] for i in idx])
    sz = float((pos[-1] - pos[0]) / (len(idx) - 1)) if len(idx) > 1 else float(primary.slice_spacing or 1.0)
    size = (primary.cols, primary.rows, len(idx))
    spacing = (float(primary.pixel_spacing[1]), float(primary.pixel_spacing[0]), sz)
    new_sp = _check_size(size, spacing, max_voxels, refuse_voxels)
    modality = primary.modality.upper() or "CT"
    if new_sp is None:
        img = dicom.load_series(primary, k)
        info = ScanInfo(tuple(img.GetSize()), tuple(img.GetSpacing()), tuple(img.GetOrigin()),
                        tuple(img.GetDirection()), modality=modality, modality_source="dicom_tag", source="dicom")
        return img, info
    first = dicom.load_series(_subset(primary, idx[:2] if len(idx) >= 2 else idx), 0)
    origin, direction = tuple(first.GetOrigin()), tuple(first.GetDirection())

    def slab(z0, z1):
        sub = idx[z0:z1]
        if len(sub) < 2:  # load_series needs >= 1 slice; pad to keep spacing defined
            sub = idx[max(0, z0 - 1):z0 + 1]
        return sitk.Cast(dicom.load_series(_subset(primary, sub), 0), sitk.sitkFloat32)

    img = _resample_slabwise(slab, size, spacing, origin, direction, new_sp)
    info = ScanInfo(tuple(img.GetSize()), tuple(img.GetSpacing()), tuple(img.GetOrigin()), direction,
                    modality=modality, modality_source="dicom_tag", resampled=True, original_size=size,
                    original_spacing=spacing, source="dicom")
    info.flags.append("resampled_input")
    info.warnings.append("the series was resampled to fit the memory cap; labels are on the resampled grid")
    return img, info


def _subset(info, sub_idx):
    """Shallow copy of a SeriesInfo whose only stack is ``sub_idx`` (lets load_series read a slab)."""
    import copy
    c = copy.copy(info)
    c.stacks = [list(sub_idx)]
    c.tilted_stacks = []
    c.volumetric = True
    return c


def describe(img: sitk.Image, info: ScanInfo, *, modality: str = "auto", warn_slice_mm: float = 3.0,
             refuse_slice_mm: float = 5.0, metal_hu: float = 3000.0, metal_min_ml: float = 0.1) -> ScanInfo:
    """Fill modality, slice thickness, HU percentiles and metal; raise InputRefused for unusable scans."""
    arr = sitk.GetArrayViewFromImage(img)
    step = max(1, int(round((arr.size / 4e6) ** (1 / 3))))
    sample = np.asarray(arr[::step, ::step, ::step], np.float32)
    p01, p99 = (float(v) for v in np.percentile(sample, [1, 99]))
    info.hu_p01, info.hu_p99 = p01, p99
    if modality != "auto":
        info.modality, info.modality_source = modality.upper(), "user"
    elif info.modality_source != "dicom_tag":
        looks_ct = float(sample.min()) <= -500 or p01 <= -500
        info.modality, info.modality_source = ("CT" if looks_ct else "MR"), "intensity"
        # raw CT stored without its -1024 intercept: an 'air' peak near 0 and a 'soft tissue' peak near 1000
        air = float(np.mean(sample <= 100))
        soft = float(np.mean(np.abs(sample - 1024) <= 150))
        if not looks_ct and float(sample.min()) >= 0 and float(sample.max()) <= 4096 and air > 0.05 and soft > 0.1:
            info.warnings.append("intensities are all >= 0 with a CT-like range: this may be a CT stored without "
                                 "its rescale intercept (raw values, not HU); treated as MR. Use --modality ct "
                                 "only if you have re-applied the intercept.")
            info.flags.append("possibly_unrescaled_ct")
    if info.modality not in ("CT", "MR"):
        raise InputRefused(f"modality '{info.modality}' is not supported (CT or MR only)")
    if info.modality == "CT" and p01 > -200 and float(sample.min()) > -200:
        info.warnings.append("no air below -200 HU in the image: the field of view may be inside the body, or "
                             "values are not in HU")
    info.slice_mm = float(max(img.GetSpacing()))
    if info.slice_mm > refuse_slice_mm:
        raise InputRefused(f"the coarsest voxel spacing is {info.slice_mm:.1f} mm; scans with slices thicker than "
                           f"{refuse_slice_mm:g} mm are refused (thin structures cannot be reconstructed)")
    if info.slice_mm > warn_slice_mm:
        info.flags.append("thick_slices")
        info.warnings.append(f"slices are {info.slice_mm:.1f} mm apart (> {warn_slice_mm:g} mm): thin structures "
                             "(vessels, ribs, nerves) are unreliable and surfaces will staircase")
    if info.modality == "CT":
        n = int(np.count_nonzero(arr >= metal_hu))
        vox_ml = float(np.prod(img.GetSpacing())) / 1000.0
        info.metal_voxels, info.metal_ml = n, n * vox_ml
        if n * vox_ml >= metal_min_ml:
            info.flags.append("metal_present")
            info.warnings.append(f"metal detected ({n * vox_ml:.1f} mL above {metal_hu:g} HU): structures near it "
                                 "may be mis-segmented by streak artefacts")
    else:
        info.flags.append("mr_no_hu_checks")
        info.warnings.append("MR scan: routed to total_mr; HU-based checks (mean HU, metal) are not applied")
    return info


def metal_mask(img: sitk.Image, metal_hu: float) -> np.ndarray:
    return sitk.GetArrayViewFromImage(img) >= metal_hu
