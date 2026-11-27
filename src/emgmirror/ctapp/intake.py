"""Input adapters for CT app uploads.

Each adapter resolves an upload package to one scalar 3D volume, then writes a
canonical NIfTI volume while preserving the physical geometry reported by the
reader. The original upload remains alongside the derived volume.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Tuple

import SimpleITK as sitk

VOLUME_SUFFIXES = (".nii.gz", ".nii", ".nrrd", ".nhdr", ".mha", ".mhd", ".hdr")
DIRECT_SUFFIXES = (".nii.gz", ".nii", ".nrrd", ".mha")
UPLOAD_SUFFIXES = (*DIRECT_SUFFIXES, ".nhdr", ".mhd", ".hdr", ".zip")
# Intake has to leave room for the decoded image, model input, label map, and
# temporary arrays. A voxel-only default hid this memory cost on larger scans.
# Operators can raise the byte budget for a larger worker; the legacy voxel
# override remains available for deployments that already configure it.
MAX_WORKING_BYTES = int(os.environ.get("CTAPP_MAX_WORKING_BYTES", str(2 * 1024**3)))
ESTIMATED_WORKING_BYTES_PER_VOXEL = 24
MAX_INPUT_VOXELS = int(os.environ.get(
    "CTAPP_MAX_INPUT_VOXELS",
    str(MAX_WORKING_BYTES // ESTIMATED_WORKING_BYTES_PER_VOXEL),
))


def _check_volume_size(size: tuple[int, ...], source: str) -> dict:
    voxels = math.prod(size)
    estimated = voxels * ESTIMATED_WORKING_BYTES_PER_VOXEL
    if voxels > MAX_INPUT_VOXELS or estimated > MAX_WORKING_BYTES:
        raise ValueError(
            f"{source} has {voxels:,} voxels; estimated working memory is "
            f"{estimated / 1024**3:.2f} GiB (configured budget "
            f"{MAX_WORKING_BYTES / 1024**3:.2f} GiB). The original upload is retained. "
            "Use a worker with more memory and configure CTAPP_MAX_WORKING_BYTES; "
            "CTAPP_MAX_INPUT_VOXELS can impose an additional voxel limit."
        )
    return {
        "voxel_count": voxels,
        "estimated_working_bytes": estimated,
        "working_memory_budget_bytes": MAX_WORKING_BYTES,
        "estimate_bytes_per_voxel": ESTIMATED_WORKING_BYTES_PER_VOXEL,
        "estimate_scope": "intake-side buffers; excludes model-specific inference workspace",
    }


def upload_suffix(name: str) -> str | None:
    low = name.lower()
    return next((suffix for suffix in UPLOAD_SUFFIXES if low.endswith(suffix)), None)


def _safe_extract(archive: Path, destination: Path, max_bytes: int, max_files: int = 100_000) -> list[Path]:
    """Extract a ZIP without path traversal, symlinks, duplicate paths, or unbounded expansion."""
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    seen: set[Path] = set()
    with zipfile.ZipFile(archive) as zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
        if len(members) > max_files or sum(info.file_size for info in members) > max_bytes:
            raise ValueError("The archive expands beyond the supported size or file-count limit.")
        total = 0
        for info in members:
            raw = info.filename.replace("\\", "/")
            rel = PurePosixPath(raw)
            mode = (info.external_attr >> 16) & 0o170000
            if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts) or ":" in raw:
                raise ValueError("The ZIP contains an unsafe file path; the upload was rejected.")
            if mode == stat.S_IFLNK:
                raise ValueError("The ZIP contains a symbolic link; the upload was rejected.")
            target = destination.joinpath(*rel.parts)
            resolved = target.resolve()
            if destination.resolve() not in resolved.parents or resolved == destination.resolve():
                raise ValueError("The archive contains a path that escapes the extraction directory; upload rejected.")
            if resolved in seen:
                raise ValueError("The archive contains duplicate file paths; rename the duplicates and try again.")
            seen.add(resolved)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("The archive expands beyond the supported size limit.")
                    out.write(chunk)
            if written != info.file_size:
                raise ValueError("The archive contains an incomplete file.")
            extracted.append(target)
    return extracted


def _volume_candidates(paths: list[Path]) -> list[Path]:
    found = []
    for path in paths:
        low = path.name.lower()
        if any(low.endswith(suffix) for suffix in VOLUME_SUFFIXES):
            found.append(path)
    return found


def _read_volume(path: Path) -> tuple[sitk.Image, dict]:
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    try:
        reader.ReadImageInformation()
    except RuntimeError as exc:
        raise ValueError(f"Could not read the uploaded volume: {str(exc).splitlines()[0][:180]}") from None
    if reader.GetDimension() != 3 or reader.GetNumberOfComponents() != 1:
        raise ValueError(
            f"Expected one scalar 3D CT volume; found {reader.GetDimension()}D with "
            f"{reader.GetNumberOfComponents()} component(s)."
        )
    size = tuple(int(v) for v in reader.GetSize())
    spacing = tuple(float(v) for v in reader.GetSpacing())
    if any(v <= 0 for v in size) or any(not (v > 0 and v < 1e5) for v in spacing):
        raise ValueError("The volume has invalid dimensions or spacing in its header.")
    resource_estimate = _check_volume_size(size, "The volume")
    try:
        image = reader.Execute()
    except RuntimeError as exc:
        raise ValueError(f"Could not load the volume pixels: {str(exc).splitlines()[0][:180]}") from None
    low_name = path.name.lower()
    source_format = ".nii.gz" if low_name.endswith('.nii.gz') else path.suffix.lower()
    info = {
        "adapter": "simpleitk-volume",
        "source_format": source_format,
        "reader": reader.GetImageIO(),
        "dimensions_xyz": size,
        "spacing_mm_xyz": spacing,
        "origin_mm": tuple(float(v) for v in image.GetOrigin()),
        "direction": tuple(float(v) for v in image.GetDirection()),
        "coordinate_frame": "reader-defined physical coordinates",
        "intensity_units": "unknown; retained from source volume",
        "resource_estimate": resource_estimate,
    }
    return image, info


def _validate_sidecars(path: Path, package_root: Path) -> None:
    """Ensure detached pixel data referenced by a header stays inside the extracted package."""
    low = path.name.lower()
    if low.endswith(".hdr"):
        expected = path.with_suffix(".img")
        if not expected.is_file():
            expected = next(
                (p for p in path.parent.iterdir()
                 if p.is_file() and p.suffix.lower() == ".img" and p.stem.lower() == path.stem.lower()),
                expected,
            )
        if not expected.is_file() or package_root.resolve() not in expected.resolve().parents:
            raise ValueError("The NIfTI/Analyze header needs its paired .img file in the same upload package.")
        return
    if not (low.endswith((".mhd", ".nhdr", ".nrrd"))):
        return
    try:
        with path.open("rb") as stream:
            header = stream.read(65536).decode("latin-1")
    except OSError as exc:
        raise ValueError(f"Could not read the image header: {exc}") from None
    for line in header.splitlines():
        stripped = line.strip()
        low_line = stripped.lower()
        if low_line.startswith("elementdatafile"):
            value = stripped.split("=", 1)[-1].strip()
        elif low_line.startswith("data file:"):
            value = stripped.split(":", 1)[-1].strip()
        else:
            continue
        if value.upper() == "LOCAL":
            if low.endswith(".mhd"):
                raise ValueError("MetaImage LOCAL payloads must be uploaded as a single .mha file.")
            continue
        if value.upper().startswith(("LIST", "%")):
            raise ValueError("This multi-file image layout is not supported yet; upload a single paired data file.")
        rel = PurePosixPath(value.replace("\\", "/"))
        if rel.is_absolute() or ":" in value or any(part in ("", ".", "..") for part in rel.parts):
            raise ValueError("The image header references data outside its package; the upload was rejected.")
        data = path.parent.joinpath(*rel.parts).resolve()
        if package_root.resolve() not in data.parents or not data.is_file():
            raise ValueError("The image header's paired data file is missing from the upload package.")

# ---------------------------------------------------------------------------
# Adapter registry – extensible matcher/handler pairs
# ---------------------------------------------------------------------------

_ADAPTER_REGISTRY: Dict[str, Dict[str, Callable]] = {}


def register_adapter(
    name: str,
    matcher: Callable[[Path, List[Path], Path], bool],
    handler: Callable[[Path, List[Path], Path, Path, Path, int], dict],
) -> None:
    """Register a new intake adapter.

    ``matcher`` receives the original upload path, the list of extracted paths (empty for non‑ZIP),
    and the package root directory. It returns ``True`` when the adapter can handle the upload.
    ``handler`` receives the same arguments plus ``job_dir``, ``output`` and ``max_expanded_bytes``
    and must return the metadata dict.
    """
    _ADAPTER_REGISTRY[name] = {"matcher": matcher, "handler": handler}

# ---------------------------------------------------------------------------
# Concrete adapters used in Phase 1
# ---------------------------------------------------------------------------

def _matcher_simpleitk_volume(upload: Path, extracted: List[Path], package_root: Path) -> bool:
    # Direct volume files or a single volume inside a ZIP (no DICOM series).
    suffix = upload_suffix(upload.name)
    if suffix is None:
        return False
    if suffix == ".zip":
        from emgmirror.ct import dicom
        if dicom.scan_series(package_root):
            return False
        return len(_volume_candidates(extracted)) == 1
    return suffix in DIRECT_SUFFIXES or suffix in (".nhdr", ".mhd", ".hdr")


def _handler_simpleitk_volume(
    upload: Path,
    extracted: List[Path],
    package_root: Path,
    job_dir: Path,
    output: Path,
    max_expanded_bytes: int,
) -> dict:
    """Process a single volume (direct file or extracted from a ZIP)."""
    if upload.suffix.lower() == ".zip":
        source = _volume_candidates(extracted)[0]
        pkg_root = package_root
    else:
        source = upload
        pkg_root = job_dir
    _validate_sidecars(source, pkg_root)
    image, info = _read_volume(source)
    if upload.suffix.lower() == ".zip":
        info["package_format"] = "ZIP"
    sitk.WriteImage(image, str(output), True)
    canonical = sitk.ReadImage(str(output))
    info.update({
        "canonical_format": "NIfTI-1 single-file gzip",
        "canonical_volume": output.name,
        "canonical_dimensions_xyz": tuple(int(v) for v in canonical.GetSize()),
        "canonical_spacing_mm_xyz": tuple(float(v) for v in canonical.GetSpacing()),
        "canonical_origin_mm": tuple(float(v) for v in canonical.GetOrigin()),
        "canonical_direction": tuple(float(v) for v in canonical.GetDirection()),
        "conversion_history": [{"operation": "container decode and NIfTI serialization", "resampled": False}],
        "source_package_retained": True,
    })
    return info


def _matcher_dicom_series(upload: Path, extracted: List[Path], package_root: Path) -> bool:
    if upload.suffix.lower() != ".zip":
        return False
    from emgmirror.ct import dicom
    return bool(dicom.scan_series(package_root))


def _handler_dicom_series(
    upload: Path,
    extracted: List[Path],
    package_root: Path,
    job_dir: Path,
    output: Path,
    max_expanded_bytes: int,
) -> dict:
    """Process a ZIP containing a DICOM series."""
    from emgmirror.ct import dicom
    dicom_series = dicom.scan_series(package_root)
    selected, _reason = dicom.choose_primary(dicom_series)
    resource_estimate = _check_volume_size(
        (int(selected.cols), int(selected.rows), int(selected.n_slices)), "The selected DICOM series"
    )
    image = dicom.load_series(selected)
    alternatives = [
        {"series_number": int(s.number), "modality": s.modality, "slices": int(s.n_slices), "selected": s is selected}
        for s in dicom_series
    ]
    info = {
        "adapter": "dicom-series",
        "source_format": "DICOM in ZIP",
        "selected_series_number": int(selected.number),
        "selection_reason": (
            "Selected by the existing primary‑series policy: finest volumetric CT voxel volume; "
            "ties prefer a sharp/bone kernel, then the series with the most slices."
        ),
        "available_series": alternatives,
        "intensity_units": "HU from DICOM rescale slope/intercept",
        "dimensions_xyz": tuple(int(v) for v in image.GetSize()),
        "spacing_mm_xyz": tuple(float(v) for v in image.GetSpacing()),
        "origin_mm": tuple(float(v) for v in image.GetOrigin()),
        "direction": tuple(float(v) for v in image.GetDirection()),
        "coordinate_frame": "DICOM patient coordinates as read by SimpleITK",
        "resource_estimate": resource_estimate,
    }
    sitk.WriteImage(image, str(output), True)
    canonical = sitk.ReadImage(str(output))
    info.update({
        "canonical_format": "NIfTI-1 single-file gzip",
        "canonical_volume": output.name,
        "canonical_dimensions_xyz": tuple(int(v) for v in canonical.GetSize()),
        "canonical_spacing_mm_xyz": tuple(float(v) for v in canonical.GetSpacing()),
        "canonical_origin_mm": tuple(float(v) for v in canonical.GetOrigin()),
        "canonical_direction": tuple(float(v) for v in canonical.GetDirection()),
        "conversion_history": [{"operation": "container decode and NIfTI serialization", "resampled": False}],
        "source_package_retained": True,
    })
    return info

# Register built‑in adapters
register_adapter("simpleitk-volume", _matcher_simpleitk_volume, _handler_simpleitk_volume)
register_adapter("dicom-series", _matcher_dicom_series, _handler_dicom_series)

# ---------------------------------------------------------------------------
# normalize_upload – core entry point
# ---------------------------------------------------------------------------

def normalize_upload(upload: Path, job_dir: Path, output: Path, max_expanded_bytes: int) -> dict:
    """Resolve an upload and write a canonical NIfTI volume.

    The function extracts ZIP archives when needed, then iterates the registered
    adapters to find one whose ``matcher`` returns ``True``. The selected adapter's
    ``handler`` performs the conversion and returns the metadata dictionary.
    """
    suffix = upload_suffix(upload.name)
    if suffix is None:
        raise ValueError(
            "Unsupported upload format. Provide a CT series ZIP or a supported 3D medical image volume."
        )

    extracted: List[Path] = []
    package_root: Path = job_dir
    if suffix == ".zip":
        package_root = job_dir / "input_package"
        extracted = _safe_extract(upload, package_root, max_expanded_bytes)

    for name, entry in _ADAPTER_REGISTRY.items():
        if entry["matcher"](upload, extracted, package_root):
            info = entry["handler"](upload, extracted, package_root, job_dir, output, max_expanded_bytes)
            break
    else:
        raise ValueError("No suitable intake adapter found for the uploaded data.")

    (job_dir / "intake.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info
