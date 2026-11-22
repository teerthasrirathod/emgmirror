"""Generate the SOUP / licence register for the segmentation engine (IEC 62304 SOUP list + licence register).

Run inside the environment that has requirements-ctseg.txt installed:

    python scripts/ctseg_soup_register.py --weights-dir ~/.totalsegmentator/nnunet/results \
        --moose-zips DIR --out docs/ct_platform/device/SOUP_register_ctseg.md

* Software: the full dependency closure of the engine's direct dependencies, from installed metadata
  (name, version, licence), with the SHA-256 of the exact wheel file published on PyPI for the installed build
  tag (looked up on pypi.org). Each entry is marked device or research-only (only needed by MOOSE).
* Model weights: SHA-256 tree hash per nnU-Net dataset folder (TotalSegmentator) and SHA-256 of each MOOSE zip.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

from emgmirror.ctseg import allowlist, device

DIRECT = {
    "TotalSegmentator": "segmentation model runner (device: `total` task only)",
    "nnunetv2": "inference framework under TotalSegmentator",
    "torch": "neural-network runtime",
    "SimpleITK": "image I/O, resampling, geometry",
    "nibabel": "NIfTI fallback reader (non-orthonormal headers), used by TotalSegmentator",
    "numpy": "arrays",
    "scipy": "connected components, morphology, k-d tree (surface distance)",
    "pydicom": "DICOM header and pixel reading (emgmirror.ct.dicom)",
    "moosez": "MOOSE runner: peripheral bones, body composition (research build only)",
}
RESEARCH_ONLY_ROOTS = {"moosez"}


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def closure(roots) -> dict[str, set[str]]:
    """{package: {root packages that pull it in}} over installed metadata (markers evaluated, extras ignored)."""
    env = default_environment()
    env["extra"] = ""
    pulled: dict[str, set[str]] = {}
    for root in roots:
        stack, seen = [root], set()
        while stack:
            n = stack.pop()
            k = _norm(n)
            if k in seen:
                continue
            try:
                dist = md.distribution(n)
            except md.PackageNotFoundError:
                continue
            seen.add(k)
            pulled.setdefault(dist.metadata["Name"], set()).add(root)
            for r in dist.requires or []:
                req = Requirement(r)
                if req.marker is None or req.marker.evaluate(env):
                    stack.append(req.name)
    return pulled


def licence_of(dist) -> str:
    m = dist.metadata
    lic = m.get("License-Expression") or ""
    if not lic:
        raw = (m.get("License") or "").strip()
        lic = raw if raw and len(raw) < 60 and "\n" not in raw else ""
    if not lic:
        cls = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
        lic = "; ".join(cls)
    return lic or "UNKNOWN (check upstream)"


def wheel_sha256(dist) -> tuple[str | None, str | None]:
    name, ver = dist.metadata["Name"], dist.version
    tags = []
    try:
        wheel = dist.read_text("WHEEL") or ""
        tags = [ln.split(":", 1)[1].strip() for ln in wheel.splitlines() if ln.startswith("Tag:")]
    except Exception:  # noqa: BLE001
        pass
    try:
        req = urllib.request.Request(f"https://pypi.org/pypi/{name}/{ver}/json", headers={"User-Agent": "soup-register"})
        files = json.loads(urllib.request.urlopen(req, timeout=60).read())["urls"]
    except Exception:  # noqa: BLE001
        return None, None
    for f in files:
        if f["packagetype"] != "bdist_wheel":
            continue
        if any(all(part in f["filename"] for part in t.split("-")) for t in tags):
            return f["filename"], f["digests"]["sha256"]
    sd = [f for f in files if f["packagetype"] == "sdist"]
    return (sd[0]["filename"], sd[0]["digests"]["sha256"]) if sd else (None, None)


def zip_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights-dir", type=Path, required=True)
    ap.add_argument("--moose-zips", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    pulled = closure(DIRECT)
    rows = []
    for name in sorted(pulled, key=str.lower):
        dist = md.distribution(name)
        roots = pulled[name]
        build = "research only" if roots <= RESEARCH_ONLY_ROOTS else "device + research"
        why = DIRECT.get(name) or ("transitive: " + ", ".join(sorted(roots)))
        fn, sha = wheel_sha256(dist)
        rows.append((name, dist.version, licence_of(dist), build, why, fn or "–", sha or "not found on PyPI"))
        print(name, dist.version, sha is not None, file=sys.stderr)

    L = ["# SOUP and licence register: segmentation engine (S1)", "",
         "Research use, not for diagnosis or surgical planning, until licensed.", "",
         f"Generated {date.today().isoformat()} by `scripts/ctseg_soup_register.py` from the installed environment "
         f"(Python {sys.version.split()[0]}). Re-generate on every dependency or weight change; a changed hash is a "
         "SOUP change that needs review. Licences are as declared in package metadata; entries marked UNKNOWN or "
         "with a non-SPDX text must be checked against the upstream LICENSE file before release.", "",
         "## Software (SOUP)", "",
         "| package | version | licence (declared) | build | why | file | SHA-256 |",
         "|---|---|---|---|---|---|---|"]
    L += [f"| {n} | {v} | {lic} | {b} | {w} | `{fn}` | `{s}` |" for n, v, lic, b, w, fn, s in rows]

    L += ["", "## Model weights", "",
          "| model | weights | licence | build | why | hash (SHA-256) |", "|---|---|---|---|---|---|"]
    manifest = device.load_manifest()
    for i, d in manifest["datasets"].items():
        res = "1.5 mm" if int(i) < 296 else "3 mm (`--fast`)"
        L.append(f"| TotalSegmentator `total` {res} | nnU-Net Dataset{i} ({len(d['files'])} files, pinned per file in "
                 f"`resources/device_weights.json`) | Apache-2.0 | device + research | primary model, intended-use bones "
                 f"| tree `{d['tree_sha256']}` |")
    for key, al in allowlist.ALLOWED.items():
        if al.source != "totalsegmentator" or key == "totalsegmentator:total":
            continue
        for i in al.weight_ids:
            files = device.dataset_files(a.weights_dir, i)
            h = device.tree_hash(files) if files else "not downloaded in this environment"
            L.append(f"| TotalSegmentator `{al.task}` | nnU-Net Dataset{i} | {al.licence} | research only | "
                     f"region subtask | tree `{h}` |")
    if a.moose_zips:
        for z in sorted(a.moose_zips.glob("*.zip")):
            L.append(f"| MOOSE `{z.stem}` | {z.name} (GitHub release moosez-v.3.1.3) | CC-BY-4.0 | research only | "
                     f"limb bones / body composition; commercial terms to confirm in writing | `{zip_sha(z)}` |")
    L += ["| CADS `open` | not installed in S1 | CC-BY-SA-4.0 | research only | allowlisted, not run | – |",
          "| NVIDIA NV-Segment-CT | not installed in S1 | NVIDIA Open Model License | research only | allowlisted, not "
          "run; card says not for clinical use | – |", "",
          "## Vendored data", "",
          "| item | source | licence | build | why |", "|---|---|---|---|---|",
          "| `resources/label_table.json` colours and SNOMED codes | SlicerTotalSegmentator "
          "`totalsegmentator_snomed_mapping.csv`, `SegmentationCategoryTypeModifier-TotalSegmentator.term.json` | "
          "Apache-2.0 | device + research | label terminology for DICOM-SEG and the viewer |", ""]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(L))
    print(f"{len(rows)} packages -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
