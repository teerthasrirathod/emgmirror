import numpy as np
import pytest
import SimpleITK as sitk

from emgmirror.ctseg import ontology
from emgmirror.ctseg.fusion import fuse
from emgmirror.ctseg.runners import ModelResult

from ctseg_helpers import local_id

SHAPE = (6, 6, 6)


def res(source, masks):
    arr = np.zeros(SHAPE, np.uint16)
    for name, m in masks.items():
        arr[m] = local_id(source, name)
    img = sitk.GetImageFromArray(arr)
    return ModelResult(source, img, ontology.classes(source))


def box(z0, z1, y0=0, y1=6, x0=0, x1=6):
    m = np.zeros(SHAPE, bool)
    m[z0:z1, y0:y1, x0:x1] = True
    return m


def arr(f):
    return sitk.GetArrayFromImage(f.labels)


def cid(name):
    return ontology.label(name).id


def test_primary_model_beats_container_whatever_the_order():
    body = res("totalsegmentator:body", {"body_trunc": box(0, 6)})
    total = res("totalsegmentator:total", {"liver": box(2, 4)})
    for results in ([body, total], [total, body]):
        a = arr(fuse(results))
        assert (a[2:4] == cid("liver")).all() and (a[0:2] == cid("body_trunc")).all()


def test_refinement_only_inside_its_parent_and_flagged():
    total = res("totalsegmentator:total", {"liver": box(2, 4), "spleen": box(4, 6)})
    seg = res("totalsegmentator:liver_segments", {"liver_segment_1": box(0, 6)})  # predicted everywhere
    f = fuse([total, seg])
    a = arr(f)
    assert (a[2:4] == cid("liver_segment_1")).all()
    assert (a[4:6] == cid("spleen")).all() and (a[0:2] == 0).all()
    assert f.part_of[cid("liver_segment_1")] == "liver"
    assert f.discarded["totalsegmentator:liver_segments"] == 4 * 36


def test_lower_rank_extra_model_only_fills_gaps():
    total = res("totalsegmentator:total", {"femur_left": box(0, 3)})
    moose = res("moose:clin_ct_peripheral_bones", {"femur_left": box(0, 5), "tibia_left": box(5, 6)})
    f = fuse([moose, total])
    a = arr(f)
    assert (a[0:5] == cid("femur_left")).all() and (a[5] == cid("tibia_left")).all()
    assert f.label_sources[cid("femur_left")] == {"totalsegmentator:total": 108, "moose:clin_ct_peripheral_bones": 72}


def test_equal_rank_first_in_plan_wins():
    a1 = res("totalsegmentator:head_glands_cavities", {"eye_left": box(0, 3)})
    a2 = res("totalsegmentator:headneck_bones_vessels", {"hyoid": box(2, 4)})
    a = arr(fuse([a1, a2]))
    assert (a[2] == cid("eye_left")).all() and (a[3] == cid("hyoid")).all()


def test_refined_vertebrae_may_grow_into_background_but_not_into_organs():
    total = res("totalsegmentator:total", {"vertebrae_L1": box(0, 2), "liver": box(4, 6)})
    ref = res("totalsegmentator:vertebrae_pp_refined", {"vertebrae_L2": box(0, 6)})
    a = arr(fuse([total, ref]))
    assert (a[0:4] == cid("vertebrae_L2")).all() and (a[4:6] == cid("liver")).all()


def test_head_container_does_not_cover_organs():
    total = res("totalsegmentator:total", {"brain": box(2, 4)})
    cf = res("totalsegmentator:craniofacial_structures", {"head": box(0, 6)})
    body = res("totalsegmentator:body", {"body_trunc": box(0, 6)})
    a = arr(fuse([body, cf, total]))
    assert (a[2:4] == cid("brain")).all() and (a[0:2] == cid("head")).all()


def test_grid_mismatch_and_unknown_source_are_errors():
    total = res("totalsegmentator:total", {"liver": box(0, 1)})
    other = ModelResult("totalsegmentator:body", sitk.Image([3, 3, 3], sitk.sitkUInt16),
                        ontology.classes("totalsegmentator:body"))
    with pytest.raises(ValueError, match="grid"):
        fuse([total, other])
    with pytest.raises(KeyError):
        fuse([ModelResult("totalsegmentator:face", total.labels, {})])
