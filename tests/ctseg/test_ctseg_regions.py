import numpy as np

from emgmirror.ctseg.config import SegConfig
from emgmirror.ctseg.allowlist import allowed
from emgmirror.ctseg.ontology import task_classes, table
from emgmirror.ctseg.regions import _markers, _planning_catalog, detect, make_plan, volumes_ml

BASE = "totalsegmentator:total"


def test_volumes_from_label_map():
    a = np.zeros((2, 2, 2), np.uint8)
    a[0] = 5
    assert volumes_ml(a, {5: "liver", 1: "spleen"}, 2.0) == {"liver": 8.0}


def test_chest_scan_runs_chest_subtasks_only():
    vols = {"lung_upper_lobe_left": 900, "heart": 600, "vertebrae_T5": 30}
    p = make_plan(BASE, vols, SegConfig(), "CT")
    assert set(p.regions) == {"thorax", "spine"}
    assert p.subtasks == ["totalsegmentator:lung_vessels", "totalsegmentator:trunk_cavities",
                          "totalsegmentator:vertebrae_pp_refined"]
    assert p.moose == []


def test_liver_segments_need_a_liver():
    p = make_plan(BASE, {"spleen": 200}, SegConfig(), "CT")
    assert "abdomen" in p.regions and "totalsegmentator:liver_segments" not in p.subtasks
    p = make_plan(BASE, {"liver": 1500}, SegConfig(), "CT")
    assert "totalsegmentator:liver_segments" in p.subtasks


def test_limbs_and_lumbar_trigger_moose():
    p = make_plan(BASE, {"femur_left": 400, "vertebrae_L3": 40}, SegConfig(), "CT")
    assert p.moose == ["moose:clin_ct_peripheral_bones", "moose:clin_ct_body_composition"]
    p = make_plan(BASE, {"body_extremities": 300}, SegConfig(), "CT")  # forearm only: no humerus in view
    assert p.moose == ["moose:clin_ct_peripheral_bones"]
    assert make_plan(BASE, {"femur_left": 400}, SegConfig(moose=False), "CT").moose == []


def test_tiny_blobs_do_not_count_as_a_region():
    assert detect({"brain": 1.0, "liver": 4.9}, SegConfig()) == {}


def test_planning_catalog_references_real_model_capabilities_and_anatomy():
    catalog = _planning_catalog()
    providers = task_classes()
    anatomies = set(table())
    for source in catalog["tasks"]:
        assert source in providers, f"{source} has no model-to-anatomy capability map"
        allowed(source, "research")
    for region, patterns in _markers().items():
        assert patterns, region
        assert any(pattern.match(name) for pattern in patterns for name in anatomies), region


def test_minimal_profile_mr_and_explicit_tasks():
    vols = {"liver": 1500, "femur_left": 400}
    assert make_plan(BASE, vols, SegConfig(profile="minimal"), "CT").sources == []
    p = make_plan("totalsegmentator:total_mr", vols, SegConfig(), "MR")
    assert p.sources == []
    p = make_plan(BASE, vols, SegConfig(tasks=["trunk_cavities"], moose=False), "CT")
    assert p.sources == ["totalsegmentator:trunk_cavities"]
