# SPDX-License-Identifier: Apache-2.0
"""Tests for the plain-language CT explainer (pure Python, fast)."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterator

import pytest

from emgmirror.ct import explain
from emgmirror.ct.explain import build_summary, format_cm3, format_mm, glossary, structure_explanations

DISCLAIMER = "Research use, not for diagnosis or surgical planning."


def _bone(name: str, kind: str = "bone", **kw: Any) -> dict[str, Any]:
    d = {"name": name, "kind": kind, "label": name.title(), "volume_cm3": 10.0, "length_mm": 200.0,
         "touches_edge": False, "cortical_fraction": 0.4, "mean_hu": 600.0, "n_pieces": 1, "named_by": "anatomy"}
    d.update(kw)
    return d


def _forearm() -> dict[str, Any]:
    return {
        "scan": {"description": "forearm bone", "n_slices": 300, "voxel_mm": [0.45, 0.45, 0.6],
                 "extent_mm": [230.0, 230.0, 180.0], "kernel": "B70f", "body_part_guess": "forearm",
                 "n_series": 2, "series_reason": "finest bone series"},
        "structures": [
            _bone("radius_cortical", volume_cm3=8.46, length_mm=212.3, touches_edge=True),
            _bone("radius_medullary", volume_cm3=11.2, length_mm=210.0, touches_edge=True),
            _bone("ulna_cortical", volume_cm3=7.1, length_mm=205.0),
            _bone("ulna_medullary", volume_cm3=9.9, length_mm=204.0),
            {"name": "muscle", "kind": "muscle", "label": "Muscle", "volume_cm3": 640.0, "touches_edge": False},
            {"name": "fat", "kind": "fat", "label": "Fat", "volume_cm3": 210.0, "touches_edge": False},
            {"name": "metal", "kind": "metal", "label": "Metal", "volume_cm3": 14.2, "touches_edge": False},
        ],
        "tissue": {"fat_cm3": 210.0, "muscle_cm3": 640.0, "bone_cm3": 36.7, "metal_cm3": 14.2, "fat_pct": 22.0,
                   "muscle_pct": 68.0, "max_muscle_area_cm2": 45.23, "max_girth_mm": 262.4},
        "hardware": {"present": True, "volume_cm3": 14.2, "max_hu": 3071.0, "n_pieces": 3, "length_mm": 96.5,
                     "scale_clipped": False},
        "validation": {"voxel_floor_mm": 0.45, "coarse_msd_mm": 0.31, "coarse_hd95_mm": 0.9, "coarse_dice": 0.973,
                       "threshold_shift_mm_per_100hu": 0.12, "mesh_volume_change_pct": 1.4, "hu_ok": True,
                       "air_hu": -996.0, "fat_hu": -103.0},
        "flags": [],
    }


def _clean() -> dict[str, Any]:
    m = _forearm()
    for s in m["structures"]:
        s["touches_edge"] = False
    return m


# ---------------------------------------------------------------- checkers
def _strings(o: Any) -> Iterator[str]:
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


FORBIDDEN = ["normal", "abnormal", "healthy", "you should", "treatment", "!"]
DIAG_OK = ["not a diagnosis", "for diagnosis or surgical planning"]
BAD_TOKEN = re.compile(r"\b(none|nan|inf|null|undefined)\b", re.I)
UNIT = re.compile(r"(mm|cm³|bones?)")


def check(out: dict[str, Any], flags: list[str] | None = None) -> None:
    json.dumps(out)
    assert out["disclaimer"] == DISCLAIMER
    words = sents = 0
    for s in _strings({k: v for k, v in out.items() if k != "disclaimer"}):
        low = s.lower()
        for f in FORBIDDEN:
            assert f not in low, (f, s)
        stripped = low
        for ok in DIAG_OK:
            stripped = stripped.replace(ok, "")
        assert "diagnos" not in stripped, s
        assert "<" not in s and "**" not in s and "http" not in low, s
        assert not BAD_TOKEN.search(s), s
        assert s == s.strip() and s
        for sent in re.split(r"(?<=[.?])\s+", s):
            n = len(sent.split())
            words += n
            sents += 1
            assert n <= 40, sent
    assert words / max(sents, 1) <= 22
    for t in out["numbers"]:
        assert UNIT.search(t["value"]), t
        assert t["label"] and t["hint"]
    for key in ("title", "headline"):
        assert out[key]


# ---------------------------------------------------------------- tests
def test_full_forearm() -> None:
    out = build_summary(_forearm())
    check(out)
    assert out["title"] == "Your scan: forearm"
    assert 4 <= len(out["numbers"]) <= 8
    assert 5 <= len(out["plain"]) <= 9
    assert 5 <= len(out["limits"]) <= 8
    for sec in out["plain"]:
        n = len(re.split(r"(?<=[.?])\s+", sec["body"]))
        assert 2 <= n <= 7, sec
    assert 10 <= len(out["glossary"]) <= 14
    body = " ".join(s["body"] for s in out["plain"])
    assert "2 bones" in body and "the radius" in body
    assert "at least" in body
    assert any(t["label"] == "Metal hardware" for t in out["numbers"])
    assert any("edge" in w for w in out["warnings"])


def test_whole_bone_plus_inner_part_not_double_counted() -> None:
    """The pipeline emits the whole bone `ulna` and its part `ulna_medullary` (inside it): the bone volume is the
    whole bone's, not the sum."""
    m = _clean()
    m["structures"][:4] = [
        _bone("ulna", volume_cm3=38.85, length_mm=257.9, part=None),
        _bone("ulna_medullary", volume_cm3=17.2, length_mm=250.0, part="medullary"),
        _bone("radius", volume_cm3=35.17, length_mm=234.9, part=None),
        _bone("radius_medullary", volume_cm3=16.95, length_mm=227.0, part="medullary"),
    ]
    del m["tissue"]["bone_cm3"]
    out = build_summary(m)
    check(out)
    body = " ".join(s["body"] for s in out["plain"])
    assert "38.9 cm³" in body and "56.0" not in body and "35.2 cm³" in body
    tile = next(t for t in out["numbers"] if t["label"] == "Bone volume")
    assert tile["value"] == format_cm3(38.85 + 35.17)


def test_pipeline_bone_names_in_plain_words_and_group_not_counted_as_one_bone() -> None:
    m = _clean()
    m["structures"][:4] = [
        _bone("ulna", part=None), _bone("radius", part=None),
        _bone("humerus_distal", part=None, touches_edge=True), _bone("carpals_hand", part=None, volume_cm3=5.0),
    ]
    m["validation"].update({"edge_shift_mm": 0.21, "coarse_weakest": {"name": "carpals_hand", "dice": 0.654}})
    out = build_summary(m)
    check(out)
    text = " ".join(_strings(out))
    assert "3 bones and 1 group of wrist and hand bones" in text
    assert "4 bones" not in text and "carpals hand" not in text and "humerus distal" not in text
    assert "lower humerus" in text
    assert "wrist and hand bones, with an overlap score of 0.65" in text
    assert "0.21 mm" in text and "100 HU" not in text  # the delivered edge, not the intermediate threshold
    assert "skin included" in text
    tile = next(t for t in out["numbers"] if t["label"] == "Muscle and lean tissue")
    assert "skin" in tile["hint"]
    sent = structure_explanations(m)
    assert sent["carpals_hand"].startswith("The wrist and hand bones, the small bones of the wrist and hand")


def test_review_fixes_in_plain_text() -> None:
    m = _clean()
    m["validation"].update({"coarse_volume_diff_pct": -14.0, "mesh_volume_change_pct": 0.76})
    m["flags"] = ["radius is cut by the scan boundary: its length and volume are lower bounds", "other flag"]
    m["structures"][0]["touches_edge"] = True
    out = build_summary(m)
    check(out)
    text = " ".join(_strings(out))
    assert "about 14 percent less bone in total" in text
    assert "Smoothing the 3D surfaces changed their volume" in text and "plain voxel count" not in text
    assert "cut by the scan boundary" not in text and "other flag" in out["warnings"]  # said once, in plain words
    assert "one forearm scan" in text
    m2 = _clean()
    m2["structures"] = [s for s in m2["structures"] if s["kind"] != "bone"]
    tile = next(t for t in build_summary(m2)["numbers"] if t["label"] == "Bones found")
    assert tile["value"] == "0 bones"


def test_empty_and_garbage() -> None:
    out = build_summary({})
    check(out)
    assert out["title"] == "Your scan"
    assert out["warnings"] == []
    assert len(out["plain"]) >= 5
    for bad in (None, [], "x", 5):
        check(build_summary(bad))  # type: ignore[arg-type]


@pytest.mark.parametrize("key", list(_forearm()))
def test_top_level_key_removed(key: str) -> None:
    m = _forearm()
    del m[key]
    check(build_summary(m))
    m = _forearm()
    m[key] = None
    check(build_summary(m))


def _paths(o: Any, path: tuple = ()) -> Iterator[tuple]:
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _paths(v, path + (k,))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _paths(v, path + (i,))
    else:
        yield path


def _edit(m: Any, path: tuple, delete: bool) -> None:
    for p in path[:-1]:
        m = m[p]
    if delete:
        del m[path[-1]]
    else:
        m[path[-1]] = None


@pytest.mark.parametrize("delete", [True, False])
@pytest.mark.parametrize("path", list(_paths(_forearm())), ids=lambda p: ".".join(map(str, p)))
def test_each_leaf_removed_or_none(path: tuple, delete: bool) -> None:
    m = _forearm()
    _edit(m, path, delete)
    check(build_summary(m))
    structure_explanations(m)
    for s in _strings(structure_explanations(m)):
        assert not BAD_TOKEN.search(s)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), "x", True, [], {}])
def test_bad_values_everywhere(bad: Any) -> None:
    for path in _paths(_forearm()):
        m = _forearm()
        node = m
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = bad
        check(build_summary(m))


def test_no_warnings_baseline() -> None:
    assert build_summary(_clean())["warnings"] == []


def _w(m: dict[str, Any]) -> list[str]:
    return build_summary(m)["warnings"]


def test_warning_hu() -> None:
    m = _clean()
    m["validation"]["hu_ok"] = False
    assert len(_w(m)) == 1 and "density scale looks unusual" in _w(m)[0]
    for v in (True, None):
        m["validation"]["hu_ok"] = v
        assert _w(m) == []


def test_warning_body_part() -> None:
    m = _clean()
    m["scan"]["body_part_guess"] = "unknown"
    assert len(_w(m)) == 1 and "body part" in _w(m)[0]
    assert build_summary(m)["title"] == "Your scan"
    m["scan"]["body_part_guess"] = "limb"
    assert _w(m) == [] and build_summary(m)["title"] == "Your scan: limb"


def test_warning_no_bone() -> None:
    m = _clean()
    m["structures"] = [s for s in m["structures"] if s["kind"] != "bone"]
    assert len(_w(m)) == 1 and _w(m)[0].startswith("No bone")
    m["structures"] = []
    assert len(_w(m)) == 1
    del m["structures"]
    assert _w(m) == []


def test_warning_edge() -> None:
    m = _clean()
    m["structures"][0]["touches_edge"] = True
    w = _w(m)
    assert len(w) == 1 and "radius" in w[0] and "edge" in w[0]
    m = _clean()
    m["structures"][4]["touches_edge"] = True
    assert len(_w(m)) == 1


def test_warning_clipped() -> None:
    m = _clean()
    m["hardware"]["scale_clipped"] = True
    assert len(_w(m)) == 1 and "3071" in _w(m)[0]


def test_warning_coarse_voxel() -> None:
    m = _clean()
    m["scan"]["voxel_mm"] = [1.5, 1.5, 1.5]
    assert _w(m) == []
    m["scan"]["voxel_mm"] = [0.4, 0.4, 1.6]
    assert len(_w(m)) == 1 and "coarse" in _w(m)[0]


def test_flags_verbatim_last() -> None:
    m = _clean()
    m["scan"]["body_part_guess"] = "unknown"
    m["flags"] = ["Slices are unevenly spaced.", "Two series differ in size."]
    w = _w(m)
    assert w[-2:] == m["flags"] and len(w) == 3


def test_singular_plural() -> None:
    m = _clean()
    m["structures"] = [_bone("radius_cortical"), m["structures"][4]]
    m["hardware"]["n_pieces"] = 1
    m["scan"]["n_slices"] = 1
    m["scan"]["n_series"] = 2
    out = build_summary(m)
    text = " ".join(_strings(out))
    assert "1 bone" in text and "1 bones" not in text
    assert "1 piece" in text and "1 pieces" not in text
    assert "1 slice" in text and "1 slices" not in text
    out2 = build_summary(_clean())
    assert "2 bones" in " ".join(_strings(out2))
    assert not re.search(r"\b1 (bones|pieces|slices)\b", " ".join(_strings(out2)))


def test_generic_names() -> None:
    m = _clean()
    for s in m["structures"]:
        if s["kind"] == "bone":
            s["named_by"] = "generic"
    m["structures"][0]["name"] = "bone_1_cortical"
    m["structures"][1]["name"] = "bone_1_medullary"
    m["structures"][2]["name"] = "bone_2_cortical"
    m["structures"][3]["name"] = "bone_2_medullary"
    out = build_summary(m)
    check(out)
    body = " ".join(s["body"] for s in out["plain"])
    assert "bone 1 and bone 2" in body and "cannot name" in body
    assert "radius" not in " ".join(_strings(out)).lower().replace("forearm bone", "")
    ex = structure_explanations(m)
    assert ex["bone_1_cortical"].startswith("The dense outer shell of bone 1")


def test_structure_explanations() -> None:
    ex = structure_explanations(_forearm())
    assert set(ex) == {s["name"] for s in _forearm()["structures"]}
    assert "thumb side" in ex["radius_cortical"] and ex["radius_cortical"].startswith("The dense outer shell")
    assert "holds marrow" in ex["ulna_medullary"] and "little-finger" in ex["ulna_medullary"]
    assert "Metal" in ex["metal"] and "Muscle" in ex["muscle"]
    for s in ex.values():
        assert s.endswith(".") and s.count(". ") == 0
    assert structure_explanations({}) == {}
    m = {"structures": [_bone("scaphoid"), _bone("mystery", named_by="generic")]}
    ex = structure_explanations(m)
    assert "scaphoid" in ex["scaphoid"] and "thumb" not in ex["scaphoid"]
    assert "could not name" in ex["mystery"]


def test_formatters() -> None:
    assert format_mm(212.34) == "212 mm"
    assert format_mm(45.67) == "45.7 mm"
    assert format_mm(0.314) == "0.31 mm"
    assert format_mm(float("nan")) == "n/a"
    assert format_cm3(12.345) == "12.3 cm³"
    assert format_cm3(1234.5) == "1230 cm³"
    assert format_cm3(0.01234) == "0.0123 cm³"
    assert format_cm3(9.9996) == "10.0 cm³"
    assert format_cm3(0) == "0 cm³"


def test_comparisons() -> None:
    assert explain._compare(0.1) == "about as thick as a sheet of paper"
    assert explain._compare(0.5) == "about as thick as a mechanical pencil lead"
    assert explain._compare(1.0) == "about as thick as a credit card"
    assert explain._compare(2.0) == "about as thick as a matchstick"
    assert explain._compare(0.3) is None  # between two references, no honest comparison
    assert explain._compare(10) is None


def test_glossary_and_limits() -> None:
    g = glossary()
    assert 10 <= len(g) <= 14
    assert all(set(e) == {"term", "meaning"} and e["term"] and e["meaning"] for e in g)
    assert len({e["term"] for e in g}) == len(g)
    g[0]["term"] = "changed"
    assert glossary()[0]["term"] != "changed"
    limits = " ".join(build_summary({})["limits"]).lower()
    for word in ("not a diagnosis", "fractures", "forearm", "single muscles", "streaks", "scanned region", "your doctor"):
        assert word in limits


def test_input_not_mutated() -> None:
    m = _forearm()
    before = copy.deepcopy(m)
    build_summary(m)
    structure_explanations(m)
    assert m == before


def test_deterministic() -> None:
    assert build_summary(_forearm()) == build_summary(_forearm())
