# SPDX-License-Identifier: Apache-2.0
"""Plain-language explanation of CT measurements for a lay reader.

Everything here is deterministic: text comes from fixed templates filled with numbers. Nothing is
looked up, generated or guessed at run time. Missing or invalid inputs simply leave a sentence or
tile out. Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import math
from typing import Any

from . import DISCLAIMER

__all__ = ["build_summary", "structure_explanations", "glossary", "format_mm", "format_cm3"]

CM3 = "cm³"
CM2 = "cm²"
COARSE_VOXEL_MM = 1.5

_KNOWN_BONES = {
    "radius": "the forearm bone on the thumb side",
    "ulna": "the forearm bone on the little-finger side",
    "humerus": "the long bone of the upper arm",
    "carpals": "the small bones of the wrist",
    "carpal": "one of the small bones of the wrist",
    "metacarpals": "the bones of the palm",
    "humerus_distal": "the lower end of the upper-arm bone, near the elbow",
    "carpals_hand": "the small bones of the wrist and hand, shown together because they touch",
}
# Plain names for the pipeline's bone names. A group holds several bones that the program cannot pull apart.
_DISPLAY = {"humerus_distal": "lower humerus", "carpals_hand": "wrist and hand bones"}
_GROUPS = {"carpals_hand", "carpals", "metacarpals"}

_GLOSSARY = [
    ("CT scan", "A stack of X-ray pictures taken from many angles and combined by a computer into slices."),
    ("Slice", "One thin picture through the body. A CT scan is many slices stacked together."),
    ("Voxel", "A tiny cube that is the smallest piece of a CT scan. Each voxel has one density value."),
    ("Hounsfield units (HU)", "The scanner's density scale. Air is about -1000, water is 0 and dense bone is far higher."),
    ("Cortical bone", "The hard, dense outer shell of a bone."),
    ("Medullary bone", "The softer inner part of a bone, where marrow sits."),
    ("Segmentation", "Sorting every voxel into a group, such as bone, muscle, fat, metal or background."),
    ("3D surface (mesh)", "A skin of tiny triangles wrapped around a group of voxels to show its shape in 3D."),
    ("Hardware", "Metal objects placed in the body, such as plates, screws and rods."),
    ("Metal streaks", "Bright and dark lines in a CT image caused by metal. They can blur the bone next to the metal."),
    ("Lean soft tissue", "Muscle and other soft tissue that is not fat. CT density alone cannot separate single muscles."),
    ("Average surface difference", "How far apart two versions of the same surface are, on average, in millimetres."),
    ("Overlap score", "A number from 0 to 1 that says how much two shapes cover the same space. 1 means identical."),
    ("Kernel", "A setting of the scanner that makes the picture sharper or smoother."),
]

_LIMITS = [
    "This tool is for research. It is not a diagnosis, and it does not replace a clinician reading your scan.",
    "It cannot show fractures, healing, infection, tumours, or nerve and tendon injury.",
    "The program was tuned on one forearm scan and has been tested less on other scans and body parts.",
    "Single muscles are not separated, because CT density alone cannot tell them apart.",
    "Metal causes bright streaks that can distort the bone next to it, so numbers near metal are less certain.",
    "The numbers describe only the scanned region, not the whole body or the whole bone.",
    "No human expert has checked this result, and the program can label some tissue wrongly.",
    "Ask your doctor what your scan means for you.",
]


# ---------------------------------------------------------------- small helpers
def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    f = float(x)
    return f if math.isfinite(f) else None


def _d(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _text(x: Any) -> str:
    return x.strip() if isinstance(x, str) else ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _n(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _sig3(v: float) -> str:
    r = float(f"{v:.3g}")
    if r == 0:
        return "0"
    decimals = max(0, 2 - math.floor(math.log10(abs(r))))
    return f"{r:.{decimals}f}"


def _pct(v: float) -> str:
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"


def format_mm(x: float) -> str:
    """Millimetres: 0 decimals from 100 mm up, 1 decimal from 1 mm, 2 decimals below."""
    v = _num(x)
    if v is None:
        return "n/a"
    a = abs(v)
    s = f"{v:.0f}" if a >= 100 else f"{v:.1f}" if a >= 1 else f"{v:.2f}"
    return f"{s} mm"


def format_cm3(x: float) -> str:
    """Cubic centimetres to 3 significant figures."""
    v = _num(x)
    return "n/a" if v is None else f"{_sig3(v)} {CM3}"


def _vec3(x: Any) -> list[float] | None:
    if not isinstance(x, (list, tuple)) or len(x) != 3:
        return None
    vals = [_num(v) for v in x]
    return None if any(v is None for v in vals) else [float(v) for v in vals if v is not None]


def glossary() -> list[dict[str, str]]:
    return [{"term": t, "meaning": m} for t, m in _GLOSSARY]


def _compare(mm: float) -> str | None:
    refs = [(0.1, "a sheet of paper"), (0.5, "a mechanical pencil lead"), (1.0, "a credit card"), (2.0, "a matchstick")]
    if mm <= 0:
        return None
    if mm < 0.067:
        return "less than a sheet of paper"
    ref = min(refs, key=lambda r: abs(math.log(mm / r[0])))
    return f"about as thick as {ref[1]}" if 1 / 1.5 <= mm / ref[0] <= 1.5 else None


# ---------------------------------------------------------------- structures
def _split_part(name: str) -> tuple[str, str]:
    for suffix in ("_cortical", "_medullary"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], suffix[1:]
    return name, ""


def _structs(m: dict[str, Any]) -> list[dict[str, Any]] | None:
    s = m.get("structures")
    return [x for x in s if isinstance(x, dict)] if isinstance(s, list) else None


def _bones(structs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the meshes of one bone into one record.

    A bone comes either as the whole bone `X` plus its part `X_medullary` (the pipeline's layout: the part lies
    inside the whole, so its volume is already counted), or as the two parts `X_cortical` + `X_medullary`, which are
    added. The whole bone's volume wins when it is present."""
    groups: dict[str, dict[str, Any]] = {}
    for s in structs:
        name = _text(s.get("name"))
        if s.get("kind") != "bone" or not name:
            continue
        base, suffix = _split_part(name)
        b = groups.setdefault(base, {"base": base, "name": _DISPLAY.get(base, base.replace("_", " ")), "volume": None,
                                     "length": None, "edge": False, "anatomy": False, "whole": None,
                                     "group": base in _GROUPS})
        v = _num(s.get("volume_cm3"))
        if v is not None:
            if not suffix and not s.get("part"):
                b["whole"] = v
            else:
                b["volume"] = (b["volume"] or 0.0) + v
        length = _num(s.get("length_mm"))
        if length is not None:
            b["length"] = max(b["length"] or 0.0, length)
        b["edge"] = b["edge"] or s.get("touches_edge") is True
        b["anatomy"] = b["anatomy"] or s.get("named_by") == "anatomy"
    for b in groups.values():
        whole = b.pop("whole")
        if whole is not None:
            b["volume"] = whole
    return list(groups.values())


def _ref(b: dict[str, Any]) -> str:
    return f"the {b['name']}" if b["anatomy"] else b["name"]


def _count_bones(bones: list[dict[str, Any]]) -> str:
    """'3 bones', or '3 bones and 1 group of wrist and hand bones' when some entries hold several bones."""
    single = [b for b in bones if not (b.get("group") and b["anatomy"])]
    grouped = [b for b in bones if b.get("group") and b["anatomy"]]
    parts = [_n(len(single), "bone")] if single or not grouped else []
    parts += [f"1 group of {b['name']}" for b in grouped]
    return _join(parts)


def _kind_volume(structs: list[dict[str, Any]] | None, kind: str) -> float | None:
    if kind == "bone":  # parts lie inside their whole bone: count each bone once
        vols = [b["volume"] for b in _bones(structs or [])]
        vols = [v for v in vols if v is not None]
        return sum(vols) if vols else None
    vols = [_num(s.get("volume_cm3")) for s in structs or [] if s.get("kind") == kind]
    vols = [v for v in vols if v is not None]
    return sum(vols) if vols else None


def _volume(m: dict[str, Any], key: str, kind: str) -> float | None:
    v = _num(_d(m.get("tissue")).get(key))
    return v if v is not None else _kind_volume(_structs(m), kind)


def _edge_names(structs: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for s in structs:
        if s.get("touches_edge") is not True:
            continue
        raw = _text(s.get("name"))
        if s.get("kind") == "bone" and raw:
            base = _split_part(raw)[0]
            base = _DISPLAY.get(base, base.replace("_", " "))
            label = f"the {base}" if s.get("named_by") == "anatomy" else base
        else:
            label = _text(s.get("label")) or raw.replace("_", " ")
            label = label.lower() if label else "part of the model"
        if label not in names:
            names.append(label)
    return names


# ---------------------------------------------------------------- per-structure sentences
def structure_explanations(m: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in _structs(_d(m)) or []:
        name = _text(s.get("name"))
        if not name:
            continue
        kind = s.get("kind")
        vol = _num(s.get("volume_cm3"))
        edge = s.get("touches_edge") is True
        if kind == "bone":
            base, part = _split_part(name)
            display = _DISPLAY.get(base, base.replace("_", " "))
            anatomy = s.get("named_by") == "anatomy"
            known = _KNOWN_BONES.get(base.lower()) if anatomy else None
            if anatomy:
                what = f"the {display}, {known}" if known else f"the {display}"
            else:
                what = f"{display}, a bone the program could not name"
            lead = {"cortical": f"The dense outer shell of {what}",
                    "medullary": f"The softer inner part of {what} that holds marrow"}.get(part, _cap(what))
        else:
            lead = {
                "metal": "Metal in the scan, often a plate, screw or rod from surgery, with bright streaks around it",
                "fat": "Fat inside the outline of the skin",
                "muscle": "Muscle and other lean soft tissue, skin included, drawn as one block because CT density "
                          "cannot separate single muscles",
                "skin": "The outer surface of the scanned body part",
            }.get(str(kind), "A part of the scan marked by the program")
        extra = [f"about {format_cm3(vol)}"] if vol is not None else []
        if edge:
            extra.append("cut off by the edge of the scan")
        out[name] = lead + (f" ({'; '.join(extra)})" if extra else "") + "."
    return out


# ---------------------------------------------------------------- warnings
def _warnings(m: dict[str, Any]) -> list[str]:
    w: list[str] = []
    scan, val, hw = _d(m.get("scan")), _d(m.get("validation")), _d(m.get("hardware"))
    structs = _structs(m)
    if val.get("hu_ok") is False:
        w.append("The scanner's density scale looks unusual for this scan, so the tissue labels may be wrong.")
    if scan.get("body_part_guess") == "unknown":
        w.append("The program could not tell which body part this is. It is tuned on forearms, "
                 "so results may be less reliable.")
    if structs is not None and not _bones(structs):
        w.append("No bone was found. If the scan contains bone, the program may have missed it.")
    edge = _edge_names(structs or [])
    if edge:
        who = _join(edge) if len(edge) <= 4 else f"{len(edge)} parts of the model"
        w.append(f"The scan cuts off {who} at its edge, so the real size is larger than the number shown.")
    if hw.get("scale_clipped") is True:
        w.append("The scan's density scale stops at 3071, so metal and very dense bone cannot be told apart reliably.")
    vox = _vec3(scan.get("voxel_mm"))
    if vox and max(vox) > COARSE_VOXEL_MM:
        w.append(f"This is a coarse scan, with voxels larger than {COARSE_VOXEL_MM} mm in at least one direction. "
                 "Fine details are not reliable.")
    # Pipeline flags come last, word for word, except the scan-edge ones: the plain sentence above already says that.
    w.extend(f.strip() for f in (m.get("flags") if isinstance(m.get("flags"), list) else [])
             if isinstance(f, str) and f.strip() and not (edge and "cut by the scan boundary" in f))
    return w


# ---------------------------------------------------------------- tiles
def _tiles(m: dict[str, Any]) -> list[dict[str, str]]:
    scan, val, hw = _d(m.get("scan")), _d(m.get("validation")), _d(m.get("hardware"))
    structs = _structs(m)
    tiles: list[dict[str, str]] = []

    def add(label: str, value: str, hint: str) -> None:
        tiles.append({"label": label, "value": value, "hint": hint})

    vox = _vec3(scan.get("voxel_mm"))
    if vox:
        parts = [f"{v:.2f}" for v in vox]
        add("Scan detail", (parts[0] if len(set(parts)) == 1 else " x ".join(parts)) + " mm",
            "Size of one voxel, the smallest detail shown")
    ext = _vec3(scan.get("extent_mm"))
    if ext:
        add("Scanned length", format_mm(max(ext)), "Longest side of the scanned region")
    bones = _bones(structs) if structs is not None else None
    if bones is not None:
        add("Bones found", _count_bones(bones), "Separate bones the program marked; a group holds several bones")
    bone_v = _volume(m, "bone_cm3", "bone")
    if bone_v is not None:
        add("Bone volume", format_cm3(bone_v), "Space taken by all bone found")
    muscle_v = _volume(m, "muscle_cm3", "muscle")
    if muscle_v is not None:
        add("Muscle and lean tissue", format_cm3(muscle_v), "Muscle, skin and other lean soft tissue in the scan")
    fat_v = _volume(m, "fat_cm3", "fat")
    if fat_v is not None:
        add("Fat volume", format_cm3(fat_v), "Fat inside the skin outline")
    hv = _num(hw.get("volume_cm3"))
    if hw.get("present") is True and hv is not None:
        add("Metal hardware", format_cm3(hv), "Metal marked in the scan")
    msd = _num(val.get("coarse_msd_mm"))
    if msd is not None:
        add("Average surface difference", format_mm(msd), "Gap between fine and coarse versions of the bones")
    return tiles


# ---------------------------------------------------------------- plain sections
def _sec(title: str, sentences: list[str]) -> dict[str, str]:
    return {"title": title, "body": " ".join(s for s in sentences if s)}


def _scan_sections(m: dict[str, Any]) -> list[dict[str, str]]:
    scan, val = _d(m.get("scan")), _d(m.get("validation"))
    n_slices = _num(scan.get("n_slices"))
    s_slices = f"This scan has {_n(int(n_slices), 'slice')}." if n_slices and n_slices >= 1 else ""
    secs = [
        _sec("What a CT scan is", [
            "A CT scan is a stack of X-ray pictures.",
            "Each picture is a thin slice through the body, a bit like one slice of bread.",
            "The scanner measures how strongly each spot blocks X-rays, which depends on how dense the spot is.",
            s_slices]),
    ]
    air, fat = _num(val.get("air_hu")), _num(val.get("fat_hu"))
    read = [f"{name} reads about {v:.0f}" for name, v in (("air", air), ("fat", fat)) if v is not None]
    secs.append(_sec("What the grey values mean", [
        "Each spot gets a number on the Hounsfield scale, the scanner's density scale. Its unit is HU.",
        "Air is about -1000 HU and water is 0.",
        "Fat is about -100, muscle is about +50, bone runs from about +300 to about +2000 in its densest parts, "
        "and metal is far higher.",
        f"In this scan, {_join(read)}." if read else ""]))
    secs.append(_sec("How the 3D model was made", [
        "The computer looks at every tiny cube of the scan, called a voxel.",
        "It marks each voxel as bone, muscle, fat, metal or background, using its density and its shape.",
        "Then it wraps a surface around each group of matching voxels.",
        "Those surfaces are the 3D model you can turn in the viewer."]))
    return secs


def _bone_section(bones: list[dict[str, Any]]) -> dict[str, str]:
    if not bones:
        return _sec("Bones", ["No bone was marked in this scan.",
                              "The program may have missed bone, for example if the density scale looks unusual."])
    n = len(bones)
    found = f"The program found {_count_bones(bones)}"
    s1 = f"{found}: {_join([_ref(b) for b in bones])}." if n <= 4 else f"{found}."
    sized = sorted((b for b in bones if b["volume"] is not None or b["length"] is not None),
                   key=lambda b: -(b["volume"] or 0.0))[:2]
    detail = []
    for b in sized:
        bits = []
        if b["volume"] is not None:
            bits.append(f"a volume of {format_cm3(b['volume'])}")
        if b["length"] is not None:
            bits.append(f"is {'at least ' if b['edge'] else ''}{format_mm(b['length'])} long")
        detail.append(f"{_cap(_ref(b))} has {bits[0]}" + (f" and {bits[1]}." if len(bits) == 2 else ".")
                      if b["volume"] is not None else f"{_cap(_ref(b))} {bits[0]}.")
    cut = [b for b in bones if b["edge"]]
    s_cut = ""
    if cut:
        names = _join([_ref(b) for b in cut]) if len(cut) <= 3 else _n(len(cut), "bone")
        s_cut = (f"The scan cuts {names} off at the edge, so "
                 + ("its real length is longer." if len(cut) == 1 else "their real lengths are longer."))
    s_gen = ("The program numbers bones it cannot name, so a number such as bone 1 says nothing about which bone it is."
             if any(not b["anatomy"] for b in bones) else "")
    return _sec("Bones found", [s1, *detail, s_cut, s_gen])


def _metal_section(hw: dict[str, Any]) -> dict[str, str]:
    pieces, vol, length = _num(hw.get("n_pieces")), _num(hw.get("volume_cm3")), _num(hw.get("length_mm"))
    s1 = "The program also found metal"
    if pieces and pieces >= 1:
        s1 += f" in {_n(int(pieces), 'piece')}"
    if vol is not None:
        s1 += f", with a total volume of {format_cm3(vol)}"
    if length is not None:
        s1 += f" and a length of about {format_mm(length)}"
    s_clip = ("The scan's density scale stops at 3071, so metal and very dense bone cannot be told apart reliably."
              if hw.get("scale_clipped") is True else "")
    return _sec("Metal hardware", [
        s1 + ".",
        "Metal in a scan is often a plate, screw or rod placed during surgery, but the program cannot tell what it is.",
        "Metal causes bright streaks in the image, and these can blur the bone next to it.",
        s_clip])


def _tissue_section(m: dict[str, Any]) -> dict[str, str] | None:
    tis = _d(m.get("tissue"))
    mus, fat = _volume(m, "muscle_cm3", "muscle"), _volume(m, "fat_cm3", "fat")
    if mus is None and fat is None:
        return None
    marked = [f"{format_cm3(v)} as {name}" for name, v in (("muscle and other lean tissue", mus), ("fat", fat))
              if v is not None]
    pcts = [f"{name} makes up about {_pct(p)} percent" for name, p in
            (("lean tissue", _num(tis.get("muscle_pct"))), ("fat", _num(tis.get("fat_pct")))) if p is not None]
    area, girth = _num(tis.get("max_muscle_area_cm2")), _num(tis.get("max_girth_mm"))
    s_pct = f"By volume, {_join(pcts)} of the scanned tissue." if pcts else ""
    s_area = f"The biggest muscle area on a single slice is {_sig3(area)} {CM2}." if area is not None else ""
    s_girth = f"The longest distance around the scanned part is about {format_mm(girth)}." if girth is not None else ""
    return _sec("Muscle and fat", [
        f"The program marked {_join(marked)}.", s_pct, s_area, s_girth,
        "The muscle label covers muscle and other lean soft tissue as one block, skin included, "
        "because single muscles cannot be told apart by CT density alone.",
        "These amounts cover only the scanned part of the body, not the whole body."])


def _detail_section(m: dict[str, Any]) -> dict[str, str]:
    scan, val = _d(m.get("scan")), _d(m.get("validation"))
    vox = _vec3(scan.get("voxel_mm"))
    if vox:
        parts = [f"{v:.2f}" for v in vox]
        size = f"{parts[0]} mm on each side" if len(set(parts)) == 1 else f"{' x '.join(parts)} mm"
        s1 = f"The smallest detail this scan can show is one voxel, a tiny cube about {size}."
    else:
        s1 = "The smallest detail a scan can show is one voxel, a tiny cube whose size the scanner sets."
    floor = _num(val.get("voxel_floor_mm"))
    s_floor = f"The limit set by voxel size is about {format_mm(floor)}." if floor is not None else ""
    kernel = _text(scan.get("kernel"))
    s_kernel = (f"The scan was rebuilt with the {kernel} kernel, a scanner setting for sharp or smooth pictures."
                if kernel else "")
    series = _num(scan.get("n_series"))
    reason = _text(scan.get("series_reason"))
    s_series = ""
    if series and series > 1:
        s_series = f"The scan files held {int(series)} image series, and the program picked one" + (
            f": {reason.rstrip('.')}." if reason else ".")
    return _sec("How fine the scan is", [s1, s_floor, s_kernel, s_series, "No human expert has checked this result."])


def _checks_section(val: dict[str, Any]) -> dict[str, str] | None:
    msd, hd95, dice = (_num(val.get(k)) for k in ("coarse_msd_mm", "coarse_hd95_mm", "coarse_dice"))
    shift, vchange = _num(val.get("edge_shift_mm")), _num(val.get("mesh_volume_change_pct"))
    weak = _d(val.get("coarse_weakest"))
    weak_dice, weak_name = _num(weak.get("dice")), _text(weak.get("name"))
    if all(v is None for v in (msd, hd95, dice, shift, vchange)):
        return None
    s: list[str] = []
    if msd is not None:
        comp = _compare(msd)
        s.append("When the same bones were rebuilt from a coarser version of the scan, the two surfaces differed by "
                 f"about {format_mm(msd)} on average" + (f", {comp}." if comp else "."))
    if hd95 is not None:
        s.append(f"For 95 percent of the surface, the gap was {format_mm(hd95)} or less.")
    if dice is not None:
        s.append(f"The overlap score of the two versions was {dice:.2f}, where 1.00 means identical.")
    dv = _num(val.get("coarse_volume_diff_pct"))
    if dv is not None and abs(dv) >= 5:
        s.append(f"The coarser version found about {abs(dv):.0f} percent {'less' if dv < 0 else 'more'} bone in total, "
                 "so bone volumes are less certain than the surface gap suggests.")
    if weak_dice is not None and weak_name and dice is not None and weak_dice < dice - 0.05:
        s.append(f"The weakest agreement was for the {_DISPLAY.get(weak_name, weak_name.replace('_', ' '))}, "
                 f"with an overlap score of {weak_dice:.2f}, so treat its shape with more caution.")
    if shift is not None:
        s.append("Moving the point where the program draws the edge of the bone a little towards muscle or towards "
                 f"bone moved the bone surface by up to about {format_mm(shift)} on average.")
    if vchange is not None:
        s.append(f"Smoothing the 3D surfaces changed their volume by at most about {_pct(vchange)} percent.")
    s.append("These checks compare the program with itself. They do not show that the result is right.")
    return _sec("Checks the program ran", s)


def _viewer_section() -> dict[str, str]:
    return _sec("Using the viewer", [
        "Drag with the mouse to rotate the model.",
        "Tick or untick the boxes to show or hide each part.",
        "The slice sliders move through the original scan pictures.",
        "The ruler measures the distance between two points you pick."])


# ---------------------------------------------------------------- headline
def _headline(m: dict[str, Any]) -> str:
    scan, hw = _d(m.get("scan")), _d(m.get("hardware"))
    structs = _structs(m)
    n_slices, ext = _num(scan.get("n_slices")), _vec3(scan.get("extent_mm"))
    s1 = ""
    if n_slices and n_slices >= 1:
        s1 = f"This scan has {_n(int(n_slices), 'slice')}" + (f" and covers about {format_mm(max(ext))}" if ext else "")
    elif ext:
        s1 = f"This scan covers about {format_mm(max(ext))}"
    found: list[str] = []
    if structs is not None and _bones(structs):
        found.append(_count_bones(_bones(structs)))
    if hw.get("present") is True or (_kind_volume(structs, "metal") or 0) > 0:
        found.append("metal hardware")
    for name, key in (("muscle", "muscle_cm3"), ("fat", "fat_cm3")):
        v = _volume(m, key, name)
        if v is not None and v > 0:
            found.append(name)
    s2 = f"The program marked {_join(found)} in the scan." if found else ""
    text = " ".join(x for x in ((s1 + ".") if s1 else "", s2) if x)
    return text or "There is not enough information to describe this scan."


# ---------------------------------------------------------------- public entry point
def build_summary(m: dict) -> dict:
    m = _d(m)
    scan, hw = _d(m.get("scan")), _d(m.get("hardware"))
    structs = _structs(m)
    guess = scan.get("body_part_guess")
    plain = _scan_sections(m)
    if structs is not None:
        plain.append(_bone_section(_bones(structs)))
    if hw.get("present") is True:
        plain.append(_metal_section(hw))
    tissue = _tissue_section(m)
    if tissue:
        plain.append(tissue)
    plain.append(_detail_section(m))
    checks = _checks_section(_d(m.get("validation")))
    if checks:
        plain.append(checks)
    plain.append(_viewer_section())
    return {
        "title": f"Your scan: {guess}" if guess in ("forearm", "limb") else "Your scan",
        "headline": _headline(m),
        "numbers": _tiles(m),
        "plain": plain,
        "limits": list(_LIMITS),
        "warnings": _warnings(m),
        "glossary": glossary(),
        "disclaimer": DISCLAIMER,
    }
