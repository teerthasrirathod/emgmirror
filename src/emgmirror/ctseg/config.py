"""Engine settings. Every field is recorded in ``provenance.json``."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Region -> open TotalSegmentator subtasks run when that region is in the field of view.
REGION_SUBTASKS: dict[str, list[str]] = {
    "head": ["craniofacial_structures", "head_glands_cavities"],
    "neck": ["headneck_bones_vessels", "headneck_muscles"],
    "thorax": ["lung_vessels", "trunk_cavities"],
    "abdomen": ["liver_segments", "trunk_cavities"],
    "spine": ["vertebrae_pp_refined"],
}

PROFILES = ("minimal", "standard")


@dataclass
class SegConfig:
    """Settings for one run.

    profile: ``minimal`` runs only the base model (``total``/``total_mr``) plus ``body``; ``standard`` adds
    the region subtasks and MOOSE. ``tasks``: explicit subtask list overriding region selection (None = by
    region). ``fast``: TotalSegmentator 3 mm models where they exist (``total``, ``body``); the subtasks have
    no fast model and run at their native resolution."""

    build: str = "research"  # "research" | "device" (REGULATORY_PATH.md section 4)
    profile: str | None = None  # default: "standard" (research), "minimal" (device)
    fast: bool = False
    device: str = "cpu"  # "cpu" | "gpu" | "mps"
    modality: str = "auto"  # "auto" | "ct" | "mr"
    tasks: list[str] | None = None
    extra_tasks: list[str] = field(default_factory=list)
    moose: bool | None = None  # default: True (research), False (device; explicit True fails the licence check)
    moose_models: list[str] = field(default_factory=lambda: ["clin_ct_peripheral_bones", "clin_ct_body_composition"])
    cads: dict | None = None  # {"license": "open"}; CADS is not run in S1, only validated
    # Region detection: a region counts as present when its marker structures add up to this volume.
    region_min_ml: float = 5.0
    limb_min_ml: float = 50.0  # body_extremities volume that triggers MOOSE peripheral bones
    # Input caps (voxel counts). Above ``max_voxels`` the image is resampled to fit before it is loaded in
    # full; above ``refuse_voxels`` it is refused from the header alone.
    max_voxels: int = 512 * 512 * 1000
    refuse_voxels: int = 2_000_000_000
    warn_slice_mm: float = 3.0
    refuse_slice_mm: float = 5.0
    metal_hu: float = 3000.0
    metal_min_ml: float = 0.1
    keep_intermediate: bool = False
    totalseg_home: str | None = None  # TOTALSEG_HOME_DIR; default: <out>/.totalseg is NOT used, see runners
    weights_dir: str | None = None  # TOTALSEG_WEIGHTS_PATH, checked against the allowlist
    weights_manifest: str | None = None  # device build: pinned weight hashes (default: resources/device_weights.json)

    def __post_init__(self) -> None:
        if self.build not in ("research", "device"):
            raise ValueError("build must be 'research' or 'device'")
        device = self.build == "device"
        if self.profile is None:
            self.profile = "minimal" if device else "standard"
        if self.moose is None:
            self.moose = not device
        if self.profile not in PROFILES:
            raise ValueError(f"profile must be one of {PROFILES}, not '{self.profile}'")
        if self.modality not in ("auto", "ct", "mr"):
            raise ValueError("modality must be auto, ct or mr")
        if self.device not in ("cpu", "gpu", "mps"):
            raise ValueError("device must be cpu, gpu or mps")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_file(cls, path: Path | str) -> "SegConfig":
        return cls(**json.loads(Path(path).read_text()))

    def licence_view(self, planned_tasks: list[str] | None = None) -> dict:
        """The mapping :func:`emgmirror.ctseg.allowlist.check_config` validates."""
        tasks = list(planned_tasks if planned_tasks is not None else (self.tasks or []))
        if self.build == "device" and self.profile == "standard":
            tasks.append("<region subtasks (profile 'standard')>")
        return {"tasks": tasks, "extra_tasks": self.extra_tasks, "build": self.build,
                "moose_models": self.moose_models if self.moose else [], "cads": self.cads}
