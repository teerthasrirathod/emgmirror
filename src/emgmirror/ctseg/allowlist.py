"""Weights allowlist: only commercial-safe models may be configured or present.

The service may become commercial, so the rule is an explicit *allowlist*, never a denylist: a task or
weight that is not listed here is refused, whatever it is. Sources (re-checked 2026-11-21, see
docs/ct_platform/research/critique.md, L3-L7):

* TotalSegmentator code and the tasks below: Apache-2.0 (README licence table). Tasks that need a licence
  key (``appendicular_bones``, ``tissue_types``, ``face``, ``heartchambers_highres``, ...) and
  ``brain_aneurysm`` (CC BY-NC) are not listed and are therefore refused.
* MOOSE 3.2 weights: CC BY 4.0 (README badge); code Apache-2.0.
* CADS: only the ``open`` weights (CC BY-SA 4.0), selected with ``-license open``. The CLI default
  ``reference`` and the CADS-region suite use a custom licence and are refused.

Never allowed, by name, so a clear message is given: nnInteractive, MedSAM2, VoxTell, SuPreM,
NV-Segment-CTMR (all non-commercial).

:func:`check_weights_dir` also inspects TotalSegmentator's weights folder: any nnU-Net ``DatasetNNN_*``
folder whose id is not one of the allowlisted tasks' ids fails the check, so a container image cannot ship
licence-key weights by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


class LicenceError(ValueError):
    """A configured model or a weight on disk is not on the commercial-safe allowlist."""


@dataclass(frozen=True)
class Allowed:
    source: str  # "totalsegmentator" | "moose" | "cads"
    task: str
    licence: str
    licence_url: str
    weight_ids: tuple[int, ...] = ()  # nnU-Net dataset ids the task downloads
    note: str = ""


_TS_URL = "https://github.com/wasserth/TotalSegmentator#subtasks"
_MOOSE_URL = "https://github.com/ENHANCE-PET/MOOSE"
_CADS_URL = "https://github.com/murong-xu/CADS"

# nnU-Net dataset ids from TotalSegmentator 2.18.0 ``map_tasks_config.TASK_CONFIGS``.
ALLOWED: dict[str, Allowed] = {a.source + ":" + a.task: a for a in [
    Allowed("totalsegmentator", "total", "Apache-2.0", _TS_URL, (291, 292, 293, 294, 295, 297, 298)),
    Allowed("totalsegmentator", "total_mr", "Apache-2.0", _TS_URL, (850, 851, 852, 853)),
    Allowed("totalsegmentator", "body", "Apache-2.0", _TS_URL, (299, 300)),
    Allowed("totalsegmentator", "body_mr", "Apache-2.0", _TS_URL, (597, 598)),
    Allowed("totalsegmentator", "lung_vessels", "Apache-2.0", _TS_URL, (117,)),
    Allowed("totalsegmentator", "vertebrae_pp_refined", "Apache-2.0", _TS_URL, (803, 305),
            "also downloads the vertebrae_body model (305) for the refinement"),
    Allowed("totalsegmentator", "craniofacial_structures", "Apache-2.0", _TS_URL, (115,)),
    Allowed("totalsegmentator", "headneck_bones_vessels", "Apache-2.0", _TS_URL, (776,)),
    Allowed("totalsegmentator", "headneck_muscles", "Apache-2.0", _TS_URL, (778, 779)),
    Allowed("totalsegmentator", "head_glands_cavities", "Apache-2.0", _TS_URL, (775,)),
    Allowed("totalsegmentator", "trunk_cavities", "Apache-2.0", _TS_URL, (343,)),
    Allowed("totalsegmentator", "liver_segments", "Apache-2.0", _TS_URL, (570,), "small training set"),
    Allowed("moose", "clin_ct_peripheral_bones", "CC-BY-4.0", _MOOSE_URL, (666,),
            "written confirmation of hosted commercial use still to be obtained (critique.md L6)"),
    Allowed("moose", "clin_ct_body_composition", "CC-BY-4.0", _MOOSE_URL, (778, 112),
            "workflow first runs clin_ct_fast_vertebrae (112) to crop the field of view"),
    Allowed("moose", "clin_ct_fast_vertebrae", "CC-BY-4.0", _MOOSE_URL, (112,), "only as body-composition crop"),
    Allowed("cads", "open", "CC-BY-SA-4.0", _CADS_URL, (), "must run with -license open"),
    Allowed("nvidia", "nv-segment-ct", "NVIDIA-Open-Model-License", "https://huggingface.co/nvidia/NV-Segment-CT", (),
            "pinned HF revision only; card says not for clinical use; not wired into S1"),
]}

# Device build (docs/ct_platform/REGULATORY_PATH.md section 4): TotalSegmentator `total` only. MOOSE, CADS and
# NVIDIA need written commercial/clinical terms first and stay in the research build.
BUILDS = ("research", "device")
DEVICE_ALLOWED = ("totalsegmentator:total",)

NEVER = {
    "nninteractive": "CC BY-NC-SA 4.0 weights", "medsam2": "research and education only",
    "voxtell": "CC BY-NC-SA 4.0 weights", "suprem": "CC BY-NC-ND 4.0", "nv-segment-ctmr": "NVIDIA non-commercial",
    "totalsegmentator:appendicular_bones": "TotalSegmentator licence key (non-commercial)",
    "totalsegmentator:tissue_types": "TotalSegmentator licence key (non-commercial)",
    "totalsegmentator:face": "TotalSegmentator licence key (non-commercial)",
    "totalsegmentator:heartchambers_highres": "TotalSegmentator licence key (non-commercial)",
    "totalsegmentator:brain_aneurysm": "CC BY-NC 4.0, no commercial licence offered",
    "cads:reference": "custom CADS licence", "cads:research": "CC BY-NC-SA 4.0", "cads:region": "custom CADS licence",
}

_DATASET_RE = re.compile(r"^Dataset(\d{3})_")


def allowed(key: str, build: str = "research") -> Allowed:
    """Return the allowlist entry for ``source:task`` or raise :class:`LicenceError`."""
    k = key.strip().lower()
    if build not in BUILDS:
        raise LicenceError(f"unknown build '{build}'")
    if build == "device" and k not in DEVICE_ALLOWED:
        raise LicenceError(f"model '{key}' is not allowed in the device build (device build = "
                           f"{', '.join(DEVICE_ALLOWED)} only; MOOSE, CADS and NVIDIA are research-only until "
                           "their terms are confirmed in writing)")
    if k in ALLOWED:
        return ALLOWED[k]
    why = NEVER.get(k) or NEVER.get(k.split(":")[-1])
    raise LicenceError(f"model '{key}' is not on the commercial-safe allowlist"
                       + (f" ({why})" if why else "") + "; see emgmirror/ctseg/allowlist.py")


def check_models(keys: Iterable[str], build: str = "research") -> list[Allowed]:
    """Check every configured model; collect all problems into one error."""
    out, bad = [], []
    for k in keys:
        try:
            out.append(allowed(k, build))
        except LicenceError as e:
            bad.append(str(e))
    if bad:
        raise LicenceError("; ".join(bad))
    return out


def check_config(cfg: Mapping, build: str | None = None) -> list[Allowed]:
    """Validate a config mapping (see :class:`emgmirror.ctseg.config.SegConfig`).

    Fails on any model outside the allowlist, on a licence key being configured (that is how
    TotalSegmentator unlocks non-commercial tasks), and on CADS without ``license == "open"``."""
    if cfg.get("license_number") or cfg.get("totalseg_license"):
        raise LicenceError("a TotalSegmentator licence key is configured; licence-key tasks are not "
                           "commercial-safe and must not be enabled on this service")
    keys = [f"totalsegmentator:{t}" for t in cfg.get("tasks", []) or []]
    keys += [f"totalsegmentator:{t}" for t in cfg.get("extra_tasks", []) or []]
    keys += [f"moose:{m}" for m in cfg.get("moose_models", []) or []]
    keys += [str(k) for k in cfg.get("models", []) or []]
    build = build or cfg.get("build") or "research"
    if build == "device" and cfg.get("body"):
        keys.append("totalsegmentator:body")
    cads = cfg.get("cads")
    if cads is not None and cads is not False:
        lic = str((cads or {}).get("license", "reference")).lower() if isinstance(cads, Mapping) else "reference"
        keys.append(f"cads:{lic}")
    return check_models(keys, build)


def allowed_weight_ids(build: str = "research") -> set[int]:
    keys = DEVICE_ALLOWED if build == "device" else tuple(ALLOWED)
    return {i for k in keys for i in ALLOWED[k].weight_ids}


def check_weights_dir(path: Path | str | None, build: str = "research") -> list[str]:
    """Fail if a weights folder holds an nnU-Net dataset that no allowlisted task uses.

    Returns the dataset folder names found (all allowed). A missing folder is fine (nothing installed)."""
    if path is None:
        return []
    p = Path(path)
    if not p.is_dir():
        return []
    ok = allowed_weight_ids(build)
    found, bad = [], []
    for d in sorted(p.rglob("Dataset*")):
        m = _DATASET_RE.match(d.name)
        if not d.is_dir() or not m:
            continue
        found.append(d.name)
        if int(m.group(1)) not in ok:
            bad.append(str(d.relative_to(p)))
    if bad:
        raise LicenceError(f"weights folder holds models that are not allowed in the {build} build: "
                           + ", ".join(bad) + ". Remove them before deploying.")
    return found
