import pytest

from emgmirror.ctseg import allowlist as A
from emgmirror.ctseg.config import REGION_SUBTASKS, SegConfig


@pytest.mark.parametrize("task", ["total", "total_mr", "body", "lung_vessels", "vertebrae_pp_refined",
                                  "craniofacial_structures", "headneck_bones_vessels", "headneck_muscles",
                                  "head_glands_cavities", "trunk_cavities", "liver_segments"])
def test_open_totalsegmentator_tasks_pass(task):
    assert A.allowed(f"totalsegmentator:{task}").licence == "Apache-2.0"


@pytest.mark.parametrize("key", ["totalsegmentator:appendicular_bones", "totalsegmentator:tissue_types",
                                 "totalsegmentator:face", "totalsegmentator:heartchambers_highres",
                                 "totalsegmentator:brain_aneurysm", "totalsegmentator:coronary_arteries",
                                 "totalsegmentator:thigh_shoulder_muscles", "totalsegmentator:head_muscles",
                                 "nninteractive", "medsam2", "voxtell", "suprem", "nv-segment-ctmr",
                                 "cads:reference", "cads:research", "moose:clin_ct_face", "something:new"])
def test_everything_else_is_refused(key):
    with pytest.raises(A.LicenceError):
        A.allowed(key)


def test_reason_is_given_for_known_non_commercial_models():
    with pytest.raises(A.LicenceError, match="licence key"):
        A.allowed("totalsegmentator:appendicular_bones")


def test_config_check_collects_all_problems():
    with pytest.raises(A.LicenceError) as e:
        A.check_config({"tasks": ["total", "face"], "moose_models": ["clin_ct_peripheral_bones"],
                        "cads": {"license": "reference"}})
    assert "face" in str(e.value) and "cads:reference" in str(e.value)


def test_cads_needs_the_open_licence():
    assert A.check_config({"cads": {"license": "open"}})[0].licence == "CC-BY-SA-4.0"
    with pytest.raises(A.LicenceError):
        A.check_config({"cads": {}})  # the CADS default is 'reference'


def test_a_configured_licence_key_fails():
    with pytest.raises(A.LicenceError, match="licence key"):
        A.check_config({"tasks": ["total"], "license_number": "aca_123"})


def test_default_config_and_every_region_subtask_are_allowlisted():
    cfg = SegConfig()
    A.check_config(cfg.licence_view(sorted({t for ts in REGION_SUBTASKS.values() for t in ts})))


def test_weights_dir_with_licence_key_weights_fails(tmp_path):
    (tmp_path / "Dataset297_TotalSegmentator_total_3mm_1559subj").mkdir()
    assert A.check_weights_dir(tmp_path) == ["Dataset297_TotalSegmentator_total_3mm_1559subj"]
    (tmp_path / "Dataset304_appendicular_bones_ext_1559subj").mkdir()
    with pytest.raises(A.LicenceError, match="Dataset304"):
        A.check_weights_dir(tmp_path)


def test_missing_weights_dir_is_fine(tmp_path):
    assert A.check_weights_dir(tmp_path / "nope") == []
    assert A.check_weights_dir(None) == []


def test_allowlist_matches_installed_totalsegmentator():
    """Cross-check against TotalSegmentator's own licence table and task ids (skipped if not installed)."""
    pytest.importorskip("totalsegmentator")
    from totalsegmentator.map_tasks_config import TASK_CONFIGS
    from totalsegmentator.map_to_binary import commercial_models
    for key, a in A.ALLOWED.items():
        if a.source != "totalsegmentator":
            continue
        assert a.task not in commercial_models, a.task
        cfg = TASK_CONFIGS[a.task]
        ids = set()
        for c in (cfg["sub_modes"].values() if "sub_modes" in cfg else [cfg]):
            tid = c["task_id"]
            ids |= set(tid if isinstance(tid, list) else [tid])
            assert not c.get("commercial")
        assert ids <= set(a.weight_ids), (a.task, ids)
    assert not set(commercial_models.values()) & A.allowed_weight_ids() - {778}  # 778 is MOOSE body comp. too
