"""Benchmark the ctseg engine on public TotalSegmentator-dataset subjects (Zenodo 6802613, CC BY 4.0).

Research use, not for diagnosis or surgical planning.

    python scripts/ctseg_benchmark.py --data-dir DATA --runs-dir RUNS --n 15 --fetch \
        --profile minimal --fast --device cpu --report docs/ct_platform/results/S1_benchmark.md

* ``--fetch`` downloads ``meta.csv`` and the chosen subjects (test split only, spread over study types) by
  HTTP range reads from the dataset zip; without it, subjects already in ``DATA/subjects`` are used.
* Every structure with a reference mask is scored: Dice, mean symmetric surface distance and HD95 (mm, via
  ``emgmirror.ct.validate.overlap_metrics``). Structures present in neither are skipped; a structure missing
  from the prediction scores Dice 0; one predicted but absent from the reference scores Dice 0.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from emgmirror.ctseg import DISCLAIMER, benchmark as B
from emgmirror.ctseg.config import SegConfig
from emgmirror.ctseg.engine import segment


def _f(x, nd=3):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--runs-dir", type=Path, required=True)
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--profile", default="minimal")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--resume", action="store_true", help="reuse engine outputs already in --runs-dir")
    ap.add_argument("--no-surface", action="store_true")
    ap.add_argument("--report", type=Path, required=True)
    a = ap.parse_args()

    meta_path = a.data_dir / "meta.csv"
    rz = None
    if a.fetch:
        rz = B.RemoteZip.from_zenodo()
        if not meta_path.exists():
            name = next(n for n in rz.zip.namelist() if n.endswith("/meta.csv"))
            a.data_dir.mkdir(parents=True, exist_ok=True)
            meta_path.write_bytes(rz.zip.read(name))
    meta = B.read_meta(meta_path.read_text(encoding="utf-8"))
    subjects = B.pick_subjects(meta, a.n)
    cfg = SegConfig(profile=a.profile, fast=a.fast, device=a.device, moose=a.profile != "minimal")

    per_subject, rows = [], []
    for m in subjects:
        sid = m["image_id"]
        d = a.data_dir / "subjects" / sid
        if rz is not None:
            B.fetch_subject(rz, sid, a.data_dir / "subjects")
        if not (d / "ct.nii.gz").exists():
            print(f"{sid}: not downloaded, skipped")
            continue
        out = a.runs_dir / sid
        t0 = time.time()
        if a.resume and (out / "labels.json").exists():
            lj = json.loads((out / "labels.json").read_text())
            prov = json.loads((out / "provenance.json").read_text())
        else:
            res = segment(d / "ct.nii.gz", out, cfg, progress=lambda s, sid=sid: print(f"[{sid}] {s}", flush=True))
            lj, prov = res.labels_json, res.provenance
        scores = B.score_subject(out / "labels.nii.gz", lj, d / "segmentations", surface=not a.no_surface)
        for r in scores:
            r["subject"] = sid
        rows += scores
        dice = [r["dice"] for r in scores if r["ref_voxels"] > 0]
        scan = lj["qc"]["scan"]
        per_subject.append({
            "subject": sid, "study_type": m["study_type"] or "(not given)",
            "size": "x".join(map(str, scan["size"])), "spacing": "x".join(f"{s:.2f}" for s in scan["spacing"]),
            "seconds": prov["timings"]["total_s"], "n_ref": len(dice),
            "mean_dice": statistics.fmean(dice) if dice else float("nan"),
            "median_dice": statistics.median(dice) if dice else float("nan"),
            "regions": ", ".join(lj["qc"]["regions"]), "flags": ", ".join(scan["flags"]) or "–",
            "n_cut": sum("cut_by_scan_edge" in e["flags"] for e in lj["labels"]),
            "models": ", ".join(x["source"].split(":")[1] for x in prov["models"]),
        })
        print(f"{sid}: {len(dice)} structures, mean Dice {per_subject[-1]['mean_dice']:.3f} "
              f"({time.time() - t0:.0f} s)", flush=True)

    a.runs_dir.mkdir(parents=True, exist_ok=True)
    (a.runs_dir / "benchmark_rows.json").write_text(json.dumps({"subjects": per_subject, "rows": rows}, indent=1))
    write_report(a, cfg, per_subject, rows, prov_software(a.runs_dir, per_subject))


def prov_software(runs: Path, per_subject) -> dict:
    for s in per_subject:
        p = runs / s["subject"] / "provenance.json"
        if p.exists():
            return json.loads(p.read_text())["software"]
    return {}


def write_report(a, cfg: SegConfig, per_subject, rows, software) -> None:
    ref_rows = [r for r in rows if r["ref_voxels"] > 0]
    by: dict[str, list[dict]] = {}
    for r in ref_rows:
        by.setdefault(r["structure"], []).append(r)
    fp = [r for r in rows if r["ref_voxels"] == 0]
    all_dice = [r["dice"] for r in ref_rows]
    subj_means = [s["mean_dice"] for s in per_subject if not math.isnan(s["mean_dice"])]
    L = [
        "# S1 benchmark: segmentation engine on public CTs", "", f"{DISCLAIMER}", "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d} by `scripts/ctseg_benchmark.py`.", "",
        "## Settings", "",
        f"- Data: TotalSegmentator dataset v3.0.0 (Zenodo record 6802613, CC BY 4.0), **test split only**, "
        f"{len(per_subject)} subjects picked one per study type (see table). Reference = the dataset's own masks.",
        f"- Engine profile **`{cfg.profile}`**, fast = **{cfg.fast}**, device = **{cfg.device}**"
        f" ({platform.processor() or platform.machine()}, no GPU). With `fast`, TotalSegmentator runs its 3 mm "
        "`total` model (task 297) and 3 mm `body` model (task 300); the result is resampled back to the CT grid.",
        f"- Software: " + ", ".join(f"{k} {v}" for k, v in software.items() if v),
        "- Metrics per structure on the native CT grid: Dice; mean symmetric surface distance (MSD) and HD95 in mm "
        "from `emgmirror.ct.validate.overlap_metrics` (voxel-face surfaces, up to 20,000 points per side). "
        "Structures absent from both reference and prediction are skipped; a missed structure scores Dice 0.",
        "", "**Read with care:**",
        "- This is the fast 3 mm CPU setting, chosen because no GPU is available yet. The production setting "
        "(1.5 mm `total`, GPU) should score higher; TotalSegmentator reports mean Dice 0.93 for v2 `total` at 1.5 mm "
        "on its own test split. Re-run this script on the GPU worker with `--profile standard` (no `--fast`).",
        "- The v3 dataset's test split may not match the split used to train the v2.18 weights exactly; some "
        "subjects could have been seen in training, which would flatter the numbers.",
        "- Small structures and structures cut by the scan edge pull the mean down; the median is more robust.",
        "", "## Summary", "",
        f"| subjects | structures scored | mean Dice | median Dice | mean of per-subject mean Dice | "
        f"false-positive structures |", "|---|---|---|---|---|---|",
        f"| {len(per_subject)} | {len(ref_rows)} | {_f(statistics.fmean(all_dice) if all_dice else None)} | "
        f"{_f(statistics.median(all_dice) if all_dice else None)} | "
        f"{_f(statistics.fmean(subj_means) if subj_means else None)} | {len(fp)} |",
        "", "## Per subject", "",
        "| subject | study type | voxels | spacing (mm) | structures | mean Dice | median Dice | runtime (s) | "
        "regions detected | scan flags | cut structures |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in per_subject:
        L.append(f"| {s['subject']} | {s['study_type']} | {s['size']} | {s['spacing']} | {s['n_ref']} | "
                 f"{_f(s['mean_dice'])} | {_f(s['median_dice'])} | {s['seconds']:.0f} | {s['regions']} | "
                 f"{s['flags']} | {s['n_cut']} |")
    L += ["", "## Per structure (subjects where the reference has the structure)", "",
          "| structure | n | mean Dice | min Dice | mean MSD (mm) | mean HD95 (mm) |", "|---|---|---|---|---|---|"]
    for name in sorted(by, key=lambda n: -statistics.fmean(r["dice"] for r in by[n])):
        rs = by[name]
        msd = [r["msd_mm"] for r in rs if r.get("msd_mm") is not None]
        hd = [r["hd95_mm"] for r in rs if r.get("hd95_mm") is not None]
        L.append(f"| {name} | {len(rs)} | {_f(statistics.fmean(r['dice'] for r in rs))} | "
                 f"{_f(min(r['dice'] for r in rs))} | {_f(statistics.fmean(msd) if msd else None, 2)} | "
                 f"{_f(statistics.fmean(hd) if hd else None, 2)} |")
    if fp:
        L += ["", "## Predicted but absent from the reference", "",
              "| subject | structure | predicted voxels |", "|---|---|---|"]
        L += [f"| {r['subject']} | {r['structure']} | {r['pred_voxels']} |" for r in fp]
    L += ["", f"Raw rows: `benchmark_rows.json` in the runs folder (not committed).", ""]
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text("\n".join(L))
    print(f"report -> {a.report}")


if __name__ == "__main__":
    main()
