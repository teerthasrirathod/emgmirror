"""Regenerate the vendored resources of :mod:`emgmirror.ctseg` (run by a maintainer, not at runtime).

Inputs (none of them is downloaded by this script; fetch them yourself, see docs/ct_platform/S1_segmentation.md):

* an installed TotalSegmentator (for the class maps of the allowlisted tasks),
* the ``dataset.json`` of each allowlisted MOOSE model (inside the MOOSE weight zips),
* SlicerTotalSegmentator's ``totalsegmentator_snomed_mapping.csv`` and
  ``SegmentationCategoryTypeModifier-TotalSegmentator.term.json`` (Apache-2.0).

Outputs, in ``src/emgmirror/ctseg/resources/``:

* ``task_classes.json``: {source: {local_id: name}} for every allowlisted model,
* ``label_table.json``: canonical label table (stable ids, display name, colour, SNOMED codes).

Ids already present in ``label_table.json`` are never changed; new names are appended.

    python scripts/ctseg_build_resources.py --slicer-dir DIR --moose-dir DIR
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

RES = Path(__file__).resolve().parents[1] / "src" / "emgmirror" / "ctseg" / "resources"

TS_TASKS = ["total", "total_mr", "body", "body_mr", "lung_vessels", "vertebrae_pp_refined", "craniofacial_structures",
            "headneck_bones_vessels", "headneck_muscles", "head_glands_cavities", "trunk_cavities", "liver_segments"]
MOOSE_MODELS = {"clin_ct_peripheral_bones": "Dataset666_Peripheral-Bones",
                "clin_ct_body_composition": "Dataset778_Body_composition"}
# MOOSE names that differ from the TotalSegmentator spelling of the same structure.
MOOSE_RENAME = {"clavicle_left": "clavicula_left", "clavicle_right": "clavicula_right",
                "fingers_left": "phalanges_hand_left", "fingers_right": "phalanges_hand_right",
                "toes_left": "phalanges_feet_left", "toes_right": "phalanges_feet_right"}
SIDE = {"left": {"CodingSchemeDesignator": "SCT", "CodeValue": "7771000", "CodeMeaning": "Left"},
        "right": {"CodingSchemeDesignator": "SCT", "CodeValue": "24028007", "CodeMeaning": "Right"}}
FALLBACK_RGB = {"bone": [241, 214, 145], "muscle": [192, 104, 88], "fat": [230, 220, 70], "vessel": [200, 60, 60],
                "air": [180, 220, 240], "heart": [206, 110, 84], "other": [190, 150, 140]}


def _fallback_rgb(name: str) -> list[int]:
    """Class base colour, shifted per name by a stable hash so neighbouring structures differ."""
    import hashlib
    base = FALLBACK_RGB[_kind(name)]
    h = hashlib.sha256(name.encode()).digest()
    return [max(0, min(255, c + (h[i] % 41) - 20)) for i, c in enumerate(base)]


def _code(r: dict, prefix: str) -> dict | None:
    if not r.get(f"{prefix}_CodeValue"):
        return None
    return {"CodingSchemeDesignator": r[f"{prefix}_CodingScheme"], "CodeValue": r[f"{prefix}_CodeValue"],
            "CodeMeaning": r[f"{prefix}_CodeMeaning"]}


def _term_colours(term: dict) -> dict:
    out = {}
    for cat in term["SegmentationCodes"]["Category"]:
        for t in cat.get("Type", []):
            if "recommendedDisplayRGBValue" in t:
                out[(t["CodeValue"], None)] = t["recommendedDisplayRGBValue"]
            for m in t.get("Modifier", []):
                if "recommendedDisplayRGBValue" in m:
                    out[(t["CodeValue"], m["CodeValue"])] = m["recommendedDisplayRGBValue"]
                    out.setdefault((t["CodeValue"], None), m["recommendedDisplayRGBValue"])
    return out


def _kind(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("artery", "vein", "vena", "aorta", "trunk_brachio", "appendage", "sinus_sag")):
        return "vessel"
    if n.startswith("heart") or n == "pericardium":
        return "heart"
    if "fat" in n:
        return "fat"
    if any(k in n for k in ("muscle", "gluteus", "autochthon", "iliopsoas", "scalene", "trapezius", "masseter")):
        return "muscle"
    if any(k in n for k in ("airway", "larynx_air", "sinus_", "nasal", "pharynx")):
        return "air"
    bones = ("vertebra", "rib_", "sternum", "skull", "humerus", "scapula", "clavicul", "femur", "hip_", "sacrum",
             "mandible", "radius", "ulna", "carpal", "metacarpal", "phalanges", "tibia", "fibula", "patella",
             "tarsal", "metatarsal", "hyoid", "zygomatic", "styloid", "teeth")
    return "bone" if any(b in n for b in bones) else "other"


def _display(name: str) -> str:
    words = name.replace("_", " ").split()
    side = ""
    if words and words[-1] in ("left", "right"):
        side = words.pop()
    s = " ".join(words)
    s = s[:1].upper() + s[1:]
    return f"{s} ({side})" if side else s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slicer-dir", type=Path, required=True)
    ap.add_argument("--moose-dir", type=Path, required=True, help="folder holding the unzipped MOOSE models")
    a = ap.parse_args()

    from totalsegmentator.map_to_binary import class_map  # maintainer-only dependency

    tasks: dict[str, dict[str, str]] = {}
    for t in TS_TASKS:
        tasks[f"totalsegmentator:{t}"] = {str(k): v for k, v in sorted(class_map[t].items())}
    for model, folder in MOOSE_MODELS.items():
        ds = next(a.moose_dir.rglob(f"{folder}/*/dataset.json"))
        labels = json.loads(ds.read_text())["labels"]
        tasks[f"moose:{model}"] = {str(int(v)): MOOSE_RENAME.get(k, k)
                                   for k, v in sorted(labels.items(), key=lambda kv: int(kv[1])) if int(v) > 0}

    rows = {r["Name"]: r for r in csv.DictReader(open(a.slicer_dir / "totalsegmentator_snomed_mapping.csv"))}
    colours = _term_colours(json.loads((a.slicer_dir / "SegmentationCategoryTypeModifier-TotalSegmentator.term.json")
                                       .read_text()))

    old = {}
    tpath = RES / "label_table.json"
    if tpath.exists():
        old = {e["name"]: e["id"] for e in json.loads(tpath.read_text())["labels"]}
    ids = dict(old)
    # TotalSegmentator `total` keeps its own ids 1..117; everything else is appended from 200 upwards.
    for k, v in tasks["totalsegmentator:total"].items():
        ids.setdefault(v, int(k))
    nxt = max([199] + [i for i in ids.values() if i >= 200]) + 1
    for src, cmap in tasks.items():
        for v in cmap.values():
            if v not in ids:
                ids[v] = nxt
                nxt += 1

    entries = []
    for name, i in sorted(ids.items(), key=lambda kv: kv[1]):
        r = rows.get(name)
        side = None
        if r is None:
            base, _, s = name.rpartition("_")
            if s in SIDE and base in rows:
                r, side = rows[base], s
        snomed = None
        rgb = None
        if r is not None:
            cat, typ = _code(r, "Category"), _code(r, "Type")
            mod = _code(r, "TypeModifier") or (SIDE[side] if side else None)
            snomed = {"category": cat, "type": typ}
            if mod:
                snomed["modifier"] = mod
            region = _code(r, "Region")
            if region:
                snomed["region"] = region
            if r.get("Color_R"):
                rgb = [int(r["Color_R"]), int(r["Color_G"]), int(r["Color_B"])]
            elif typ:
                rgb = colours.get((typ["CodeValue"], mod["CodeValue"] if mod else None)) or \
                    colours.get((typ["CodeValue"], None))
        entries.append({"id": i, "name": name, "display": _display(name),
                        "rgb": [int(c) for c in rgb] if rgb else _fallback_rgb(name),
                        "rgb_source": "slicer" if rgb else "fallback", "kind": _kind(name), "snomed": snomed})

    RES.mkdir(parents=True, exist_ok=True)
    (RES / "task_classes.json").write_text(json.dumps(tasks, indent=1) + "\n")
    tpath.write_text(json.dumps({
        "about": "Canonical label table for emgmirror.ctseg. Names follow TotalSegmentator. Colours and SNOMED CT "
                 "codes from SlicerTotalSegmentator (Apache-2.0, see NOTICE). Ids are stable: never renumber.",
        "labels": entries}, indent=1) + "\n")
    print(f"{len(entries)} labels, {sum(e['snomed'] is None for e in entries)} without SNOMED, "
          f"{sum(e['rgb_source'] == 'fallback' for e in entries)} with fallback colour")


if __name__ == "__main__":
    main()
