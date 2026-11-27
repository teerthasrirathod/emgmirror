"""Compare the MOOSE case-001 output with the source elbow annotations.

The source annotation convention is reviewed here for this one case: mask 1 is
distal humerus, mask 2 is proximal ulna. Radius has no source reference mask.
All comparisons are made on the physical CT grid, never by assuming array index
identity across arbitrary NIfTI files.
"""
from pathlib import Path
import json
import numpy as np
import SimpleITK as sitk
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "scratch/skeletal-primary-20261125/elbow_dataset/raw/patient001-005/001"
PRED_PATH = ROOT / "scratch/skeletal-primary-20261125/elbow_dataset/inference/moose_case001/labels.nii.gz"
OUT_PATH = ROOT / "docs/ct_platform/results/elbow_case001_comparison.json"

# Dataset-specific mapping established by overlaying source mask labels on CT
# and cross-checking the co-located humerus/ulna reference surfaces.
REFERENCE_LABELS = {1: "humerus_right", 2: "ulna_right"}

ct = sitk.ReadImage(str(CASE / "f1_001_RAW.nii.gz"))
ref_raw = sitk.ReadImage(str(CASE / "f2_001-mask.nii.gz"))
pred_raw = sitk.ReadImage(str(PRED_PATH))
ref_img = sitk.Resample(
    ref_raw, ct, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt16
)
pred_img = sitk.Resample(
    pred_raw, ct, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt16
)
ref = sitk.GetArrayViewFromImage(ref_img)
pred = sitk.GetArrayViewFromImage(pred_img)
meta = json.loads((PRED_PATH.parent / "result.json").read_text())
classes = {int(k): v for k, v in meta["classes"].items()}
spacing_zyx = tuple(reversed(ct.GetSpacing()))


def geometry(image):
    return {
        "size_xyz": list(image.GetSize()),
        "spacing_mm_xyz": list(image.GetSpacing()),
        "origin_mm_lps": list(image.GetOrigin()),
        "direction": list(image.GetDirection()),
    }


def dice(a, b):
    na, nb = int(np.count_nonzero(a)), int(np.count_nonzero(b))
    if not na and not nb:
        return None
    if not na or not nb:
        return 0.0
    return float(2 * np.count_nonzero(a & b) / (na + nb))


def hd95_mm(a, b):
    if not np.any(a) or not np.any(b):
        return None
    occupied = np.argwhere(a | b)
    lo = np.maximum(occupied.min(axis=0) - 2, 0)
    hi = np.minimum(occupied.max(axis=0) + 3, a.shape)
    sl = tuple(slice(int(x), int(y)) for x, y in zip(lo, hi))
    a_roi, b_roi = a[sl], b[sl]
    a_surface = a_roi & ~ndimage.binary_erosion(a_roi, structure=ndimage.generate_binary_structure(3, 1))
    b_surface = b_roi & ~ndimage.binary_erosion(b_roi, structure=ndimage.generate_binary_structure(3, 1))
    to_b = ndimage.distance_transform_edt(~b_surface, sampling=spacing_zyx)[a_surface]
    to_a = ndimage.distance_transform_edt(~a_surface, sampling=spacing_zyx)[b_surface]
    return float(max(np.percentile(to_b, 95), np.percentile(to_a, 95)))


def volume_ml(mask):
    return float(np.count_nonzero(mask) * np.prod(ct.GetSpacing()) / 1000.0)

results = {}
for ref_id, anatomy in REFERENCE_LABELS.items():
    model_id = next((i for i, name in classes.items() if name == anatomy), None)
    ref_mask = ref == ref_id
    pred_mask = (pred == model_id) if model_id is not None else np.zeros(ref.shape, dtype=bool)
    results[anatomy] = {
        "reference_label_id": ref_id,
        "model_class_id": model_id,
        "reference_voxels": int(np.count_nonzero(ref_mask)),
        "prediction_voxels": int(np.count_nonzero(pred_mask)),
        "reference_volume_ml": volume_ml(ref_mask),
        "prediction_volume_ml": volume_ml(pred_mask),
        "dice": dice(ref_mask, pred_mask),
        "hd95_mm": hd95_mm(ref_mask, pred_mask),
        "reference_status": "annotated",
        "review_status": "single_case_exploratory_comparison",
    }

radius_classes = {name: class_id for class_id, name in classes.items() if name.startswith("radius_")}
report = {
    "source": {
        "dataset": "201 healthy elbow CT cohort",
        "doi": "10.5281/zenodo.20305534",
        "license": "CC BY 4.0",
        "case_id": "001",
        "source_reference_scope": ["distal humerus", "proximal ulna"],
        "not_reference_annotated": ["radius", "fracture"],
    },
    "comparison": {
        "model_source": meta.get("source"),
        "model_seconds": meta.get("seconds"),
        "model_device": meta.get("meta", {}).get("device"),
        "model_classes": classes,
        "reference_label_mapping": {
            str(k): {"anatomy": v, "basis": "CT overlay plus source named humerus/ulna surfaces; case-specific"}
            for k, v in REFERENCE_LABELS.items()
        },
        "geometry": {
            "ct": geometry(ct),
            "reference_original": geometry(ref_raw),
            "model_original": geometry(pred_raw),
            "reference_resampled_to_ct": geometry(ref_img),
            "model_resampled_to_ct": geometry(pred_img),
            "resampling": "nearest-neighbour into the original CT physical grid; no CT resampling",
        },
        "metrics_on_matched_anatomy_only": results,
        "radius_model_output_voxels_by_class": {
            name: int(np.count_nonzero(pred == class_id)) for name, class_id in radius_classes.items()
        },
        "radius_reference_status": "not_annotated; model radius output is unscored, not a validated radius",
        "interpretation": "Single healthy right-elbow case; no fracture sensitivity claim. Exploratory model comparison, not clinical validation.",
    },
}
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
