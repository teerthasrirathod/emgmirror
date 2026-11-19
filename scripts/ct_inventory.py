"""Header-only inventory of the clinical CT DICOM series.

Reads every file under 01_clinical_CT/IMAGES with stop_before_pixels=True,
groups by SeriesInstanceUID (grouping key only, never written out), and writes
results/ct/inventory.json and results/ct/inventory.md. Identifying tags
(names, IDs, dates, UIDs, institution, operator) are never written.
"""

import json
import logging
import math
import re
import statistics
import sys
import warnings
from collections import defaultdict
from pathlib import Path

import pydicom
from pydicom.multival import MultiValue

warnings.simplefilter("ignore")
logging.getLogger("pydicom").setLevel(logging.CRITICAL)

REPO = Path(__file__).resolve().parents[1]
CT_DIR = REPO / "01_clinical_CT"
IMG_DIR = CT_DIR / "IMAGES"
OUT_DIR = REPO / "results" / "ct"

IOP_TOL = 1e-4
SPACING_OUTLIER_MM = 0.01
DUP_MM = 1e-3


# ---------- value helpers (return plain Python types only) ----------

def f_or_none(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def i_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def s_or_none(v):
    if v is None:
        return None
    if isinstance(v, (MultiValue, list, tuple)):
        s = "\\".join(str(x).strip() for x in v)
    else:
        s = str(v).strip()
    return s if s else None


def floats_or_none(v):
    if v is None:
        return None
    try:
        return [float(x) for x in v]
    except (TypeError, ValueError):
        return None


def natkey(name):
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name))


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def normal_from_iop(iop):
    n = cross(iop[0:3], iop[3:6])
    norm = math.sqrt(dot(n, n))
    if norm == 0.0:
        return None
    return [x / norm for x in n]


def orient_class(n):
    if n is None:
        return None
    a = [abs(x) for x in n]
    k = max(range(3), key=lambda i: a[i])
    if a[k] < 0.95:
        return "oblique"
    return ["sagittal", "coronal", "axial"][k]


# ---------- reading ----------

def read_slice(path):
    ds = pydicom.dcmread(str(path), stop_before_pixels=True)
    iop = floats_or_none(ds.get("ImageOrientationPatient"))
    ipp = floats_or_none(ds.get("ImagePositionPatient"))
    if iop is not None and len(iop) != 6:
        iop = None
    if ipp is not None and len(ipp) != 3:
        ipp = None
    return {
        "uid": s_or_none(ds.get("SeriesInstanceUID")),  # grouping key only
        "series_number": i_or_none(ds.get("SeriesNumber")),
        "description": s_or_none(ds.get("SeriesDescription")),
        "modality": s_or_none(ds.get("Modality")),
        "image_type": s_or_none(ds.get("ImageType")),
        "rows": i_or_none(ds.get("Rows")),
        "cols": i_or_none(ds.get("Columns")),
        "pixel_spacing": floats_or_none(ds.get("PixelSpacing")),
        "slice_thickness": f_or_none(ds.get("SliceThickness")),
        "kernel": s_or_none(ds.get("ConvolutionKernel")),
        "kvp": f_or_none(ds.get("KVP")),
        "xray_tube_current": f_or_none(ds.get("XRayTubeCurrent")),
        "recon_diameter": f_or_none(ds.get("ReconstructionDiameter")),
        "gantry_tilt": f_or_none(ds.get("GantryDetectorTilt")),
        "body_part": s_or_none(ds.get("BodyPartExamined")),
        "patient_position": s_or_none(ds.get("PatientPosition")),
        "manufacturer": s_or_none(ds.get("Manufacturer")),
        "model": s_or_none(ds.get("ManufacturerModelName")),
        "iop": iop,
        "ipp": ipp,
        "rescale_slope": f_or_none(ds.get("RescaleSlope")),
        "rescale_intercept": f_or_none(ds.get("RescaleIntercept")),
        "bits_stored": i_or_none(ds.get("BitsStored")),
        "pixel_repr": i_or_none(ds.get("PixelRepresentation")),
        "photometric": s_or_none(ds.get("PhotometricInterpretation")),
    }


# ---------- per-series analysis ----------

def build_series(items):
    items = sorted(items, key=lambda t: natkey(t[0]))
    names = [nm for nm, _ in items]
    recs = [r for _, r in items]

    ref_iop = next((r["iop"] for r in recs if r["iop"] is not None), None)
    n = normal_from_iop(ref_iop) if ref_iop is not None else None
    iop_identical = ref_iop is not None and all(
        r["iop"] is not None and max(abs(a - b) for a, b in zip(r["iop"], ref_iop)) <= IOP_TOL
        for r in recs
    )

    geo = []
    n_missing_ipp = 0
    if n is not None:
        for nm, r in items:
            if r["ipp"] is None:
                n_missing_ipp += 1
            else:
                geo.append((dot(n, r["ipp"]), nm))
    geo.sort(key=lambda t: (t[0], natkey(t[1])))
    d_vals = [d for d, _ in geo]
    ordered = [nm for _, nm in geo]
    ordered_set = set(ordered)
    files = ordered + [nm for nm in names if nm not in ordered_set]

    spacings = [d_vals[i + 1] - d_vals[i] for i in range(len(d_vals) - 1)]
    if spacings:
        med = statistics.median(spacings)
        outliers = sum(1 for s in spacings if abs(s - med) > SPACING_OUTLIER_MM)
        dups = sum(1 for s in spacings if s < DUP_MM)
        sp_min, sp_max, sp_med = min(spacings), max(spacings), med
    else:
        outliers, dups, sp_min, sp_max, sp_med = None, None, None, None, None

    def first(key):
        return next((r[key] for r in recs if r[key] is not None), None)

    tc = [r["xray_tube_current"] for r in recs if r["xray_tube_current"] is not None]
    slopes = sorted({r["rescale_slope"] for r in recs if r["rescale_slope"] is not None})
    intercepts = sorted({r["rescale_intercept"] for r in recs if r["rescale_intercept"] is not None})

    varies = []
    for key, label in [("rows", "Rows"), ("cols", "Columns"), ("pixel_spacing", "PixelSpacing"),
                       ("slice_thickness", "SliceThickness"), ("kernel", "ConvolutionKernel"),
                       ("kvp", "KVP"), ("gantry_tilt", "GantryDetectorTilt")]:
        if len({repr(r[key]) for r in recs if r[key] is not None}) > 1:
            varies.append(label)

    ref_iop_out = [round(x, 4) for x in ref_iop] if ref_iop is not None else None
    n_out = [float(x) for x in n] if n is not None else None

    series = {
        "series_number": first("series_number"),
        "series_description": first("description"),
        "modality": first("modality"),
        "image_type": first("image_type"),
        "n_slices": len(recs),
        "rows": first("rows"),
        "columns": first("cols"),
        "pixel_spacing_mm": first("pixel_spacing"),
        "slice_thickness_mm": first("slice_thickness"),
        "convolution_kernel": first("kernel"),
        "kvp": first("kvp"),
        "xray_tube_current_min": float(min(tc)) if tc else None,
        "xray_tube_current_max": float(max(tc)) if tc else None,
        "reconstruction_diameter_mm": first("recon_diameter"),
        "gantry_detector_tilt_deg": first("gantry_tilt"),
        "body_part_examined": first("body_part"),
        "patient_position": first("patient_position"),
        "manufacturer": first("manufacturer"),
        "manufacturer_model_name": first("model"),
        "image_orientation_patient": ref_iop_out,
        "iop_identical_all_slices": bool(iop_identical),
        "slice_normal": n_out,
        "orientation_class": orient_class(n),
        "position_d_min_mm": float(d_vals[0]) if d_vals else None,
        "position_d_max_mm": float(d_vals[-1]) if d_vals else None,
        "extent_mm": float(d_vals[-1] - d_vals[0]) if d_vals else None,
        "median_spacing_mm": float(sp_med) if sp_med is not None else None,
        "min_spacing_mm": float(sp_min) if sp_min is not None else None,
        "max_spacing_mm": float(sp_max) if sp_max is not None else None,
        "spacing_outliers": outliers,
        "duplicate_positions": dups,
        "n_slices_missing_position": n_missing_ipp,
        "rescale_slope_values": [float(x) for x in slopes],
        "rescale_intercept_values": [float(x) for x in intercepts],
        "bits_stored": first("bits_stored"),
        "pixel_representation": first("pixel_repr"),
        "photometric_interpretation": first("photometric"),
        "files": files,
    }
    return series, varies


# ---------- DICOMDIR ----------

def read_dicomdir():
    try:
        dd = pydicom.dcmread(str(CT_DIR / "DICOMDIR"))
        types = [str(rec.get("DirectoryRecordType", "")).strip()
                 for rec in dd.get("DirectoryRecordSequence", [])]
        return {"n_series": types.count("SERIES"), "n_images": types.count("IMAGE")}, None
    except Exception as exc:  # report only the exception type, not its message
        return {"n_series": None, "n_images": None}, type(exc).__name__


# ---------- output ----------

def cell(x):
    if x is None:
        return "n/a"
    return str(x).replace("|", "\\|")


def fmt(x, spec):
    return "n/a" if x is None else format(x, spec)


def write_markdown(series, n_files, n_unreadable, dicomdir, anomalies):
    lines = ["# Clinical CT series inventory", ""]
    lines.append(f"Files found: {n_files}; unreadable: {n_unreadable}; series: {len(series)}; "
                 f"DICOMDIR SERIES/IMAGE records: {cell(dicomdir['n_series'])}/{cell(dicomdir['n_images'])}.")
    lines.append("")
    lines.append("| Series # | Description | Orientation | Slices | Matrix (Rows x Cols) | Pixel spacing mm "
                 "| Slice spacing from positions mm (median) | SliceThickness tag mm | Extent mm | Kernel | kVp "
                 "| Spacing outliers |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in series:
        ps = s["pixel_spacing_mm"]
        ps_txt = f"{ps[0]:g} x {ps[1]:g}" if ps and len(ps) == 2 else "n/a"
        matrix = f"{cell(s['rows'])} x {cell(s['columns'])}"
        lines.append("| " + " | ".join([
            cell(s["series_number"]),
            cell(s["series_description"]),
            cell(s["orientation_class"]),
            cell(s["n_slices"]),
            matrix,
            ps_txt,
            fmt(s["median_spacing_mm"], ".3f"),
            fmt(s["slice_thickness_mm"], "g"),
            fmt(s["extent_mm"], ".2f"),
            cell(s["convolution_kernel"]),
            fmt(s["kvp"], "g"),
            cell(s["spacing_outliers"]),
        ]) + " |")
    lines.append("")
    lines.append("## Anomalies")
    lines.append("")
    if anomalies:
        for a in anomalies:
            lines.append(f"- {a}")
    else:
        lines.append("- None found.")
    lines.append("")
    (OUT_DIR / "inventory.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    paths = sorted((p for p in IMG_DIR.rglob("*") if p.is_file()), key=lambda p: natkey(p.name))
    n_files = len(paths)
    n_unreadable = 0
    slices = []
    for p in paths:
        try:
            slices.append((p.name, read_slice(p)))
        except Exception:
            n_unreadable += 1

    groups = defaultdict(list)
    for name, rec in slices:
        groups[rec["uid"]].append((name, rec))

    built = [build_series(items) for items in groups.values()]
    built.sort(key=lambda sv: (sv[0]["series_number"] is None,
                               sv[0]["series_number"] or 0,
                               sv[0]["series_description"] or "",
                               sv[0]["n_slices"]))
    series = [s for s, _ in built]

    dicomdir, dd_error = read_dicomdir()

    total_slices = sum(s["n_slices"] for s in series)
    if total_slices != n_files - n_unreadable:
        print(f"CHECK FAILED: slices {total_slices} != n_files - n_unreadable {n_files - n_unreadable}")
        sys.exit(1)

    anomalies = []
    if n_unreadable:
        anomalies.append(f"{n_unreadable} of {n_files} files could not be read as DICOM and were skipped.")
    if None in groups:
        anomalies.append(f"{len(groups[None])} readable files have no SeriesInstanceUID and were grouped together.")
    if dd_error:
        anomalies.append(f"DICOMDIR could not be parsed ({dd_error}); its counts are null.")
    else:
        if dicomdir["n_series"] != len(series):
            anomalies.append(f"DICOMDIR lists {dicomdir['n_series']} SERIES records but {len(series)} series "
                             f"were found in the image headers.")
        if dicomdir["n_images"] != n_files - n_unreadable:
            anomalies.append(f"DICOMDIR lists {dicomdir['n_images']} IMAGE records but "
                             f"{n_files - n_unreadable} readable image files were found.")

    for s, varies in built:
        label = f"Series {s['series_number']} ({s['series_description']}, {s['n_slices']} slices)"
        if s["n_slices"] == 1:
            anomalies.append(f"{label}: single slice, no spacing can be computed.")
        if s["spacing_outliers"]:
            anomalies.append(f"{label}: non-uniform spacing, {s['spacing_outliers']} of "
                             f"{s['n_slices'] - 1} spacings differ from the median "
                             f"({s['median_spacing_mm']:.4f} mm) by more than 0.01 mm "
                             f"(min {s['min_spacing_mm']:.4f} mm, max {s['max_spacing_mm']:.4f} mm).")
        if s["duplicate_positions"]:
            anomalies.append(f"{label}: {s['duplicate_positions']} duplicate slice positions (spacing < 0.001 mm).")
        tilt = s["gantry_detector_tilt_deg"]
        if tilt is not None and tilt != 0:
            anomalies.append(f"{label}: GantryDetectorTilt = {tilt:g} (not 0).")
        if not s["iop_identical_all_slices"]:
            anomalies.append(f"{label}: ImageOrientationPatient is not identical across all slices.")
        if s["orientation_class"] == "oblique":
            anomalies.append(f"{label}: oblique slice normal.")
        if len(s["rescale_slope_values"]) > 1 or len(s["rescale_intercept_values"]) > 1:
            anomalies.append(f"{label}: RescaleSlope/RescaleIntercept take more than one value "
                             f"(slope {s['rescale_slope_values']}, intercept {s['rescale_intercept_values']}).")
        if s["n_slices_missing_position"]:
            anomalies.append(f"{label}: {s['n_slices_missing_position']} slices lack ImagePositionPatient "
                             f"and are excluded from spacing statistics.")
        if varies:
            anomalies.append(f"{label}: tags vary across slices: {', '.join(varies)}.")

    result = {
        "n_files": n_files,
        "n_unreadable": n_unreadable,
        "dicomdir": dicomdir,
        "series": series,
    }
    with open(OUT_DIR / "inventory.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    write_markdown(series, n_files, n_unreadable, dicomdir, anomalies)

    print(f"n_files={n_files} n_unreadable={n_unreadable} n_series={len(series)} "
          f"dicomdir_series={dicomdir['n_series']} dicomdir_images={dicomdir['n_images']} "
          f"slice_sum_check=ok")
    print(f"wrote {OUT_DIR / 'inventory.json'}")
    print(f"wrote {OUT_DIR / 'inventory.md'}")


if __name__ == "__main__":
    main()
