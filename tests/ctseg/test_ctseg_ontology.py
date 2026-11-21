import re

from emgmirror.ctseg import ontology


def test_total_keeps_its_own_ids():
    for lid, name in ontology.classes("totalsegmentator:total").items():
        assert ontology.label(name).id == lid


def test_every_model_class_is_in_the_table_and_ids_are_unique():
    tab = ontology.table()
    assert len({lab.id for lab in tab.values()}) == len(tab)
    for src, cmap in ontology.task_classes().items():
        for name in cmap.values():
            assert name in tab, (src, name)
    assert max(lab.id for lab in tab.values()) < 65536


def test_names_are_snake_case_and_colours_valid():
    for lab in ontology.table().values():
        assert re.fullmatch(r"[a-z0-9_]+?(_[CTLS]\d+)?", lab.name), lab.name  # TotalSegmentator: vertebrae_C1
        assert len(lab.rgb) == 3 and all(0 <= c <= 255 for c in lab.rgb)
        assert lab.display


def test_snomed_codes_present_with_laterality():
    missing = [lab.name for lab in ontology.table().values() if lab.snomed is None]
    assert missing == ["visceral_fat"]  # no SNOMED row in SlicerTotalSegmentator; documented
    r = ontology.label("radius_left").snomed
    assert r["type"]["CodeMeaning"] == "Radius" and r["modifier"]["CodeMeaning"] == "Left"
    k = ontology.label("kidney_right").snomed
    assert k["category"]["CodeValue"] == "123037004" and k["modifier"]["CodeMeaning"] == "Right"


def test_moose_names_follow_totalsegmentator_spelling():
    names = set(ontology.classes("moose:clin_ct_peripheral_bones").values())
    assert {"clavicula_left", "phalanges_hand_right", "tibia_left", "ulna_right"} <= names
    assert not any(n.startswith(("clavicle", "fingers", "toes")) for n in names)
