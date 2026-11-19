"""DICOM CT discovery and loading.

Geometry comes only from ImageOrientationPatient / ImagePositionPatient (never SliceThickness).
Loaded images are int16 HU ``SimpleITK.Image`` in DICOM patient LPS mm, numpy view ``[z, y, x]``.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pydicom
import SimpleITK as sitk

_TAGS = ["SeriesInstanceUID", "SeriesNumber", "SeriesDescription", "Modality", "Rows", "Columns", "PixelSpacing",
         "SliceThickness", "ImageOrientationPatient", "ImagePositionPatient", "ConvolutionKernel", "InstanceNumber"]
_DUP_TOL = 1e-3  # mm, positions closer than this are duplicates
_IOP_TOL = 1e-4
_MIN_STACK = 8
_SHARP_KERNELS = {"FC30", "FC31", "FC80", "FC81", "BONE", "BONEPLUS", "D", "YC", "YD"}
_SHARP_RE = re.compile(r"^(B[rl]?[6-9]\d|H[rl]?[6-9]\d)")  # Siemens B60+/Br6x/Hr6x and above


@dataclass
class SeriesInfo:
    number: int
    description: str
    kernel: str
    modality: str
    rows: int
    cols: int
    pixel_spacing: tuple[float, float]  # (row spacing, col spacing) mm
    slice_spacing: float | None  # median distance between slice positions along the normal
    slice_thickness_tag: float | None  # reported only, never used for geometry
    orientation: str  # 'axial' | 'coronal' | 'sagittal' | 'oblique' | 'unknown'
    iop: tuple[float, ...] | None
    n_slices: int
    files: list[Path]  # sorted along the slice normal, duplicates removed
    positions: list[float]  # dot(normal, IPP) per file, same order
    stacks: list[list[int]]  # indices into files; contiguous, uniform spacing
    volumetric: bool
    problems: list[str] = field(default_factory=list)
    ipps: list[tuple[float, float, float]] = field(default_factory=list)  # IPP per file, same order
    tilted_stacks: list[int] = field(default_factory=list)  # stacks whose slices drift sideways (not loadable)


def _floats(v, n: int) -> tuple[float, ...] | None:
    try:
        t = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    return t if len(t) == n else None


def _read_header(p: Path):
    try:
        with open(p, "rb") as f:
            f.seek(128)
            if f.read(4) != b"DICM":
                return None
        ds = pydicom.dcmread(p, stop_before_pixels=True, force=False, specific_tags=_TAGS)
    except Exception:
        return None
    if "SeriesInstanceUID" not in ds or "Rows" not in ds or "Columns" not in ds:  # also drops DICOMDIR
        return None
    return ds


def _orientation(n: np.ndarray) -> str:
    a = np.abs(n)
    k = int(np.argmax(a))
    return "oblique" if a[k] < 0.98 else ("sagittal", "coronal", "axial")[k]


def _split_stacks(pos: np.ndarray) -> tuple[list[list[int]], float | None]:
    if len(pos) < 2:
        return ([list(range(len(pos)))] if len(pos) else []), None
    d = np.diff(pos)
    med = float(np.median(d))
    tol = max(0.01 * abs(med), 0.01)
    stacks, cur = [], [0]
    for i, g in enumerate(d):
        if abs(g - med) > tol:
            stacks.append(cur)
            cur = []
        cur.append(i + 1)
    stacks.append(cur)
    return stacks, med


def _tilt(ipps: np.ndarray, pos: np.ndarray, n: np.ndarray, ps: tuple[float, float]) -> tuple[float, float] | None:
    """(sideways drift mm over the stack, angle degrees) when the slice positions of ONE stack do not run along
    the slice normal (gantry tilt or a sheared stack), else None.

    A straight line is fitted to the sideways part of the positions against the position along the normal, so
    jitter from rounding of the position tags (often 0.1 mm) averages out and only a steady drift counts. The
    drift over the whole stack must exceed max(0.1 mm, a quarter pixel) to be reported."""
    if len(ipps) < 3:
        return None
    along = pos - pos[0]
    side = (ipps - ipps[0]) - np.outer(along, n)  # sideways offset of each slice from the first one
    a = along - along.mean()
    if not np.any(a):
        return None
    slope = (a @ (side - side.mean(axis=0))) / (a @ a)  # mm sideways per mm along the normal, per axis
    s = float(np.linalg.norm(slope))
    drift = s * float(along.max() - along.min())
    if drift <= max(0.1, 0.25 * min(ps)):
        return None
    return drift, float(np.degrees(np.arctan(s)))


def _build(uid_files: list[tuple[Path, object]]) -> SeriesInfo:
    uid_files.sort(key=lambda t: str(t[0]))
    ds0 = uid_files[0][1]
    s = lambda ds, k: str(ds.get(k, "") or "").strip()  # noqa: E731
    kernel = ds0.get("ConvolutionKernel", "")
    kernel = "\\".join(map(str, kernel)) if isinstance(kernel, (list, pydicom.multival.MultiValue)) else str(kernel or "")
    problems: list[str] = []
    try:
        number = int(ds0.get("SeriesNumber", 0) or 0)
    except (TypeError, ValueError):
        number = 0
    ps_list = [_floats(ds.get("PixelSpacing", ()), 2) for _, ds in uid_files]
    iop_list = [_floats(ds.get("ImageOrientationPatient", ()), 6) for _, ds in uid_files]
    ipp_list = [_floats(ds.get("ImagePositionPatient", ()), 3) for _, ds in uid_files]
    shapes = {(int(ds.Rows), int(ds.Columns)) for _, ds in uid_files}
    try:
        thick = float(ds0.get("SliceThickness")) if ds0.get("SliceThickness") not in (None, "") else None
    except (TypeError, ValueError):
        thick = None
    ps0 = ps_list[0] or (0.0, 0.0)
    info = SeriesInfo(number, s(ds0, "SeriesDescription"), kernel.strip(), s(ds0, "Modality"), *next(iter(shapes)), ps0,
                      None, thick, "unknown", iop_list[0], len(uid_files), [p for p, _ in uid_files], [], [], False,
                      problems)
    ok = True
    if len(shapes) > 1:
        problems.append("matrix size varies across slices")
        ok = False
    if any(v is None for v in ps_list):
        problems.append("PixelSpacing missing")
        ok = False
    elif any(np.max(np.abs(np.subtract(v, ps0))) > 1e-4 for v in ps_list):
        problems.append("PixelSpacing is not identical across slices")
        ok = False
    if any(v is None for v in iop_list):
        problems.append("ImageOrientationPatient missing")
        ok = False
    elif any(np.max(np.abs(np.subtract(v, iop_list[0]))) > _IOP_TOL for v in iop_list):
        problems.append("ImageOrientationPatient is not identical across slices")
        ok = False
    nmiss = sum(v is None for v in ipp_list)
    if nmiss:
        problems.append(f"{nmiss} slices lack ImagePositionPatient")
        ok = False
    if not ok or iop_list[0] is None:
        return info
    iop = np.array(iop_list[0])
    n = np.cross(iop[:3], iop[3:])
    if abs(np.linalg.norm(n) - 1) > 1e-3 or abs(iop[:3] @ iop[3:]) > 1e-3:
        problems.append("ImageOrientationPatient is not orthonormal")
        return info
    info.orientation = _orientation(n)
    if info.orientation == "oblique":
        problems.append("oblique slice normal")
    pos = np.array([n @ np.array(v) for v in ipp_list])
    order = np.argsort(pos, kind="stable")
    keep, last = [], None
    for i in order:
        if last is None or pos[i] - last > _DUP_TOL:
            keep.append(i)
            last = pos[i]
    if len(keep) < len(order):
        problems.append(f"{len(order) - len(keep)} duplicate slice positions dropped (kept first)")
    info.files = [uid_files[i][0] for i in keep]
    info.positions = [float(pos[i]) for i in keep]
    info.ipps = [tuple(ipp_list[i]) for i in keep]  # type: ignore[misc]
    info.n_slices = len(keep)
    stacks, med = _split_stacks(np.array(info.positions))
    info.stacks, info.slice_spacing = stacks, med
    # Each stack is checked on its own: stacks of one series may have different in-plane origins, and the
    # loader gives every stack its own origin. The loader puts slices along the normal only, so a tilted
    # stack would be sheared.
    ipp_arr, pos_arr = np.array(info.ipps, float), np.array(info.positions)
    tilts = {k: t for k, idx in enumerate(stacks)
             if (t := _tilt(ipp_arr[idx], pos_arr[idx], n, ps0)) is not None}
    info.tilted_stacks = sorted(tilts)
    if tilts:
        k_worst = max(tilts, key=lambda k: tilts[k][1])
        drift, angle = tilts[k_worst]
        which = f" (stack {', '.join(str(k) for k in info.tilted_stacks)})" if len(stacks) > 1 else ""
        problems.append(f"gantry tilt / sheared stack{which}: slice positions drift {drift:.2f} mm sideways from "
                        f"the slice normal (about {angle:.1f} degrees); tilted stacks are not resampled")
    tilt = tilts.get(max(range(len(stacks)), key=lambda k: len(stacks[k]))) if stacks else None
    if len(stacks) > 1:
        lens = ", ".join(str(len(k)) for k in stacks)
        problems.append(f"{len(stacks)} separate stacks (slice counts {lens}); gaps in slice positions")
    longest = max((len(k) for k in stacks), default=0)
    info.volumetric = longest >= _MIN_STACK and tilt is None
    if longest < _MIN_STACK:
        problems.append(f"no stack with >= {_MIN_STACK} slices (longest {longest})")
    if thick is not None and med and abs(thick - med) > 0.01 * med + 0.01:
        problems.append(f"SliceThickness tag {thick:g} mm differs from position spacing {med:.3f} mm (positions used)")
    return info


def scan_series(ct_dir: Path) -> list[SeriesInfo]:
    """Find every DICOM image under ``ct_dir`` (recursive, any extension) and describe each series."""
    groups: dict[str, list[tuple[Path, object]]] = defaultdict(list)
    for root, _, names in os.walk(ct_dir):
        for name in names:
            p = Path(root) / name
            if name.upper() == "DICOMDIR":
                continue
            ds = _read_header(p)
            if ds is not None:
                groups[str(ds.SeriesInstanceUID)].append((p, ds))
    out = [_build(v) for v in groups.values()]
    out.sort(key=lambda i: (i.number, str(i.files[0])))
    return out


def _is_sharp(i: SeriesInfo) -> bool:
    k = i.kernel.upper().replace(" ", "")
    return "bone" in i.description.lower() or k in _SHARP_KERNELS or bool(_SHARP_RE.match(k.split("\\")[0]))


def _longest(i: SeriesInfo) -> list[int]:
    return max(i.stacks, key=len)


def choose_primary(series: list[SeriesInfo]) -> tuple[SeriesInfo, str]:
    """Finest voxel volume among volumetric CT series; ties (within 0.1%) prefer sharp/bone kernels, then most slices."""
    cands = [i for i in series if i.volumetric and i.modality.upper() == "CT" and i.slice_spacing]
    if not cands:
        tilted = [f"series {i.number}: {p}" for i in series for p in i.problems if p.startswith("gantry tilt")]
        raise ValueError("no volumetric CT series found (need a stack of >= 8 slices with consistent geometry)" + (
            ". " + "; ".join(tilted) + ". Ask for a reconstruction without gantry tilt (or with tilt correction)."
            if tilted else ""))
    vol = {id(i): i.pixel_spacing[0] * i.pixel_spacing[1] * i.slice_spacing for i in cands}  # type: ignore[operator]
    vmin = min(vol.values())
    tied = [i for i in cands if vol[id(i)] <= vmin * 1.001]
    best = max(tied, key=lambda i: (_is_sharp(i), i.n_slices))
    reason = (f"series {best.number} ('{best.description}', kernel {best.kernel or 'n/a'}) has the finest voxel volume "
              f"{vol[id(best)]:.4f} mm3 ({best.pixel_spacing[1]:g} x {best.pixel_spacing[0]:g} x "
              f"{best.slice_spacing:.3f} mm, {best.n_slices} slices) among {len(cands)} volumetric CT series")
    if len(tied) > 1:
        why = "sharp/bone kernel" if _is_sharp(best) else "largest slice count"
        reason += f"; {len(tied)} series tie on geometry, chosen for {why}"
    return best, reason + "."


def _same_grid(a: SeriesInfo, b: SeriesInfo) -> bool:
    sa, sb = _longest(a), _longest(b)
    if len(sa) != len(sb) or a.iop is None or b.iop is None:
        return False
    if (a.rows, a.cols) != (b.rows, b.cols) or np.max(np.abs(np.subtract(a.iop, b.iop))) > _IOP_TOL:
        return False
    if np.max(np.abs(np.subtract(a.pixel_spacing, b.pixel_spacing))) > 1e-4:
        return False
    if abs((a.slice_spacing or 0) - (b.slice_spacing or 0)) > 0.01:
        return False
    return float(np.max(np.abs(np.subtract(a.ipps[sa[0]], b.ipps[sb[0]])))) <= 0.01


def choose_soft(series: list[SeriesInfo], primary: SeriesInfo) -> SeriesInfo | None:
    """Same-grid volumetric series with a soft-tissue description, else any same-grid series with another kernel."""
    same = [i for i in series if i is not primary and i.volumetric and _same_grid(primary, i)]
    soft = [i for i in same if re.search(r"sft|soft|std", i.description, re.I)]
    if soft:
        return soft[0]
    other = [i for i in same if i.kernel.upper() != primary.kernel.upper() and not _is_sharp(i)]
    return other[0] if other else None


def choose_coarse(series: list[SeriesInfo], primary: SeriesInfo) -> list[SeriesInfo]:
    """Volumetric series with slice spacing >= 3x the primary's (independent coarse reconstructions)."""
    ps = primary.slice_spacing or 0.0
    return [i for i in series if i is not primary and i.volumetric and i.slice_spacing and i.slice_spacing >= 3 * ps]


def load_series(info: SeriesInfo, stack: int | None = None) -> sitk.Image:
    """Load one stack (default: the longest) as an int16 HU image with geometry from the DICOM headers."""
    if not info.volumetric or info.iop is None:
        raise ValueError(f"series {info.number} is not a volume: {'; '.join(info.problems)}")
    k = stack if stack is not None else max(range(len(info.stacks)), key=lambda j: len(info.stacks[j]))
    if k in info.tilted_stacks:
        raise ValueError(f"series {info.number} stack {k} has gantry tilt (sheared slices) and is not resampled")
    idx = info.stacks[k]
    n = len(idx)
    arr = np.empty((n, info.rows, info.cols), np.int16)
    for k, i in enumerate(idx):
        ds = pydicom.dcmread(info.files[i])
        px = ds.pixel_array
        slope, icpt = float(ds.get("RescaleSlope", 1) or 1), float(ds.get("RescaleIntercept", 0) or 0)
        if slope == 1.0 and icpt == round(icpt):
            v = px.astype(np.int32) + int(icpt)
        else:
            v = np.rint(px.astype(np.float32) * slope + icpt)
        arr[k] = np.clip(v, -32768, 32767)
    iop = np.array(info.iop)
    row_dir, col_dir = iop[:3], iop[3:]
    normal = np.cross(row_dir, col_dir)
    pos = np.array([info.positions[i] for i in idx])
    sp = float((pos[-1] - pos[0]) / (n - 1)) if n > 1 else float(info.slice_spacing or 1.0)
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing((float(info.pixel_spacing[1]), float(info.pixel_spacing[0]), sp))
    img.SetOrigin(tuple(float(v) for v in info.ipps[idx[0]]))
    img.SetDirection(tuple(float(v) for v in np.column_stack([row_dir, col_dir, normal]).ravel()))
    return img


def _short_path(p: Path) -> Path | None:
    """Windows 8.3 short form of an existing path (plain ASCII when 8.3 names are enabled), else None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, len(buf))
    except Exception:
        return None
    return Path(buf.value) if 0 < n < len(buf) else None


def _nifti_suffix(path: Path) -> str:
    return ".nii.gz" if path.name.lower().endswith(".nii.gz") else path.suffix


def _ascii_dirs(near: Path) -> list[Path]:
    """Folders whose path the ITK file I/O can open (it cannot open non-ASCII paths on Windows)."""
    out = [d for d in (_short_path(near), Path(tempfile.gettempdir())) if d is not None and str(d).isascii()]
    if not out:
        raise ValueError(f"Cannot write or read the image files: the folder path '{near}' contains non-ASCII "
                         "characters and no plain-ASCII temporary folder is available. Choose an output folder "
                         "whose path uses only plain letters and digits.")
    return out


def write_nifti(img: sitk.Image, path: Path) -> None:
    """Write NIfTI (SimpleITK converts LPS to the NIfTI RAS convention in the header).

    The header's 80-byte ``descrip`` field carries the research-use disclaimer, so the label stays with the
    file when it is copied out of the results folder. Paths with non-ASCII characters (which the ITK writer
    cannot open on Windows) are written through an ASCII temporary name and then moved into place."""
    from . import DISCLAIMER
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.SetMetaData("ITK_FileNotes", DISCLAIMER[:79])
    if str(path).isascii():
        sitk.WriteImage(img, str(path), useCompression=True)
        return
    for d in _ascii_dirs(path.parent):
        tmp = d / f"emgm_{secrets.token_hex(6)}{_nifti_suffix(path)}"
        try:
            sitk.WriteImage(img, str(tmp), useCompression=True)
        except RuntimeError:
            tmp.unlink(missing_ok=True)
            continue
        try:
            shutil.move(str(tmp), str(path))
        finally:
            tmp.unlink(missing_ok=True)
        return
    raise ValueError(f"Cannot write '{path}': choose an output folder whose path uses only plain letters and digits.")


def read_nifti(path: Path) -> sitk.Image:
    path = Path(path)
    if str(path).isascii():
        return sitk.ReadImage(str(path))
    short = _short_path(path.parent)  # the folder only: a file's 8.3 name loses the .nii.gz the reader looks for
    if short is not None and str(short / path.name).isascii() and path.is_file():
        return sitk.ReadImage(str(short / path.name))
    if not path.is_file():
        raise FileNotFoundError(path)
    tmp = Path(tempfile.gettempdir()) / f"emgm_{secrets.token_hex(6)}{_nifti_suffix(path)}"
    if not str(tmp).isascii():
        _ascii_dirs(path.parent)  # raises the plain-language error
    try:
        shutil.copyfile(path, tmp)
        return sitk.ReadImage(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)


def inventory_markdown(series: list[SeriesInfo]) -> str:
    """Markdown table of series plus a bullet list of problems (no identifying tags)."""
    head = ("| Series | Description | Orientation | Slices | Matrix | Pixel spacing mm | Slice spacing mm (positions) | "
            "SliceThickness tag mm | Stacks | Kernel | Volumetric |\n|---|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for i in series:
        sp = f"{i.slice_spacing:.3f}" if i.slice_spacing is not None else "n/a"
        th = f"{i.slice_thickness_tag:g}" if i.slice_thickness_tag is not None else "n/a"
        rows.append(f"| {i.number} | {i.description} | {i.orientation} | {i.n_slices} | {i.rows} x {i.cols} | "
                    f"{i.pixel_spacing[1]:g} x {i.pixel_spacing[0]:g} | {sp} | {th} | {len(i.stacks)} | "
                    f"{i.kernel or 'n/a'} | {'yes' if i.volumetric else 'no'} |")
    probs = [f"- Series {i.number} ({i.description}): {p}" for i in series for p in i.problems]
    return "\n".join([head, *rows, "", "Problems:", *(probs or ["- none"])]) + "\n"
