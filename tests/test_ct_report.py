"""Tests of the CT technical report generator on a fabricated results dict (pure Python, no imaging)."""

import copy
import re

import pytest

from emgmirror.ct import DISCLAIMER
from emgmirror.ct.report import build_report, headline

HEADINGS = ["# CT to 3D report", "## Headline numbers", "## Series", "## Geometry", "## Method and parameters",
            "## Structures", "## Validation", "## Where the labels are unreliable (failure modes)", "## Honest limits",
            "## Timings"]
FORBIDDEN = ["excellent", "robust", "accurate ", "state-of-the-art", "perfect"]


def _mesh(vol, change, wt=True, si=0, comps=1, nv=1000, nf=2000):
    return {"voxel_volume_mm3": 0.1, "volume_mm3": vol, "volume_change_pct": change, "n_vertices": nv, "n_faces": nf}, \
        {"watertight": wt, "self_intersections": si, "n_components": comps, "volume_mm3": vol, "area_mm2": 5000.0,
         "volume_vs_voxels_pct": -0.4}


def _structs():
    out = {}
    for name, kind, vol, chg, wt, si in [("radius", "bone", 30.0, -0.8, True, 0), ("radius_medullary", "bone", 12.0,
        -1.1, True, 0),
                                         ("ulna", "bone", 28.0, -0.6, True, 0), ("ulna_medullary", "bone", 10.0, -2.0,
                                             False, 3),
                                         ("metal", "metal", 4.0, -0.3, True, 0), ("skin", "skin", 900.0, -0.1, True, 0),
                                         ("muscle", "muscle", 500.0, 0.2, True, 0), ("fat", "fat", 300.0, 0.1, True,
                                             0)]:
        m, q = _mesh(vol * 1000, chg, wt, si)
        out[name] = {"kind": kind, "label": name, "group": "Bones", "volume_cm3": vol, "mesh_grid_mm": [0.4, 0.4, 0.5],
                     "mesh": m, "qc": q, "seconds": 3.2}
    return out


def _overlap(msd, dice, note=""):
    return {"dice": dice, "dice_nearest": dice, "dice_grid": "test", "volume_ref_mm3": 4.0e4, "volume_test_mm3": 4.1e4,
            "volume_diff_pct": 2.5, "msd_mm": msd, "rms_mm": 0.9, "hd95_mm": 1.4 if msd else None,
                "hausdorff_mm": 2.6 if msd else None,
            "signed_bias_mm": 0.12 if msd else None, "n_surface_points": [100, 90], "common_fov_mm": [50.0, 40.0,
                100.0],
            "margin_mm": 3.0, "voxel_floor_mm": 1.2, "sampling_error_bound_mm": 0.3, "note": note}


def make_results():
    ov = {"all bone: series 3 (coronal, 3.0 mm) stack 0": _overlap(0.50, 0.93),
          "all bone: series 4 (sagittal, 3.0 mm) stack 0": _overlap(0.80, 0.90),
          "all bone: series 5 (coronal, 5.0 mm) stack 0": _overlap(None, None,
              "one mask has no surface inside the common field of view"),
          "metal: series 3 (coronal, 3.0 mm) stack 0": _overlap(0.4, 0.8)}
    return {
        "disclaimer": DISCLAIMER, "input": "scan_folder",
        "primary": {"number": 2, "description": "forearm axial thin", "kernel": "B70", "orientation": "axial",
                    "reason": "thinnest slices with a sharp kernel", "slice_thickness_tag_mm": 0.6},
        "soft": {"number": 7, "description": "forearm soft", "kernel": "B30"},
        "coarse": [{"series": 3, "stack": 0, "orientation": "coronal", "slices": 40, "spacing_mm": [0.8, 0.8, 3.0],
                    "seconds": 21.0, "flags": ["coarse run found no ulna"]},
                   {"series": 4, "stack": 0, "orientation": "sagittal", "slices": 35, "spacing_mm": [0.8, 0.8, 3.0],
                       "seconds": 19.5},
                   {"series": 5, "stack": 0, "error": "MemoryError: cannot allocate"}],
        "geometry": {"size": [512, 512, 300], "spacing_mm": [0.39, 0.39, 0.6], "origin_mm": [-100.0, -90.5, 12.25],
                     "direction": [1, 0, 0, 0, 1, 0, 0, 0, 1]},
        "parameters": {"pad_min_fraction": 0.002, "body_hu": None, "body_tissue_hu": [-300.0, 300.0],
            "name_bones": True,
                       "close_mm": 1.5, "bone_noise_k": 6.0},
        "pipeline": {"soft_mesh_mm": 1.0, "sweep_offsets_hu": [-150, -100, 0, 100, 150], "viewer_voxels": 6000000,
                     "min_coarse_slices": 8},
        "names": {"10": "radius_cortical", "11": "radius_medullary", "12": "ulna_cortical", "13": "ulna_medullary",
                  "2": "metal", "3": "muscle"},
        "segmentation": {"padding": {"value_hu": -3024, "fraction": 0.31}, "bone_thresholds": {"low_hu": 312.456789,
                         "high_hu": 800, "note": "half-maximum"}, "long": list(range(40)), "short": [1.23456, 2],
                         "a": {"b": {"c": {"d": {"e": 1}}}}, "timing": {"x": 1.0},
                             "flags": ["metal streaks cross the radius"]},
        "structures": _structs(),
        "validation": {
            "accuracy_floor": {"spacing_mm": [0.39, 0.39, 0.6], "min_spacing_mm": 0.39, "max_spacing_mm": 0.6,
                               "half_max_spacing_mm": 0.3, "half_diagonal_mm": 0.4, "voxel_volume_mm3": 0.09},
            "overlap": ov,
            "threshold_sweep": {"reference_hu": 300.0, "thresholds_hu": [250.0, 300.0, 350.0],
                "central_difference_hu": [250.0, 350.0],
                                "table": [{"threshold_hu": t, "bone above threshold": v}
                                          for t, v in ((250.0, 7.4e4), (300.0, 7.0e4), (350.0, 6.7e4))],
                                "structures": {"bone above threshold": {"volume_at_reference_mm3": 7.0e4,
                                    "dV_dHU_mm3": -70.0,
                                               "pct_per_100hu": -10.0, "range_pct": 10.0, "min_mm3": 6.7e4,
                                                   "max_mm3": 7.4e4,
                                               "area_mm2": 10000.0, "shift_mm_per_100hu": -0.7,
                                                   "range_shift_mm": 0.7}}},
            "hu_sanity": {"n_samples": 1000, "stride": 4,
                          "air": {"value": -990.0, "expected_range": [-1050.0, -950.0], "ok": True, "n": 100},
                          "fat": {"value": -30.0, "expected_range": [-140.0, -60.0], "ok": False, "peak_fraction": 0.1},
                          "muscle": {"value": 55.0, "expected_range": [20.0, 80.0], "ok": True},
                          "cortical": {"value": 1200.0, "expected_range": [500.0, 2200.0], "ok": True},
                          "max": {"value": 3071.0, "expected_range": [None, None], "ok": False, "clipped": True,
                                  "scale": "clipped (12 bit)", "min": -3024.0}, "ok": False},
            "bone_sanity": {"radius": {"volume_cm3": 42.0, "cortical_fraction": 0.7, "length_mm": 150.0,
                "length_robust_mm": 148.0,
                                       "max_diameter_mm": 22.0, "median_diameter_mm": 14.0, "touches_border": ["z_min"],
                                       "n_components": 2,
                                           "flags": ["bone touches the scan edge (z_min): length is a lower bound"]},
                            "ulna": {"volume_cm3": 38.0, "cortical_fraction": 0.68, "length_mm": 240.0,
                                "length_robust_mm": 238.0,
                                     "max_diameter_mm": 20.0, "median_diameter_mm": 12.0, "touches_border": [],
                                         "n_components": 1,
                                     "flags": []}},
            "mesh_vs_mask": {"radius": {"vertex_mean_abs_mm": 0.01, "mean_abs_mm": 0.12, "rms_mm": 0.15,
                "p95_mm": 0.3, "max_mm": 0.4,
                                        "signed_mean_mm": -0.05, "voxel_floor_mm": 0.4},
                             "ulna": {"vertex_mean_abs_mm": 0.01, "mean_abs_mm": 0.2, "rms_mm": 0.25, "p95_mm": 0.4,
                                 "max_mm": 0.5,
                                      "signed_mean_mm": -0.04, "voxel_floor_mm": 0.4}},
            "threshold_sweep_error": "ValueError: demo sweep message"},
        "measurements": {"tissue": {"fat_cm3": 300.0, "muscle_cm3": 500.0, "bone_cm3": 80.0, "metal_cm3": 4.0,
            "fat_pct": 33.9,
                                    "muscle_pct": 56.6}, "flags": ["metal streaks cross the radius",
                                        "second measurement flag"]},
        "timings_s": {"scan": 1.5, "segment": 300.2, "total": 700.0},
    }


@pytest.fixture(scope="module")
def results():
    return make_results()


@pytest.fixture(scope="module")
def report(results):
    return build_report(results, "# Inventory\n\n| series | n |\n|---|---|\n| 2 | 300 |\n", None)


def test_headings_in_order(report):
    pos = [report.index("\n" + h + "\n") if h[:2] != "# " else report.index(h) for h in HEADINGS]
    assert pos == sorted(pos)
    assert len([ln for ln in report.splitlines() if ln.startswith("## Validation")]) == 1


def test_disclaimer_first_and_last(report):
    lines = [ln for ln in report.splitlines() if ln.strip()]
    assert lines[0] == "# CT to 3D report" and lines[1] == DISCLAIMER and lines[-1] == DISCLAIMER
    assert "not a certified medical device" in lines[2] and "no human expert has reviewed" in lines[2]


def test_all_parameters_present(report, results):
    for k in list(results["parameters"]) + list(results["pipeline"]):
        assert f"| {k} |" in report


def test_headline_numbers_with_units(report, results):
    for s in ["0.390 x 0.390 x 0.600 mm", "0.400 mm", "0.600 mm", "0.900 to 0.930", "0.500 to 0.800 mm", "1.400 mm",
              "2.60 mm", "0.120 mm", "n = 2 of 3 stacks", "x coarse floor", "-10.00 % volume per 100 HU",
                  "-0.700 mm per 100 HU",
              "2.00 %", "0.200 mm", "3 of 4 bone meshes"]:
        assert s in report, s
    text = headline(results)
    assert "0.500 to 0.800 mm" in text and "% volume per 100 HU" in text and text.splitlines()[-1] == DISCLAIMER
    assert 8 <= len(text.splitlines()) <= 20


def test_no_none_or_nan(report, results):
    for t in (report, headline(results), build_report({}, ""), headline({})):
        assert not re.search(r"\b(None|nan|NaN)\b", t)


def test_empty_inputs():
    r = build_report({}, "")
    assert "not measured" in r and "No automatic flags were raised" in r
    assert headline({}).splitlines()[-1] == DISCLAIMER


@pytest.mark.parametrize("key", list(make_results()))
def test_missing_top_level_key(key):
    res = copy.deepcopy(make_results())
    del res[key]
    assert HEADINGS[-1] in build_report(res, "inventory")
    headline(res)


def test_failure_modes(report):
    sec = report.split("## Where the labels are unreliable")[1].split("## Honest limits")[0]
    assert "ulna_medullary: not watertight" in sec and "3 self-intersections" in sec
    assert "HU check fat" in sec and "failed" in sec
    assert "check failed, MemoryError" in sec
    assert "coarse run found no ulna" in sec and "bone radius: bone touches the scan edge" in sec
    assert sec.count("metal streaks cross the radius") == 1 and "second measurement flag" in sec


def test_weak_bones_edge_sensitivity_and_new_checks_are_reported():
    res = copy.deepcopy(make_results())
    v = res["validation"]
    v["overlap"]["radius: series 3 (coronal, 3.0 mm) stack 0"] = {**_overlap(0.6, 0.91), "volume_diff_pct": -4.0}
    v["overlap"]["ulna: series 3 (coronal, 3.0 mm) stack 0"] = {
        **_overlap(1.6, 0.65, "the coarse run splits or names this bone differently"), "volume_diff_pct": 62.0}
    v["edge_sensitivity"] = {"reference_fraction": 0.5, "max_abs_shift_mm": 0.21, "note": "delivered bone labels",
                             "fractions": {"0.4": {"volume_change_cm3": 1.2, "shift_mm": 0.12},
                                           "0.6": {"volume_change_cm3": -2.1, "shift_mm": -0.21}}}
    v["enclosed_tissue"] = {"radius": {"enclosed_cm3": 0.3, "n_regions": 4, "flags": ["a straight-edged region"],
                                       "largest": [{"volume_mm3": 225.0, "box_fill": 0.9, "labels": [2]}]}}
    v["mesh_vs_mask"]["ulna"]["max_mm"] = 7.5
    res["notes"] = {"threshold_sweep": "The sweep measures the thresholded bone mask, an intermediate step"}
    rep = build_report(res, "inventory")
    head = rep.split("## Series")[0]
    assert "0.650 (ulna, series 3 (coronal, 3.0 mm) stack 0)" in head  # weakest bone next to the all-bone figures
    assert "62.0 % (ulna" in head
    assert "0.120 mm at 0.4, -0.210 mm at 0.6" in head
    assert "thresholded mask" in head and "It is not the delivered bone" in head
    assert "7.500 mm" in head  # largest distance, not only the largest mean
    assert "Notes on the comparisons:" in head and "Comparisons without a value" not in rep
    sec = rep.split("## Where the labels are unreliable")[1]
    assert "bone radius: a straight-edged region" in sec and "mesh ulna: up to 7.50 mm" in sec
    assert "### Tissue enclosed by bone" in rep and "box fill 0.90" in rep
    assert "### Notes on how the checks were run" in rep and "intermediate step" in rep
    assert "pieces joined by an edge (count)" in rep


def test_weakest_bone_ignores_metal_and_body_and_skipped_stacks_are_named():
    res = copy.deepcopy(make_results())
    v = res["validation"]
    v["overlap"]["radius: series 3 (coronal, 3.0 mm) stack 0"] = _overlap(0.6, 0.91)
    v["overlap"]["metal: series 3 (coronal, 3.0 mm) stack 0"] = {**_overlap(0.4, 0.30), "volume_diff_pct": 90.0}
    v["overlap"]["body: series 3 (coronal, 3.0 mm) stack 0"] = {**_overlap(0.4, 0.20), "volume_diff_pct": -80.0}
    res["coarse"].append({"series": 6, "stack": 0, "orientation": "sagittal", "slices": 2,
                          "skipped": "only 2 slices; at least 8 are needed"})
    rep = build_report(res, "inventory")
    head = rep.split("## Series")[0]
    assert "0.910 (radius" in head and "metal" not in head.split("weakest bone")[1].split("\n")[0]
    assert "2.5 % (radius" in head
    assert "Coarse stacks compared with the primary: 2 of 4." in rep
    assert "series 6, stack 0: not compared (only 2 slices; at least 8 are needed)." in rep
    assert "Achieved error" not in rep and "Coarse agreement relative to the coarse floor" in rep
    assert "ref -> test mean (mm)" in rep


def test_generic_segmentation_rendering(report):
    sec = report.split("### Thresholds and decisions measured on this scan")[1].split("## Structures")[0]
    assert "- bone thresholds:" in sec and "low hu: 312.5" in sec and "(40 items)" in sec
    assert "1.235, 2" in sec and "timing" not in sec and "flags" not in sec and "nested detail" in sec


def test_validation_embedded(report):
    assert "### Voxel-size floor" in report and "### Agreement between reconstructions" in report
    assert "demo sweep message" in report and "\n# Validation" not in report


def test_no_praise(report, results):
    low = (report + headline(results)).lower()
    for w in FORBIDDEN:
        assert w not in low, w


def test_tables_well_formed(report):
    block = []
    for ln in report.splitlines() + [""]:
        if ln.startswith("|"):
            block.append(ln)
        elif block:
            assert len({b.count("|") for b in block}) == 1, block[0]
            block = []
