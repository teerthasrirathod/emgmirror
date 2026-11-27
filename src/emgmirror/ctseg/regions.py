"""Region evidence and the run plan derived from the base model's output.

Thresholded ``regions`` are retained for summary/QC. Planning uses any non-zero marker evidence so a small
field of view cannot silently suppress a relevant task; partial structures are still passed to QC, which
marks surfaces cut by the scan edge.

Limbs: ``upper_limb``/``lower_limb`` come from humerus/femur and from ``body_extremities``. Any limb, or a
``body_extremities`` volume of at least ``limb_min_ml``, triggers MOOSE ``clin_ct_peripheral_bones`` (the only
commercial-safe source of forearm, hand, lower-leg and foot bones). The trunk wall (lumbar spine in view)
triggers MOOSE ``clin_ct_body_composition`` (muscle / subcutaneous / visceral fat), whose own workflow crops
to the lumbar region.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
import json

import numpy as np

from .config import SegConfig


@lru_cache(maxsize=1)
def _planning_catalog() -> dict:
    return json.loads(resources.files("emgmirror.ctseg.resources").joinpath("planning_catalog.json").read_text())


@lru_cache(maxsize=1)
def _markers() -> dict[str, tuple[re.Pattern, ...]]:
    return {region: tuple(re.compile(pattern) for pattern in entry["markers"])
            for region, entry in _planning_catalog()["regions"].items()}


@dataclass
class Plan:
    base: str  # "totalsegmentator:total" | "totalsegmentator:total_mr"
    regions: dict[str, float] = field(default_factory=dict)  # region -> marker volume (mL)
    planning_regions: dict[str, float] = field(default_factory=dict)  # any positive marker evidence
    subtasks: list[str] = field(default_factory=list)  # "totalsegmentator:<task>"
    moose: list[str] = field(default_factory=list)  # "moose:<model>"
    reasons: list[str] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return [*self.subtasks, *self.moose]

    def to_dict(self) -> dict:
        return {"base": self.base, "regions": {k: round(v, 1) for k, v in self.regions.items()},
                "planning_regions": {k: round(v, 1) for k, v in self.planning_regions.items()},
                "subtasks": self.subtasks, "moose": self.moose, "reasons": self.reasons}


def volumes_ml(labels: np.ndarray, classes: dict[int, str], voxel_ml: float) -> dict[str, float]:
    counts = np.bincount(labels.ravel().astype(np.int64), minlength=max(classes) + 1 if classes else 1)
    return {name: float(counts[i]) * voxel_ml for i, name in classes.items() if i < len(counts) and counts[i]}


def detect(vols: dict[str, float], cfg: SegConfig) -> dict[str, float]:
    out = {}
    for region, patterns in _markers().items():
        v = sum(ml for name, ml in vols.items() if any(pat.match(name) for pat in patterns))
        if v >= cfg.region_min_ml:
            out[region] = v
    ext = vols.get("body_extremities", 0.0)
    if ext >= cfg.limb_min_ml:
        out["limbs_soft_tissue"] = ext
    return out


def _planning_evidence(vols: dict[str, float]) -> dict[str, float]:
    """Use any non-zero catalog marker to select tasks; volume thresholds are reporting-only."""
    out = {}
    for region, patterns in _markers().items():
        v = sum(ml for name, ml in vols.items() if any(pat.match(name) for pat in patterns))
        if v > 0:
            out[region] = v
    ext = vols.get("body_extremities", 0.0)
    if ext > 0:
        out["limbs_soft_tissue"] = ext
    return out


def make_plan(base: str, vols: dict[str, float], cfg: SegConfig, modality: str) -> Plan:
    plan = Plan(base=base, regions=detect(vols, cfg), planning_regions=_planning_evidence(vols))
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
        for region in plan.planning_regions:
            for source, task in _planning_catalog()["tasks"].items():
                if not source.startswith("totalsegmentator:") or region not in task.get("regions", []):
                    continue
                if modality not in task.get("modalities", []):
                    continue
                required_marker = task.get("requires_marker")
                if required_marker and vols.get(required_marker, 0.0) <= 0:
                    continue
                name = source.split(":", 1)[1]
                if name not in wanted:
                    wanted.append(name)
                    plan.reasons.append(f"{name}: non-zero anatomy marker for region '{region}'")
    for t in cfg.extra_tasks:
        if t not in wanted:
            wanted.append(t)
            plan.reasons.append(f"{t}: requested")
    plan.subtasks = [f"totalsegmentator:{t}" for t in wanted]
    if cfg.moose:
        for source, task in _planning_catalog()["tasks"].items():
            if not source.startswith("moose:") or modality not in task.get("modalities", []):
                continue
            name = source.split(":", 1)[1]
            if name not in cfg.moose_models:
                continue
            matching_regions = set(task.get("regions", [])) & set(plan.planning_regions)
            scheduled_by_profile = cfg.profile in task.get("profiles", [])
            policy = task.get("profile_policy")
            should_schedule = bool(matching_regions)
            if policy == "always_or_any_region":
                should_schedule = should_schedule or scheduled_by_profile
            if should_schedule:
                plan.moose.append(source)
                reason = (f"profile '{cfg.profile}' or region evidence {sorted(matching_regions)}"
                          if scheduled_by_profile else f"region evidence {sorted(matching_regions)}")
                plan.reasons.append(f"{source}: {reason}")
    return plan
