"""Technical report (report.md) and end-of-build headline for the CT to 3D pipeline. Apache-2.0.

Pure text formatting over the ``results`` dict built by ``pipeline.build``. Every lookup tolerates missing
keys: a number that is absent is printed as "not measured" (with the reason when the dict holds one).
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from . import DISCLAIMER
from .validate import format_validation_markdown

NM = "not measured"
SEG_SKIP = {"timing", "timings", "flags", "params"}  # params: listed in full in the parameters table
SEG_MAX_DEPTH, SEG_MAX_LINES, LIST_MAX = 4, 250, 12
NOT_A_NUMBER_KEYS = {"note", "dice_grid", "dice_resampling", "interpretation", "scale", "source"}

LIMITS = [
    "Validation uses one acquisition: reconstructions of the same scan share systematic errors (threshold choice, "
    "beam hardening, metal streaks), so agreement between them does not expose those errors.",
    "No anatomical ground truth exists for this scan; no manual segmentation and no second scan were available.",
    "Regions outside the coarse stacks are not cross-checked.",
    "One global bone threshold is used; local density differences (osteopenic bone, thin cortex) are not adapted to.",
    "Metal blooming enlarges hardware and hides adjacent bone.",
    "Cortical versus medullary is a density split at this resolution; trabecular structure is not resolved.",
    "Lean tissue is one compartment: single muscles are not separated, and skin is included in lean tissue unless "
    "stated otherwise.",
    "Soft-tissue meshes are built on a coarser grid ({soft}) than bone meshes.",
    "Bone naming is rule-based; generic names (bone_1, bone_2, ...) are used when the evidence is insufficient.",
    "The rules were tuned on one forearm scan and are less tested on other body parts.",
    "Compressed transfer syntaxes and non-unit rescale slopes were not tested.",
]


# ------------------------------------------------------------------------------------------ helpers

def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x: Any) -> list:
    return x if isinstance(x, (list, tuple)) else []


def _dig(d: Any, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if not isinstance(d, dict) or d.get(k) is None:
            return default
        d = d[k]
    return d


def _f(x: Any, nd: int = 2, unit: str = "") -> str:
    """Fixed decimals plus unit; anything that is not a finite number becomes 'not measured'."""
    if isinstance(x, bool):
        return "yes" if x else "no"
    if _num(x):
        return f"{x:.{nd}f}" + (f" {unit}" if unit else "")
    return NM


def _g(x: Any) -> str:
    """Scalar for the generic renderers: floats to 4 significant figures."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return NM
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def _vec(v: Any, nd: int = 3, unit: str = "", sep: str = " x ") -> str:
    vals = _l(v)
    if not vals or not all(_num(a) for a in vals):
        return NM
    return sep.join(f"{a:.{nd}f}" for a in vals) + (f" {unit}" if unit else "")


def _cell(x: Any) -> str:
    return str(x).replace("|", "/").replace("\n", " ")


def _table(head: list[str], rows: Iterable[list]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out + [""]


def _demote(md: str, by: int, drop_first: bool = False) -> str:
    lines = md.splitlines()
    if drop_first and lines and lines[0].startswith("#"):
        lines = lines[1:]
    return "\n".join(("#" * by + ln) if ln.startswith("#") else ln for ln in lines).strip("\n")


def _range(vals: list[float], nd: int, unit: str = "") -> str:
    vals = [v for v in vals if _num(v)]
    if not vals:
        return NM
    lo, hi = min(vals), max(vals)
    u = f" {unit}" if unit else ""
    return f"{lo:.{nd}f}{u}" if f"{lo:.{nd}f}" == f"{hi:.{nd}f}" else f"{lo:.{nd}f} to {hi:.{nd}f}{u}"


def _pval(v: Any) -> str:
    if v is None:
        return "not set (measured on the scan, or disabled)"
    if isinstance(v, (list, tuple)):
        return ", ".join(_g(a) for a in v)
    return _g(v)


def _sanitize(x: Any, key: str = "") -> Any:
    """None -> NaN for numeric fields, so validate.format_validation_markdown prints n/a instead of crashing."""
    if isinstance(x, dict):
        return {k: _sanitize(v, k) for k, v in x.items()}
    if x is None and key not in NOT_A_NUMBER_KEYS:
        return float("nan")
    return x


# ------------------------------------------------------------------------------------- the numbers

def _numbers(r: dict) -> dict:
    """Every headline number as display strings, shared by the report table and the headline."""
    val = _d(r.get("validation"))
    fl = _d(val.get("accuracy_floor"))
    sp = _l(_dig(r, "geometry", "spacing_mm")) or _l(fl.get("spacing_mm"))
    ov = [v for k, v in _d(val.get("overlap")).items() if str(k).startswith("all bone") and isinstance(v, dict)]
    usable = [v for v in ov if _num(v.get("msd_mm"))]
    n_ov, n_ok = len(ov), len(usable)
    ratios = [v["msd_mm"] / v["voxel_floor_mm"] for v in usable
              if _num(v.get("voxel_floor_mm")) and v["voxel_floor_mm"] > 0]

    def over(key: str, nd: int, unit: str) -> str:
        s = _range([v.get(key) for v in ov], nd, unit)
        return s if s == NM else f"{s} (n = {n_ok} of {n_ov} stacks)" if n_ok != n_ov else f"{s} (n = {n_ov} stacks)"
    notes = [f"{k}: {v['note']}" for k, v in _d(val.get("overlap")).items() if isinstance(v, dict) and v.get("note")]
    row = _d(_d(_d(val.get("threshold_sweep")).get("structures")).get("bone above threshold"))
    st = _d(r.get("structures"))
    bones = {n: s for n, s in st.items() if isinstance(s, dict) and s.get("kind") == "bone"}
    mvm = _d(val.get("mesh_vs_mask"))
    vchg = [abs(_dig(s, "mesh", "volume_change_pct")) for s in bones.values()
            if _num(_dig(s, "mesh", "volume_change_pct"))]
    mdist = [_dig(mvm, n, "mean_abs_mm") for n in (bones or mvm)]
    mdist = [m for m in mdist if _num(m)]
    mmax = [_dig(mvm, n, "max_mm") for n in (bones or mvm)]
    mmax = [m for m in mmax if _num(m)]
    per = [(str(k).split(":")[0], str(k).split(": ", 1)[-1], v) for k, v in _d(val.get("overlap")).items()
           if str(k).split(":")[0] in bones and isinstance(v, dict)]  # whole bones only, not metal or body
    pd = [t for t in per if _num(t[2].get("dice"))]
    pv = [t for t in per if _num(t[2].get("volume_diff_pct"))]
    w_d = min(pd, key=lambda t: t[2]["dice"]) if pd else None
    w_v = max(pv, key=lambda t: abs(t[2]["volume_diff_pct"])) if pv else None
    edge = _d(val.get("edge_sensitivity"))
    erows = _d(edge.get("fractions"))
    edge_txt = ", ".join(f"{_f(_d(v).get('shift_mm'), 3, 'mm')} at {k}" for k, v in sorted(erows.items())) if erows \
        else NM
    qcs = [_d(s.get("qc")) for s in bones.values()]
    n_wt = sum(1 for q in qcs if q.get("watertight") is True)
    n_si = sum(1 for q in qcs if q.get("self_intersections") == 0)
    sweep_why = f"sweep failed: {val['threshold_sweep_error']}" if val.get("threshold_sweep_error") else "sweep not run"
    none_why = "no coarse stack was available" if not ov else "no usable value"
    gap = lambda s: s if s != NM else f"{NM} ({none_why})"  # noqa: E731
    return {
        "voxel": _vec(sp, 3, "mm"),
        "half_diag": _f(fl.get("half_diagonal_mm"), 3, "mm"), "max_spacing": _f(fl.get("max_spacing_mm"), 3, "mm"),
        "dice": gap(over("dice", 3, "")), "msd": gap(over("msd_mm", 3, "mm")), "hd95": gap(over("hd95_mm", 3, "mm")),
        "haus": gap(over("hausdorff_mm", 2, "mm")), "bias": gap(over("signed_bias_mm", 3, "mm")),
        "ratio": gap(_range(ratios, 2, "x coarse floor")),
        "pct100": _f(row.get("pct_per_100hu"), 2, "% volume per 100 HU") if row else
        f"{NM} ({sweep_why})",
        "shift100": _f(row.get("shift_mm_per_100hu"), 3, "mm per 100 HU") if row else NM,
        "mesh_vol": _f(max(vchg), 2, "%") if vchg else NM, "mesh_dist": _f(max(mdist), 3, "mm") if mdist else NM,
        "mesh_max": _f(max(mmax), 3, "mm") if mmax else NM,
        "weak_dice": f"{_f(w_d[2]['dice'], 3)} ({w_d[0]}, {w_d[1]})" if w_d else NM,
        "weak_vol": f"{_f(w_v[2]['volume_diff_pct'], 1, '%')} ({w_v[0]}, {w_v[1]})" if w_v else NM,
        "edge": edge_txt, "edge_max": _f(edge.get("max_abs_shift_mm"), 3, "mm") if edge else NM,
        "n_bone_meshes": len(bones), "n_wt": n_wt, "n_si": n_si, "n_struct": len(st), "notes": notes, "n_ov": n_ov,
    }


def _flags(r: dict) -> list[str]:
    """All plain-language problems, deduplicated, in the order found."""
    out: list[str] = []

    def add(s: str) -> None:
        if s not in out:
            out.append(s)
    for f in _l(_d(r.get("segmentation")).get("flags")) + _l(_d(r.get("measurements")).get("flags")):
        add(f"segmentation: {f}")
    for c in _l(r.get("coarse")):
        c = _d(c)
        tag = f"coarse series {_g(c.get('series'))} stack {_g(c.get('stack'))}"
        if c.get("error"):
            add(f"{tag}: check failed, {c['error']}")
        for f in _l(c.get("flags")):
            add(f"{tag}: {f}")
    for n, s in _d(r.get("structures")).items():
        q = _d(_d(s).get("qc"))
        if q and q.get("watertight") is False:
            add(f"mesh {n}: not watertight")
        if _num(q.get("self_intersections")) and q["self_intersections"] > 0:
            add(f"mesh {n}: {int(q['self_intersections'])} self-intersections")
    for n, mv in _d(_d(r.get("validation")).get("mesh_vs_mask")).items():
        mv = _d(mv)
        lim = mv.get("voxel_floor_mm")
        if _num(mv.get("max_mm")) and _num(lim) and mv["max_mm"] > 2 * lim:
            add(f"mesh {n}: up to {_f(mv['max_mm'], 2, 'mm')} from its label map (95 % within "
                f"{_f(mv.get('p95_mm'), 2, 'mm')}): smoothing and decimation moved the surface that far somewhere; "
                "the mesh is less exact there than the label map")
    hu = _d(_d(r.get("validation")).get("hu_sanity"))
    for k, v in hu.items():
        if isinstance(v, dict) and v.get("ok") is False:
            rng = _l(v.get("expected_range"))
            exp = f" (expected {_vec(rng, 0, 'HU', ' to ')})" if rng and all(_num(a) for a in rng) else ""
            add(f"HU check {k}: value {_f(v.get('value'), 0, 'HU')}{exp} failed")
    for n, b in _d(_d(r.get("validation")).get("bone_sanity")).items():
        for f in _l(_d(b).get("flags")):
            add(f"bone {n}: {f}")
    for n, b in _d(_d(r.get("validation")).get("enclosed_tissue")).items():
        for f in _l(_d(b).get("flags")):
            add(f"bone {n}: {f}")
    return out


# ------------------------------------------------------------------------------------------ report

def _seg_lines(d: Any, depth: int, out: list[str]) -> None:
    pad = "  " * depth
    for k, v in d.items():
        if len(out) >= SEG_MAX_LINES:
            return
        if k in SEG_SKIP:
            continue
        label = str(k).replace("_", " ")
        if isinstance(v, dict):
            if depth + 1 >= SEG_MAX_DEPTH:
                out.append(f"{pad}- {label}: (nested detail, {len(v)} entries, in results.json)")
            else:
                out.append(f"{pad}- {label}:")
                _seg_lines(v, depth + 1, out)
        elif isinstance(v, (list, tuple)):
            if all(not isinstance(a, (dict, list, tuple)) for a in v):
                shown = ", ".join(_g(a) for a in v[:LIST_MAX])
                more = f", ... ({len(v)} items)" if len(v) > LIST_MAX else ""
                out.append(f"{pad}- {label}: {shown or 'empty'}{more}")
            else:
                out.append(f"{pad}- {label}: {len(v)} items")
                for i, a in enumerate(v[:LIST_MAX]):
                    if isinstance(a, dict) and depth + 1 < SEG_MAX_DEPTH:
                        out.append(f"{pad}  - item {i + 1}:")
                        _seg_lines(a, depth + 2, out)
                    else:
                        out.append(f"{pad}  - {_g(a) if not isinstance(a, (dict, list, tuple)) else 'nested detail'}")
        else:
            out.append(f"{pad}- {label}: {_g(v)}")


def _series_section(r: dict, inventory_md: str) -> list[str]:
    p, s = _d(r.get("primary")), r.get("soft")
    o = ["## Series", "",
         f"- Primary series (segmentation and all measurements): number {_g(p.get('number'))}, "
         f"{_g(p.get('description'))}, kernel {_g(p.get('kernel'))}, orientation {_g(p.get('orientation'))}.",
         f"- Why it was chosen: {_g(p.get('reason'))}"]
    s = _d(s)
    o.append(f"- Soft-tissue series: number {_g(s.get('number'))}, {_g(s.get('description'))}, kernel "
             f"{_g(s.get('kernel'))}." if s else
             "- Soft-tissue series: none used; the primary series was used for soft tissue.")
    runs = [_d(c) for c in _l(r.get("coarse"))]
    used = [c for c in runs if not c.get("skipped") and not c.get("error")]
    o.append(f"- Coarse stacks compared with the primary: {len(used)} of {len(runs)}." if runs else
             "- Coarse stacks compared with the primary: none (not measured; no coarse stack was available or the "
             "check was disabled).")
    for c in runs:
        who = f"series {_g(c.get('series'))}, stack {_g(c.get('stack'))}"
        if c.get("error"):
            o.append(f"  - {who}: failed with {c['error']}")
        elif c.get("skipped"):
            o.append(f"  - {who}: not compared ({c['skipped']}).")
        else:
            o.append(f"  - {who}: orientation {_g(c.get('orientation'))}, {_g(c.get('slices'))} slices, spacing "
                     f"{_vec(c.get('spacing_mm'), 2, 'mm')}, runtime {_f(c.get('seconds'), 1, 's')}.")
    o += ["- Geometry comes from ImagePositionPatient and ImageOrientationPatient. The SliceThickness tag "
          f"({_f(p.get('slice_thickness_tag_mm'), 2, 'mm')}) is reported but not used.", "",
          "### Series inventory", "",
          _demote(inventory_md, 2) if inventory_md.strip() else NM + " (no inventory given).", ""]
    return o


def _geometry_section(r: dict) -> list[str]:
    g, names = _d(r.get("geometry")), _d(r.get("names"))
    dr = [x for x in _l(g.get("direction"))]
    o = ["## Geometry", "", f"- Size: {' x '.join(_g(a) for a in _l(g.get('size'))) or NM} voxels.",
         f"- Spacing x, y, z: {_vec(g.get('spacing_mm'), 4, 'mm')}.",
         f"- Origin (first voxel centre): {_vec(g.get('origin_mm'), 3, 'mm', ', ')}.",
         "- Direction cosines (rows):"]
    if len(dr) == 9 and all(_num(a) for a in dr):
        o += [f"  - {', '.join(f'{a:.5f}' for a in dr[i:i + 3])}" for i in (0, 3, 6)]
    else:
        o.append(f"  - {NM}")
    o += ["- Coordinate frame: DICOM patient LPS (x to the patient's left, y to posterior, z to superior), "
          "millimetres.",
          "", "Files written:", "", "- `volume.nii.gz`: the primary series as HU on the grid above.",
          "- `labels.nii.gz`: the label map on the same grid, with the ids below.",
          "- `meshes/`: one STL, OBJ and PLY file per structure, and `combined.glb` with all structures.",
          "- `viewer.html`: the local 3D viewer. Also `report.md`, `results.json`, `summary.json`, `labels.json`, "
          "`inventory.md`.", ""]
    if names:
        rows = sorted(((int(k), v) for k, v in names.items() if str(k).lstrip("-").isdigit()), key=lambda t: t[0])
        o += _table(["label id", "name"], [[i, n] for i, n in rows])
    return o


def _method_section(r: dict) -> list[str]:
    o = ["## Method and parameters", "", "### Segmentation parameters", ""]
    params = _d(r.get("parameters"))
    o += _table(["parameter", "value"], [[k, _pval(v)] for k, v in params.items()]) if params else [NM, ""]
    o += ["### Pipeline settings", ""]
    pipe = _d(r.get("pipeline"))
    o += _table(["setting", "value"], [[k, _pval(v)] for k, v in pipe.items()]) if pipe else [NM, ""]
    o += ["### Thresholds and decisions measured on this scan", ""]
    seg, lines = _d(r.get("segmentation")), []
    _seg_lines(seg, 0, lines)
    o += lines or [NM]
    if len(lines) >= SEG_MAX_LINES:
        o.append(f"- (list truncated at {SEG_MAX_LINES} lines; the full record is in results.json)")
    return o + [""]


def _structures_section(r: dict) -> list[str]:
    o = ["## Structures", ""]
    rows = []
    for n, s in _d(r.get("structures")).items():
        s = _d(s)
        m, q = _d(s.get("mesh")), _d(s.get("qc"))
        vm = q.get("volume_mm3")
        rows.append([n, _g(s.get("kind")), _f(s.get("volume_cm3"), 2), _vec(s.get("mesh_grid_mm"), 2),
                     _g(m.get("n_vertices")),
                     _g(m.get("n_faces")), _f(vm / 1000 if _num(vm) else None, 2), _f(q.get("volume_vs_voxels_pct"), 2),
                     _f(m.get("volume_change_pct"), 2), _f(q.get("watertight")), _g(q.get("self_intersections")),
                     _g(q.get("n_components")), _g(q.get("n_components_edge"))])
    head = ["structure", "kind", "volume (cm3)", "mesh grid (mm)", "vertices (count)", "faces (count)",
            "mesh volume (cm3)", "mesh vs voxel volume (%)", "smoothing+decimation volume change (%)", "watertight",
            "self-intersections (count)",
            "pieces joined by a vertex (count)", "pieces joined by an edge (count)"]
    o += _table(head, rows) if rows else [NM, ""]
    o += ["### Bones", ""]
    bs = _d(_d(r.get("validation")).get("bone_sanity"))
    brow = [[n, _f(b.get("length_mm"), 1), _f(b.get("length_robust_mm"), 1), _f(b.get("median_diameter_mm"), 1),
             _f(b.get("max_diameter_mm"), 1), _f(b.get("cortical_fraction"), 2),
             _g(b.get("n_components")), ", ".join(map(str, _l(b.get("touches_border")))) or "no"]
            for n, b in bs.items() if isinstance(b, dict)]
    o += _table(["bone", "length (mm)", "length p0.5-p99.5 (mm)", "median diameter (mm)", "max diameter (mm)",
                 "cortical fraction (0-1)", "pieces (count)", "touches the scan edge"], brow) if brow else [NM, ""]
    o += ["Length is a lower bound for any bone that touches the scan edge.", "", "### Tissue totals", ""]
    t = _d(_d(r.get("measurements")).get("tissue"))
    trow = [[lbl, _f(t.get(k), 1, "cm3"), _f(t.get(p), 1, "%") if p else "-"]
            for lbl, k, p in (("fat", "fat_cm3", "fat_pct"),
                              ("lean tissue (muscle, skin, other)", "muscle_cm3", "muscle_pct"),
                              ("bone", "bone_cm3", None), ("metal", "metal_cm3", None))]
    o += _table(["tissue", "volume", "share of fat + lean + bone + metal"], trow) if t else [NM, ""]
    return o


def _validation_section(r: dict) -> list[str]:
    val = _d(r.get("validation"))
    o = ["## Validation", "",
         "No manual ground truth and no second scan exist for this study, so the checks are independent but indirect: "
         "coarse reconstructions of the same acquisition are segmented on their own and compared with the primary "
         "result; the bone threshold is swept to bound the effect of that choice; HU values of air, fat, muscle and "
         "cortical bone are compared with plausible ranges; bone size and topology are checked against rough adult "
         "bands; each mesh is "
         "compared with the label map it came from; and a synthetic phantom with analytic truth is segmented in the "
         "test suite to measure the algorithm itself under idealised physics.", ""]
    try:
        md = format_validation_markdown(_sanitize(val))
        o += [_demote(md, 1, drop_first=True), ""]
    except Exception as exc:  # partial data must not stop the report
        o += [f"{NM} (the validation tables could not be formatted: {type(exc).__name__}: {exc}).", ""]
    if val.get("threshold_sweep_error"):
        o += [f"Threshold sweep error: {val['threshold_sweep_error']}", ""]
    enc = _d(val.get("enclosed_tissue"))
    if enc:
        o += ["### Tissue enclosed by bone", "",
              "Non-bone voxels that a bone surrounds in at least one axial, coronal or sagittal slice. Real anatomy "
              "can do this (a canal around an implant, a fossa seen edge-on); a region that fills most of its "
              "bounding box is a straight-edged artefact of the method. Box fill = region volume / bounding box.", ""]
        rows = []
        for b, row in enc.items():
            row = _d(row)
            big = [_d(x) for x in _l(row.get("largest"))]
            desc = "; ".join(f"{_f(x.get('volume_mm3'), 0)} mm3, box fill {_f(x.get('box_fill'), 2)}, labels "
                             f"{', '.join(map(str, _l(x.get('labels'))))}" for x in big) or "none"
            rows.append([b, _f(row.get("enclosed_cm3"), 3), _g(row.get("n_regions")), desc])
        o += _table(["bone", "enclosed tissue (cm3)", "regions (count)", "largest regions"], rows)
    if val.get("enclosed_tissue_error"):
        o += [f"Enclosed-tissue check error: {val['enclosed_tissue_error']}", ""]
    edge = _d(val.get("edge_sensitivity"))
    if edge:
        o += ["### Edge sensitivity of the delivered bone surface", ""]
        o += _table(["edge fraction (0.5 = half-maximum)", "bone volume change (cm3)", "mean outer surface shift (mm)"],
                    [[k, _f(_d(v).get("volume_change_cm3"), 3), _f(_d(v).get("shift_mm"), 3)]
                     for k, v in sorted(_d(edge.get("fractions")).items())])
        if edge.get("note"):
            o += [f"{_g(edge['note'])}", ""]
    notes = _d(r.get("notes"))
    if notes:
        o += ["### Notes on how the checks were run", ""] + [f"- {k.replace('_', ' ')}: {_g(v)}"
                                                           for k, v in notes.items()] + [""]
    return o


def build_report(results: dict, inventory_md: str, summary: dict | None = None) -> str:
    """The whole report.md. Missing data is printed as 'not measured'; this never raises on a missing key."""
    r = _d(results)
    n = _numbers(r)
    soft = _dig(r, "pipeline", "soft_mesh_mm")
    o = ["# CT to 3D report", "", DISCLAIMER, "",
         "This is DICOM-correct geometry in patient millimetres with measured error, not a certified medical device, "
         "and no human expert has reviewed the segmentation.", "",
         "## Headline numbers", ""]
    rows = [
        ["Voxel size x, y, z", n["voxel"], "Grid of the primary series."],
        ["Accuracy floor, half voxel diagonal", n["half_diag"],
         "Surfaces on this grid cannot be located better than this."],
        ["Accuracy floor, largest spacing", n["max_spacing"], "Coarsest single-axis spacing of the grid."],
        ["Coarse comparison (all bone): Dice, unitless", n["dice"], "Volume overlap of the two segmentations."],
        ["Coarse comparison: mean surface distance", n["msd"], "Mean gap between the two bone surfaces."],
        ["Coarse comparison: 95th-percentile distance", n["hd95"], "Gap that 95 % of the surface stays within."],
        ["Coarse comparison: Hausdorff distance", n["haus"], "Largest gap anywhere; sensitive to single outliers."],
        ["Coarse comparison: signed bias", n["bias"], "Positive: the coarse surface lies outside the primary one."],
        ["Coarse agreement relative to the coarse floor", n["ratio"],
         "Mean surface distance between the two segmentations divided by the coarse grid floor (agreement, not error); "
         "below 1 means the gap is within what the coarse grid can resolve."],
        ["Coarse comparison, weakest bone: Dice", n["weak_dice"],
         "Lowest per-bone overlap over all coarse stacks. The all-bone figures above are dominated by the large bones."],
        ["Coarse comparison, largest bone volume difference", n["weak_vol"],
         "Coarse minus primary volume, per bone, largest in magnitude. It does not say which of the two is right."],
        ["Edge sensitivity of the delivered bone surface", n["edge"],
         "Mean outer surface shift when the edge moves from the local half-maximum (0.5) to that fraction of the way "
         "from lean tissue to the local maximum. Positive: the bone grows. This is the decision that places the "
         "delivered surface."],
        ["Threshold sensitivity of the thresholded mask, volume", n["pct100"],
         "Intermediate step only: volume of the hysteresis mask per 100 HU, before closing, filling and the "
         "local half-maximum edge. It is not the delivered bone."],
        ["Threshold sensitivity of the thresholded mask, surface shift", n["shift100"],
         "The same change as a mean displacement of that mask; an upper bound, because inner surfaces move too."],
        ["Mesh fidelity, volume change", n["mesh_vol"],
         "Largest absolute volume change over bone meshes from smoothing and decimation."],
        ["Mesh fidelity, mean distance to label map", n["mesh_dist"],
         "Largest per-bone mean distance of a mesh to its label map."],
        ["Mesh fidelity, largest distance to label map", n["mesh_max"],
         "Largest distance anywhere on any bone mesh to its label map."],
        ["Mesh integrity, watertight", f"{n['n_wt']} of {n['n_bone_meshes']} bone meshes", "Closed surfaces."],
        ["Mesh integrity, no self-intersections", f"{n['n_si']} of {n['n_bone_meshes']} bone meshes",
         "Triangles that do not cross each other."]]
    o += _table(["quantity", "value", "what it shows"], rows)
    if n["notes"]:
        o += ["Notes on the comparisons: " + "; ".join(n["notes"]) + ".", ""]
    o += ["Agreement between reconstructions of one acquisition measures stability to resolution, not anatomical "
          "truth. Both reconstructions share the same patient, scanner, beam hardening and threshold rule, so their "
          "shared errors do not appear in these numbers. The mean surface distance alone can hide shifts below the "
          "coarse floor and a few large deviations, so Dice and the 95th-percentile distance must be read with it. "
          "Nothing is measured for regions "
          "or structures where no coarse stack exists.", ""]
    o += _series_section(r, inventory_md) + _geometry_section(r) + _method_section(r) + _structures_section(r)
    o += _validation_section(r)
    fl = _flags(r)
    o += ["## Where the labels are unreliable (failure modes)", ""]
    o += [f"- {f}" for f in fl] if fl else [
        "No automatic flags were raised. This is not evidence of correctness: the checks only catch the failures "
        "they were written to catch."]
    o += ["", "## Honest limits", ""]
    soft_txt = f"{soft:g} mm" if _num(soft) else "not used on this scan"
    o += [f"- {x.format(soft=soft_txt)}" for x in LIMITS]
    o += ["", "## Timings", ""]
    tim = _d(r.get("timings_s"))
    o += _table(["step", "seconds (s)"], [[k, _f(v, 1)] for k, v in tim.items()]) if tim else [NM, ""]
    per = [[n_, _f(_d(s).get("seconds"), 1)] for n_, s in _d(r.get("structures")).items()]
    if per:
        o += ["Meshing time per structure:", ""] + _table(["structure", "seconds (s)"], per)
    o += [DISCLAIMER, ""]
    text = "\n".join(o)
    return re.sub(r"\b(?:None|nan)\b", "n/a", text)


def headline(results: dict) -> str:
    """8-15 plain lines for the end of the build."""
    r = _d(results)
    n = _numbers(r)
    nb = n["n_bone_meshes"]
    ids = _dig(r, "primary", "number")
    lines = [f"Built {n['n_struct']} structures ({nb} bone) from series {_g(ids) if ids is not None else NM}.",
             "Files: volume.nii.gz, labels.nii.gz, meshes/ (STL, OBJ, PLY, combined.glb), viewer.html, report.md.",
             f"Voxel size {n['voxel']}; accuracy floor {n['half_diag']} (half voxel diagonal), "
             f"largest spacing {n['max_spacing']}.",
             f"Coarse comparison, all bone, Dice: {n['dice']}",
             f"Coarse comparison, mean surface distance: {n['msd']}",
             f"Coarse comparison, 95th-percentile distance: {n['hd95']}",
             f"Coarse comparison, Hausdorff distance: {n['haus']}",
             f"Coarse comparison, signed bias: {n['bias']}",
             f"Coarse agreement relative to the coarse floor (agreement, not error): {n['ratio']}",
             f"Coarse comparison, weakest bone Dice: {n['weak_dice']}; largest volume difference: {n['weak_vol']}",
             f"Edge sensitivity of the delivered bone surface: {n['edge']}",
             f"Threshold sensitivity of the intermediate thresholded mask: {n['pct100']}; {n['shift100']}",
             f"Mesh fidelity: volume change up to {n['mesh_vol']}; distance to label map up to {n['mesh_dist']} mean, "
             f"{n['mesh_max']} max",
             f"Mesh integrity: {n['n_wt']} of {nb} bone meshes watertight, "
             f"{n['n_si']} of {nb} without self-intersections"]
    weak = _flags(r)[:5]
    fixed = ["validation uses one acquisition, so shared systematic errors are not exposed",
             "no anatomical ground truth and no expert review of the segmentation",
             "one global bone threshold; metal blooming hides adjacent bone",
             "soft tissue is one lean compartment; single muscles are not separated",
             "tuned on one forearm scan; less tested on other body parts"]
    weak += [f for f in fixed if len(weak) < 5]
    lines += [f"Still weak: {w}" for w in weak[:5]]
    lines.append(DISCLAIMER)
    return "\n".join(re.sub(r"\b(?:None|nan)\b", "n/a", ln) for ln in lines)
