"""Command line: ``python -m emgmirror.ctseg {run,check-licences,labels}``.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import DISCLAIMER, allowlist, ontology
from .config import PROFILES, SegConfig


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m emgmirror.ctseg", description=f"CT segmentation engine. {DISCLAIMER}")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="segment one scan")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="CT (or MR) NIfTI file")
    src.add_argument("--dicom", type=Path, help="folder of DICOM files (the primary series is chosen)")
    r.add_argument("--out", type=Path, required=True, help="output folder")
    r.add_argument("--config", type=Path, help="JSON file with SegConfig fields (command-line flags win)")
    r.add_argument("--profile", choices=PROFILES)
    r.add_argument("--fast", action="store_true", default=None, help="3 mm TotalSegmentator models where available")
    r.add_argument("--device", choices=("cpu", "gpu", "mps"))
    r.add_argument("--modality", choices=("auto", "ct", "mr"))
    r.add_argument("--tasks", help="comma-separated subtasks instead of region selection ('' for none)")
    r.add_argument("--no-moose", action="store_true")
    r.add_argument("--keep-intermediate", action="store_true")
    c = sub.add_parser("check-licences", help="fail if any configured model or installed weight is not allowlisted")
    c.add_argument("--config", type=Path)
    c.add_argument("--weights-dir", type=Path, help="TotalSegmentator weights folder to inspect")
    c.add_argument("--models", help="comma-separated source:task keys to check, e.g. totalsegmentator:face")
    sub.add_parser("labels", help="print the canonical label table as JSON")
    return p


def _config(a) -> SegConfig:
    d = json.loads(a.config.read_text()) if a.config else {}
    for k in ("profile", "device", "modality"):
        if getattr(a, k, None):
            d[k] = getattr(a, k)
    if a.fast:
        d["fast"] = True
    if a.tasks is not None:
        d["tasks"] = [t for t in a.tasks.split(",") if t]
    if a.no_moose:
        d["moose"] = False
    if a.keep_intermediate:
        d["keep_intermediate"] = True
    return SegConfig(**d)


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.cmd == "labels":
        print(json.dumps([{"id": lab.id, "name": lab.name, "display": lab.display, "rgb": lab.rgb}
                          for lab in sorted(ontology.table().values(), key=lambda x: x.id)], indent=1))
        return 0
    if a.cmd == "check-licences":
        try:
            cfg = SegConfig.from_file(a.config) if a.config else SegConfig()
            ok = allowlist.check_config(cfg.licence_view(cfg.tasks or []))
            if a.models:
                ok += allowlist.check_models([m for m in a.models.split(",") if m])
            found = allowlist.check_weights_dir(a.weights_dir or cfg.weights_dir)
        except allowlist.LicenceError as e:
            print(f"LICENCE CHECK FAILED: {e}", file=sys.stderr)
            return 2
        print(f"licence check passed: {len(ok)} configured model(s) allowlisted"
              + (f", {len(found)} weight folder(s) allowlisted" if found else ""))
        return 0
    from .engine import segment
    from .inputs import InputRefused
    try:
        res = segment(a.input or a.dicom, a.out, _config(a), dicom=a.dicom is not None,
                      progress=lambda m: print(f"[ctseg] {m}", flush=True))
    except (InputRefused, allowlist.LicenceError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    n = len(res.labels_json["labels"])
    print(f"{n} structures -> {res.labels_path}\n{DISCLAIMER}")
    for w in res.labels_json["qc"]["warnings"]:
        print(f"warning: {w}")
    return 0
