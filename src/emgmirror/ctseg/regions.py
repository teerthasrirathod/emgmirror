"""Region detection from the base model's result, and the run plan that follows from it.

A region is present when its marker structures (from ``total``/``total_mr`` and ``body``) add up to at least
``region_min_ml``; markers are chosen so that a region only counts when the scan actually covers it (a few
slices of liver at the bottom of a chest CT still count as abdomen, deliberately: the subtask then runs and
QC marks its structures as cut by the scan edge).

Limbs: ``upper_limb``/``lower_limb`` come from humerus/femur and from ``body_extremities``. Any limb, or a
``body_extremities`` volume of at least ``limb_min_ml``, triggers MOOSE ``clin_ct_peripheral_bones`` (the only
commercial-safe source of forearm, hand, lower-leg and foot bones). The trunk wall (lumbar spine in view)
triggers MOOSE ``clin_ct_body_composition`` (muscle / subcutaneous / visceral fat), whose own workflow crops
to the lumbar region.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .config import REGION_SUBTASKS, SegConfig

_MARKERS: dict[str, re.Pattern] = {
    "head": re.compile(r"^(brain|skull)$"),
    "neck": re.compile(r"^(vertebrae_C[1-7]|thyroid_gland)$"),
    "thorax": re.compile(r"^(lung_.*|heart|vertebrae_T([1-9]|1[0-2])|rib_.*)$"),
    "abdomen": re.compile(r"^(liver|spleen|kidney_(left|right)|pancreas|stomach|vertebrae_L[1-5])$"),
    "pelvis": re.compile(r"^(hip_(left|right)|sacrum|urinary_bladder|gluteus_maximus_(left|right))$"),
    "spine": re.compile(r"^(vertebrae_.*|vertebrae)$"),
    "upper_limb": re.compile(r"^humerus_(left|right)$"),
    "lower_limb": re.compile(r"^femur_(left|right)$"),
    "lumbar": re.compile(r"^(vertebrae_L[1-5])$"),
}


@dataclass
class Plan:
    base: str  # "totalsegmentator:total" | "totalsegmentator:total_mr"
    regions: dict[str, float] = field(default_factory=dict)  # region -> marker volume (mL)
    subtasks: list[str] = field(default_factory=list)  # "totalsegmentator:<task>"
    moose: list[str] = field(default_factory=list)  # "moose:<model>"
    reasons: list[str] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return [*self.subtasks, *self.moose]

    def to_dict(self) -> dict:
        return {"base": self.base, "regions": {k: round(v, 1) for k, v in self.regions.items()},
                "subtasks": self.subtasks, "moose": self.moose, "reasons": self.reasons}


def volumes_ml(labels: np.ndarray, classes: dict[int, str], voxel_ml: float) -> dict[str, float]:
    counts = np.bincount(labels.ravel().astype(np.int64), minlength=max(classes) + 1 if classes else 1)
    return {name: float(counts[i]) * voxel_ml for i, name in classes.items() if i < len(counts) and counts[i]}


def detect(vols: dict[str, float], cfg: SegConfig) -> dict[str, float]:
    out = {}
    for region, pat in _MARKERS.items():
        v = sum(ml for name, ml in vols.items() if pat.match(name))
        if v >= cfg.region_min_ml:
            out[region] = v
    ext = vols.get("body_extremities", 0.0)
    if ext >= cfg.limb_min_ml:
        out["limbs_soft_tissue"] = ext
    return out


def make_plan(base: str, vols: dict[str, float], cfg: SegConfig, modality: str) -> Plan:
    plan = Plan(base=base, regions=detect(vols, cfg))
    if cfg.profile == "minimal" and cfg.tasks is None:
        plan.reasons.append("profile 'minimal': no region subtasks, no MOOSE")
        return plan
    if cfg.tasks is not None:
        wanted = list(cfg.tasks)
        plan.reasons.append("subtasks given explicitly")
    elif modality == "MR":
        wanted = []
        plan.reasons.append("MR: only total_mr and body_mr have open MR weights in the allowlist")
    else:
        wanted = []
        for region in plan.regions:
            for t in REGION_SUBTASKS.get(region, []):
                if t == "liver_segments" and vols.get("liver", 0.0) < cfg.region_min_ml:
                    continue
                if t not in wanted:
                    wanted.append(t)
                    plan.reasons.append(f"{t}: region '{region}' in view")
    for t in cfg.extra_tasks:
        if t not in wanted:
            wanted.append(t)
            plan.reasons.append(f"{t}: requested")
    plan.subtasks = [f"totalsegmentator:{t}" for t in wanted]
    if cfg.moose and modality == "CT":
        limbs = any(r in plan.regions for r in ("upper_limb", "lower_limb", "limbs_soft_tissue"))
        if limbs and "clin_ct_peripheral_bones" in cfg.moose_models:
            plan.moose.append("moose:clin_ct_peripheral_bones")
            plan.reasons.append("MOOSE clin_ct_peripheral_bones: limbs in view")
        if "lumbar" in plan.regions and "clin_ct_body_composition" in cfg.moose_models:
            plan.moose.append("moose:clin_ct_body_composition")
            plan.reasons.append("MOOSE clin_ct_body_composition: lumbar trunk wall in view")
    return plan
