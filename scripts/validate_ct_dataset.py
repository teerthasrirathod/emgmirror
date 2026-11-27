#!/usr/bin/env python3
"""Validate a CT dataset JSONL manifest without loading scan contents.

This standard-library-only validator checks the repository's schema-critical contract,
split leakage, and safe file references. It does not de-identify data or validate clinical
labels. Run without ``--data-root`` for metadata-only validation.

Exit codes: 0 = valid; 1 = invalid manifest; 2 = input/usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import ntpath
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

MANIFEST_VERSION = "1.0"
SPLITS = {"train", "validation", "internal_test", "external_test"}
FOV_STATUSES = {"in_fov", "partial_fov", "out_of_fov", "uncertain", "not_applicable"}
ANNOTATION_STATUSES = {"annotated", "not_annotated", "uncertain", "not_applicable"}
FINDING_STATUSES = {"present", "absent", "uncertain", "not_assessed", "not_applicable"}
PROCESSING_STATUSES = {"segmented", "not_scheduled", "unavailable", "failed", "not_detected", "partial", "uncertain"}
REVIEW_STATUSES = {"unreviewed", "reviewed", "disagreement", "adjudicated"}
ASSESSMENT_STATUSES = {"positive", "negative", "uncertain", "unreviewed", "not_applicable"}
SPLIT_MODES = {"patient_grouped", "site_held_out"}
TECHNICAL_KEYS = {
    "manufacturer", "scanner_model", "kvp", "exposure_mas", "slice_thickness_mm",
    "reconstruction_kernel", "contrast_status", "intensity_scale", "protocol_category",
}
FORBIDDEN_KEY = re.compile(
    r"(^|_)(patient.?name|name|dob|birth.?date|accession|study.?instance.?uid|series.?instance.?uid|"
    r"sop.?instance.?uid|patient.?id|mrn|address|institution|facility|operator|physician|"
    r"acquisition.?date|study.?date|series.?date|birth.?time)(_|$)", re.IGNORECASE
)
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


def _err(errors: list[str], line: int, message: str) -> None:
    errors.append(f"line {line}: {message}")


def _object(value: Any, label: str, line: int, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _err(errors, line, f"{label} must be an object")
        return None
    return value


def _array(value: Any, label: str, line: int, errors: list[str], *, required: bool = False) -> list[Any] | None:
    if value is None and not required:
        return None
    if not isinstance(value, list):
        _err(errors, line, f"{label} must be an array")
        return None
    return value


def _string(value: Any, label: str, line: int, errors: list[str], *, nonempty: bool = True) -> bool:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        _err(errors, line, f"{label} must be a{' non-empty' if nonempty else ''} string")
        return False
    return True


def _id(value: Any, label: str, line: int, errors: list[str]) -> bool:
    if not isinstance(value, str) or not OPAQUE_ID.fullmatch(value):
        _err(errors, line, f"{label} must be an opaque 8-128 character identifier")
        return False
    return True


def _reject_unexpected_fields(value: dict[str, Any], allowed: set[str], label: str, line: int,
                             errors: list[str]) -> None:
    unexpected = sorted(str(key) for key in value if key not in allowed)
    if unexpected:
        _err(errors, line, f"{label} contains unapproved fields: {unexpected}")


def _safe_relative_path(value: Any, label: str, line: int, errors: list[str]) -> str | None:
    if not _string(value, label, line, errors):
        return None
    raw = value
    # Check both platform grammars. Treat either slash as a separator so a Windows path
    # cannot bypass validation when the validator is run on POSIX (or vice versa).
    win = PureWindowsPath(raw)
    posix = PurePosixPath(raw.replace("\\", "/"))
    drive, _ = ntpath.splitdrive(raw)
    components = raw.replace("\\", "/").split("/")
    if (win.is_absolute() or bool(drive) or posix.is_absolute() or raw.startswith(("\\\\", "//"))
            or any(part == ".." for part in components)):
        _err(errors, line, f"{label} must be relative and must not contain drive, UNC, absolute, or '..' paths")
        return None
    normalized = "/".join(part for part in components if part not in ("", "."))
    if not normalized:
        _err(errors, line, f"{label} must name a file or directory")
        return None
    return normalized


def _check_finite_array(value: Any, label: str, length: int, line: int, errors: list[str], *, positive: bool = False,
                        direction: bool = False) -> None:
    if not isinstance(value, list) or len(value) != length:
        _err(errors, line, f"{label} must be an array of {length} numbers")
        return
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            _err(errors, line, f"{label} must contain only finite numbers")
            return
        if positive and item <= 0:
            _err(errors, line, f"{label} values must be positive")
            return
        if direction and not -1.000001 <= item <= 1.000001:
            _err(errors, line, f"{label} direction cosines must be between -1 and 1")
            return


def _validate_geometry(value: Any, label: str, line: int, errors: list[str]) -> None:
    geom = _object(value, label, line, errors)
    if geom is None:
        return
    _reject_unexpected_fields(geom, {"dimensions_xyz", "spacing_mm_xyz", "origin_mm", "direction_3x3", "coordinate_frame"}, label, line, errors)
    dims = geom.get("dimensions_xyz")
    if not isinstance(dims, list) or len(dims) != 3 or any(
            isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in dims):
        _err(errors, line, f"{label}.dimensions_xyz must contain exactly three positive integers")
    _check_finite_array(geom.get("spacing_mm_xyz"), f"{label}.spacing_mm_xyz", 3, line, errors, positive=True)
    _check_finite_array(geom.get("origin_mm"), f"{label}.origin_mm", 3, line, errors)
    _check_finite_array(geom.get("direction_3x3"), f"{label}.direction_3x3", 9, line, errors, direction=True)
    _string(geom.get("coordinate_frame"), f"{label}.coordinate_frame", line, errors)


def _scan_for_forbidden_keys(value: Any, label: str, line: int, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and FORBIDDEN_KEY.search(key):
                _err(errors, line, f"{label} contains a prohibited identifier-bearing key: {key!r}")
            _scan_for_forbidden_keys(nested, label, line, errors)
    elif isinstance(value, list):
        for nested in value:
            _scan_for_forbidden_keys(nested, label, line, errors)


def _validate_record(record: Any, line: int, errors: list[str], case_ids: set[tuple[str, str]],
                     patient_splits: dict[tuple[str, str], str], site_splits: dict[tuple[str, str], str],
                     strategy_by_dataset: dict[str, tuple[str, tuple[str, ...]]], referenced: list[tuple[int, str]]) -> None:
    if not isinstance(record, dict):
        _err(errors, line, "each JSONL value must be an object")
        return
    _scan_for_forbidden_keys(record, "record", line, errors)
    _reject_unexpected_fields(record, {
        "manifest_version", "dataset_id", "case_id", "patient_group_id", "site_id", "split", "split_strategy",
        "cohort_ids", "source", "modality", "technical_metadata", "original_geometry", "derived_volumes",
        "region_fov", "anatomy_annotations", "model_runs", "fracture_assessment", "fracture_lesions", "extensions",
    }, "record", line, errors)

    for field in ("manifest_version", "dataset_id", "case_id", "patient_group_id", "site_id", "split",
                  "split_strategy", "cohort_ids", "source", "modality", "original_geometry",
                  "anatomy_annotations", "fracture_assessment", "fracture_lesions"):
        if field not in record:
            _err(errors, line, f"missing required field {field!r}")
    if record.get("manifest_version") != MANIFEST_VERSION:
        _err(errors, line, f"manifest_version must be {MANIFEST_VERSION!r}")

    dataset_id = record.get("dataset_id")
    case_id = record.get("case_id")
    patient_id = record.get("patient_group_id")
    site_id = record.get("site_id")
    for value, label in ((dataset_id, "dataset_id"), (case_id, "case_id"),
                         (patient_id, "patient_group_id"), (site_id, "site_id")):
        _id(value, label, line, errors)
    split = record.get("split")
    if not isinstance(split, str) or split not in SPLITS:
        _err(errors, line, f"split must be one of {sorted(SPLITS)}")

    if isinstance(dataset_id, str) and isinstance(case_id, str):
        key = (dataset_id, case_id)
        if key in case_ids:
            _err(errors, line, f"duplicate case_id {case_id!r} in dataset {dataset_id!r}")
        case_ids.add(key)
    if isinstance(dataset_id, str) and isinstance(patient_id, str) and isinstance(split, str) and split in SPLITS:
        key = (dataset_id, patient_id)
        previous = patient_splits.setdefault(key, split)
        if previous != split:
            _err(errors, line, f"patient_group_id {patient_id!r} appears in both {previous!r} and {split!r} splits")

    strategy = _object(record.get("split_strategy"), "split_strategy", line, errors)
    if strategy is not None:
        _reject_unexpected_fields(strategy, {"mode", "version", "held_out_site_ids"}, "split_strategy", line, errors)
        mode = strategy.get("mode")
        if not isinstance(mode, str) or mode not in SPLIT_MODES:
            _err(errors, line, f"split_strategy.mode must be one of {sorted(SPLIT_MODES)}")
        version = strategy.get("version")
        _string(version, "split_strategy.version", line, errors)
        held = strategy.get("held_out_site_ids", [])
        if not isinstance(held, list) or any(not isinstance(x, str) for x in held):
            _err(errors, line, "split_strategy.held_out_site_ids must be an array of opaque IDs")
            held = []
        else:
            for item in held:
                _id(item, "held_out_site_id", line, errors)
        if mode == "site_held_out" and not held:
            _err(errors, line, "site_held_out strategy requires held_out_site_ids")
        if mode == "patient_grouped" and held:
            _err(errors, line, "patient_grouped strategy must not declare held_out_site_ids")
        if isinstance(dataset_id, str) and isinstance(mode, str) and mode in SPLIT_MODES:
            signature = (mode, tuple(sorted(set(held))))
            previous = strategy_by_dataset.setdefault(dataset_id, signature)
            if previous != signature:
                _err(errors, line, "split_strategy must be consistent within a dataset")
        if (mode == "site_held_out" and isinstance(dataset_id, str) and isinstance(site_id, str)
                and isinstance(split, str) and split in SPLITS):
            if split == "external_test" and site_id not in held:
                _err(errors, line, "external_test site is not listed in held_out_site_ids")
            if split != "external_test" and site_id in held:
                _err(errors, line, "held-out site must use external_test split")
            key = (dataset_id, site_id)
            previous = site_splits.setdefault(key, split)
            if previous != split:
                _err(errors, line, f"site_id {site_id!r} appears in multiple splits under site_held_out strategy")

    cohorts = _array(record.get("cohort_ids"), "cohort_ids", line, errors, required=True)
    if cohorts is not None:
        for index, value in enumerate(cohorts):
            _string(value, f"cohort_ids[{index}]", line, errors)

    source = _object(record.get("source"), "source", line, errors)
    if source is not None:
        _reject_unexpected_fields(source, {"kind", "format_id", "adapter_id", "paths"}, "source", line, errors)
        for key in ("kind", "format_id", "adapter_id"):
            _string(source.get(key), f"source.{key}", line, errors)
        paths = _array(source.get("paths"), "source.paths", line, errors, required=True)
        if paths is not None:
            if not paths:
                _err(errors, line, "source.paths must contain at least one path")
            for index, path in enumerate(paths):
                safe = _safe_relative_path(path, f"source.paths[{index}]", line, errors)
                if safe is not None:
                    referenced.append((line, safe))
    _string(record.get("modality"), "modality", line, errors)
    _validate_geometry(record.get("original_geometry"), "original_geometry", line, errors)

    metadata = record.get("technical_metadata")
    if metadata is not None:
        metadata_obj = _object(metadata, "technical_metadata", line, errors)
        if metadata_obj is not None:
            _reject_unexpected_fields(metadata_obj, TECHNICAL_KEYS, "technical_metadata", line, errors)
            unknown = sorted(set(metadata_obj) - TECHNICAL_KEYS)
            if unknown:
                _err(errors, line, f"technical_metadata contains unapproved keys: {unknown}")

    derived = _array(record.get("derived_volumes", []), "derived_volumes", line, errors)
    if derived is not None:
        for index, item in enumerate(derived):
            obj = _object(item, f"derived_volumes[{index}]", line, errors)
            if obj is None:
                continue
            _reject_unexpected_fields(obj, {"artifact_id", "path", "geometry", "parent_artifact_id", "conversion_history"},
                                      f"derived_volumes[{index}]", line, errors)
            _validate_geometry(obj.get("geometry"), f"derived_volumes[{index}].geometry", line, errors)
            safe = _safe_relative_path(obj.get("path"), f"derived_volumes[{index}].path", line, errors)
            if safe is not None:
                referenced.append((line, safe))
            history = _array(obj.get("conversion_history"), f"derived_volumes[{index}].conversion_history", line, errors, required=True)
            if history is not None and not history:
                _err(errors, line, f"derived_volumes[{index}].conversion_history must not be empty")
            if history is not None:
                for history_index, operation in enumerate(history):
                    op = _object(operation, f"derived_volumes[{index}].conversion_history[{history_index}]", line, errors)
                    if op is None:
                        continue
                    _reject_unexpected_fields(op, {"operation", "software", "version", "resampling", "reason_code"},
                                              f"derived_volumes[{index}].conversion_history[{history_index}]", line, errors)
                    for key in ("operation", "software", "version"):
                        _string(op.get(key), f"conversion_history[{history_index}].{key}", line, errors)

    region_fov = _array(record.get("region_fov", []), "region_fov", line, errors)
    if region_fov is not None:
        for index, item in enumerate(region_fov):
            obj = _object(item, f"region_fov[{index}]", line, errors)
            if obj is None:
                continue
            _reject_unexpected_fields(obj, {"region_id", "status", "basis"}, f"region_fov[{index}]", line, errors)
            _string(obj.get("region_id"), f"region_fov[{index}].region_id", line, errors)
            if not isinstance(obj.get("status"), str) or obj.get("status") not in FOV_STATUSES:
                _err(errors, line, f"region_fov[{index}].status is invalid")

    anatomy = _array(record.get("anatomy_annotations"), "anatomy_annotations", line, errors, required=True)
    if anatomy is not None:
        seen_anatomy: set[str] = set()
        for index, item in enumerate(anatomy):
            obj = _object(item, f"anatomy_annotations[{index}]", line, errors)
            if obj is None:
                continue
            _reject_unexpected_fields(obj, {"anatomy_id", "region_id", "fov_status", "annotation_status", "finding_status",
                                            "mask_path", "reference_artifact_id", "review_status"},
                                      f"anatomy_annotations[{index}]", line, errors)
            anatomy_id = obj.get("anatomy_id")
            _string(anatomy_id, f"anatomy_annotations[{index}].anatomy_id", line, errors)
            if isinstance(anatomy_id, str) and anatomy_id in seen_anatomy:
                _err(errors, line, f"duplicate anatomy_id {anatomy_id!r} in anatomy_annotations")
            if isinstance(anatomy_id, str):
                seen_anatomy.add(anatomy_id)
            _string(obj.get("region_id"), f"anatomy_annotations[{index}].region_id", line, errors)
            if not isinstance(obj.get("fov_status"), str) or obj.get("fov_status") not in FOV_STATUSES:
                _err(errors, line, f"anatomy_annotations[{index}].fov_status is invalid")
            if not isinstance(obj.get("annotation_status"), str) or obj.get("annotation_status") not in ANNOTATION_STATUSES:
                _err(errors, line, f"anatomy_annotations[{index}].annotation_status is invalid")
            if obj.get("finding_status") is not None and (
                    not isinstance(obj.get("finding_status"), str) or obj.get("finding_status") not in FINDING_STATUSES):
                _err(errors, line, f"anatomy_annotations[{index}].finding_status is invalid")
            if obj.get("review_status") is not None and (
                    not isinstance(obj.get("review_status"), str) or obj.get("review_status") not in REVIEW_STATUSES):
                _err(errors, line, f"anatomy_annotations[{index}].review_status is invalid")
            if obj.get("annotation_status") == "annotated" and not obj.get("mask_path"):
                _err(errors, line, f"anatomy_annotations[{index}] is annotated but has no mask_path")
            if obj.get("finding_status") == "absent" and (
                    obj.get("annotation_status") != "annotated"
                    or not isinstance(obj.get("review_status"), str)
                    or obj.get("review_status") not in {"reviewed", "adjudicated"}):
                _err(errors, line, f"anatomy_annotations[{index}] may record absent only after reviewed annotation")
            if obj.get("mask_path") is not None:
                safe = _safe_relative_path(obj.get("mask_path"), f"anatomy_annotations[{index}].mask_path", line, errors)
                if safe is not None:
                    referenced.append((line, safe))

    model_runs = _array(record.get("model_runs", []), "model_runs", line, errors)
    if model_runs is not None:
        for index, item in enumerate(model_runs):
            obj = _object(item, f"model_runs[{index}]", line, errors)
            if obj is None:
                continue
            _reject_unexpected_fields(obj, {"model_id", "task_id", "version", "anatomy_ids", "status", "output_path", "qc_status"},
                                      f"model_runs[{index}]", line, errors)
            for key in ("model_id", "task_id", "version"):
                _string(obj.get(key), f"model_runs[{index}].{key}", line, errors)
            if not isinstance(obj.get("status"), str) or obj.get("status") not in PROCESSING_STATUSES:
                _err(errors, line, f"model_runs[{index}].status is invalid")
            anatomy_ids = _array(obj.get("anatomy_ids", []), f"model_runs[{index}].anatomy_ids", line, errors)
            if anatomy_ids is not None:
                for anatomy_index, anatomy_id in enumerate(anatomy_ids):
                    _string(anatomy_id, f"model_runs[{index}].anatomy_ids[{anatomy_index}]", line, errors)
            if obj.get("qc_status") is not None and (
                    not isinstance(obj.get("qc_status"), str)
                    or obj.get("qc_status") not in {"pass", "warn", "fail", "not_assessed"}):
                _err(errors, line, f"model_runs[{index}].qc_status is invalid")
            if obj.get("output_path") is not None:
                safe = _safe_relative_path(obj.get("output_path"), f"model_runs[{index}].output_path", line, errors)
                if safe is not None:
                    referenced.append((line, safe))

    assessment = _object(record.get("fracture_assessment"), "fracture_assessment", line, errors)
    lesions = _array(record.get("fracture_lesions"), "fracture_lesions", line, errors, required=True)
    if assessment is not None:
        status = assessment.get("status")
        _reject_unexpected_fields(assessment, {"status", "scope_region_id", "reason_code", "review"}, "fracture_assessment", line, errors)
        if not isinstance(status, str) or status not in ASSESSMENT_STATUSES:
            _err(errors, line, "fracture_assessment.status is invalid")
        _string(assessment.get("scope_region_id"), "fracture_assessment.scope_region_id", line, errors)
        review = _object(assessment.get("review"), "fracture_assessment.review", line, errors)
        if review is not None:
            _reject_unexpected_fields(review, {"coverage", "status", "reviewer_count", "reviewer_roles", "reviewer_ids",
                                               "adjudication", "protocol_version"}, "fracture_assessment.review", line, errors)
            coverage = review.get("coverage")
            review_status = review.get("status")
            count = review.get("reviewer_count")
            if not isinstance(coverage, str) or coverage not in {"complete", "partial", "unknown"}:
                _err(errors, line, "fracture_assessment.review.coverage is invalid")
            if not isinstance(review_status, str) or review_status not in REVIEW_STATUSES:
                _err(errors, line, "fracture_assessment.review.status is invalid")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                _err(errors, line, "fracture_assessment.review.reviewer_count must be a nonnegative integer")
            if status == "negative" and (coverage != "complete" or not isinstance(review_status, str)
                                          or review_status not in {"reviewed", "adjudicated"}
                                          or isinstance(count, bool) or not isinstance(count, int) or count == 0):
                _err(errors, line, "negative assessment requires complete reviewed/adjudicated coverage and at least one reviewer")
            if status == "not_applicable" and not assessment.get("reason_code"):
                _err(errors, line, "not_applicable fracture assessment requires a reason_code")
    if lesions is not None:
        status = assessment.get("status") if assessment is not None else None
        if status == "positive" and not lesions:
            _err(errors, line, "positive fracture assessment requires at least one lesion")
        if isinstance(status, str) and status in {"negative", "not_applicable"} and lesions:
            _err(errors, line, f"{status} fracture assessment must not contain fracture lesions")
        seen_lesions: set[str] = set()
        for index, item in enumerate(lesions):
            obj = _object(item, f"fracture_lesions[{index}]", line, errors)
            if obj is None:
                continue
            _reject_unexpected_fields(obj, {"lesion_id", "anatomy_id", "laterality", "location", "fracture_type", "acuity",
                                            "subtlety", "review_status", "mask_path", "reviewer_ids"},
                                      f"fracture_lesions[{index}]", line, errors)
            lesion_id = obj.get("lesion_id")
            _id(lesion_id, f"fracture_lesions[{index}].lesion_id", line, errors)
            if isinstance(lesion_id, str) and lesion_id in seen_lesions:
                _err(errors, line, f"duplicate lesion_id {lesion_id!r}")
            if isinstance(lesion_id, str):
                seen_lesions.add(lesion_id)
            _string(obj.get("anatomy_id"), f"fracture_lesions[{index}].anatomy_id", line, errors)
            if not isinstance(obj.get("laterality"), str) or obj.get("laterality") not in {"left", "right", "midline", "bilateral", "unknown"}:
                _err(errors, line, f"fracture_lesions[{index}].laterality is invalid")
            _string(obj.get("fracture_type"), f"fracture_lesions[{index}].fracture_type", line, errors)
            if not isinstance(obj.get("review_status"), str) or obj.get("review_status") not in REVIEW_STATUSES | {"uncertain"}:
                _err(errors, line, f"fracture_lesions[{index}].review_status is invalid")
            if obj.get("acuity") is not None and (
                    not isinstance(obj.get("acuity"), str)
                    or obj.get("acuity") not in {"acute", "subacute", "chronic", "healing", "unknown"}):
                _err(errors, line, f"fracture_lesions[{index}].acuity is invalid")
            location = obj.get("location")
            if location is not None:
                loc = _object(location, f"fracture_lesions[{index}].location", line, errors)
                if loc is not None:
                    _reject_unexpected_fields(loc, {"voxel_ijk", "world_xyz_mm", "coordinate_frame", "artifact_id", "bbox"},
                                              f"fracture_lesions[{index}].location", line, errors)
                    location_present = False
                    if "voxel_ijk" in loc:
                        _check_finite_array(loc["voxel_ijk"], f"fracture_lesions[{index}].location.voxel_ijk", 3, line, errors)
                        if isinstance(loc["voxel_ijk"], list) and any(
                                isinstance(x, (int, float)) and not isinstance(x, bool) and x < 0 for x in loc["voxel_ijk"]):
                            _err(errors, line, f"fracture_lesions[{index}].location.voxel_ijk cannot be negative")
                        location_present = True
                    if "world_xyz_mm" in loc:
                        _check_finite_array(loc["world_xyz_mm"], f"fracture_lesions[{index}].location.world_xyz_mm", 3, line, errors)
                        _string(loc.get("coordinate_frame"), f"fracture_lesions[{index}].location.coordinate_frame", line, errors)
                        location_present = True
                    if "bbox" in loc:
                        _check_finite_array(loc["bbox"], f"fracture_lesions[{index}].location.bbox", 6, line, errors)
                        location_present = True
                    if not location_present:
                        _err(errors, line, f"fracture_lesions[{index}].location needs voxel_ijk, world_xyz_mm, or bbox")
                    if loc.get("artifact_id") is not None:
                        _string(loc.get("artifact_id"), f"fracture_lesions[{index}].location.artifact_id", line, errors)
            subtlety = obj.get("subtlety")
            if subtlety is not None and (isinstance(subtlety, bool) or not isinstance(subtlety, int) or not 1 <= subtlety <= 5):
                _err(errors, line, f"fracture_lesions[{index}].subtlety must be an integer from 1 to 5")
            if obj.get("mask_path") is not None:
                safe = _safe_relative_path(obj.get("mask_path"), f"fracture_lesions[{index}].mask_path", line, errors)
                if safe is not None:
                    referenced.append((line, safe))

    extensions = record.get("extensions", {})
    ext_obj = _object(extensions, "extensions", line, errors)
    if ext_obj is not None:
        for key in ext_obj:
            if not isinstance(key, str) or not re.fullmatch(r"x-[a-z0-9][a-z0-9._-]*", key):
                _err(errors, line, f"extension key {key!r} must use the reviewed x- prefix")
            value = ext_obj[key]
            if isinstance(value, (dict, list)) or (isinstance(value, str) and len(value) > 200):
                _err(errors, line, f"extension {key!r} must be a scalar with at most 200 characters")


def _check_data_root(root_arg: str, referenced: list[tuple[int, str]], errors: list[str]) -> None:
    root = Path(root_arg).resolve()
    if not root.is_dir():
        errors.append(f"data root is not a directory: {root}")
        return
    for line, relative in referenced:
        candidate = (root / Path(*PurePosixPath(relative).parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            _err(errors, line, f"referenced path escapes data root (including symlink resolution): {relative!r}")
            continue
        if not candidate.exists():
            _err(errors, line, f"referenced file does not exist under data root: {relative!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a CT dataset JSONL manifest without reading scan contents.")
    parser.add_argument("--manifest", required=True, help="JSONL manifest, one case object per line")
    parser.add_argument("--data-root", help="Optional local root for source and annotation artifacts; validates containment and existence")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    errors: list[str] = []
    case_ids: set[tuple[str, str]] = set()
    patient_splits: dict[tuple[str, str], str] = {}
    site_splits: dict[tuple[str, str], str] = {}
    strategy_by_dataset: dict[str, tuple[str, tuple[str, ...]]] = {}
    referenced: list[tuple[int, str]] = []
    try:
        with manifest_path.open("r", encoding="utf-8-sig") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if not raw_line.strip():
                    continue
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    _err(errors, line_number, f"invalid JSON: {exc.msg} (column {exc.colno})")
                    continue
                _validate_record(record, line_number, errors, case_ids, patient_splits, site_splits,
                                 strategy_by_dataset, referenced)
    except OSError as exc:
        print(f"error: cannot read manifest: {exc}", file=sys.stderr)
        return 2

    if args.data_root:
        _check_data_root(args.data_root, referenced, errors)
    if errors:
        print(f"Manifest invalid: {len(errors)} issue(s)", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Manifest validation passed (metadata only; no clinical or de-identification validation performed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
