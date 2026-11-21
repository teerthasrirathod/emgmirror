"""Device build: intended-use structures, pinned model weights, model fingerprint.

The device build follows docs/ct_platform/REGULATORY_PATH.md section 2 (first intended use: adult CT, bony
anatomy for pre-operative orthopaedic and trauma planning) and section 4 (device build = TotalSegmentator's
Apache ``total`` task only, usage statistics off, locked model registry with hashes). Until a licence or a
permitted clinical investigation exists, every output is still labelled research use, and every device-build
result is marked ``not_approved`` until a qualified clinician reviews it.

Weights are pinned in ``resources/device_weights.json``: one SHA-256 per weight file of each nnU-Net dataset
the ``total`` task uses, plus a tree hash per dataset. The engine verifies them **before** running (a missing
or changed file stops the job) and writes the fingerprint into ``provenance.json`` in both builds, so a model
change between two runs is visible by comparing provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
from importlib import resources
from pathlib import Path

DEVICE_MODELS = ("totalsegmentator:total",)

# Intended use v1 -> TotalSegmentator `total` labels (REGULATORY_PATH.md section 2).
INTENDED_USE: dict[str, list[str]] = {
    "pelvis_acetabulum": ["hip_left", "hip_right", "sacrum"],
    "spine": ([f"vertebrae_C{i}" for i in range(1, 8)] + [f"vertebrae_T{i}" for i in range(1, 13)]
              + [f"vertebrae_L{i}" for i in range(1, 6)] + ["vertebrae_S1"]),
    "shoulder_girdle": ["scapula_left", "scapula_right", "clavicula_left", "clavicula_right"],
    "proximal_femur": ["femur_left", "femur_right"],
    "humerus": ["humerus_left", "humerus_right"],
}
INTENDED_USE_TEXT = ("Adult CT to 3D surface models of bony anatomy (pelvis/acetabulum, spine, shoulder girdle, "
                     "proximal femur and humerus) for pre-operative planning in orthopaedic and trauma surgery, after "
                     "review, editing and approval by a qualified clinician. Not for diagnosis, children, MR or "
                     "cone-beam CT, intra-operative navigation or registration.")
APPROVAL_NOTE = ("NOT APPROVED: these models must be reviewed, edited if needed and approved by a qualified clinician "
                 "before any use. Research use, not for diagnosis or surgical planning, until licensed.")


def device_structures() -> list[str]:
    return [n for names in INTENDED_USE.values() for n in names]


class ModelIntegrityError(RuntimeError):
    """The model weights on disk do not match the pinned device manifest (missing, changed or wrong version)."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def weights_dir_default(cfg_weights_dir: str | None = None, totalseg_home: str | None = None) -> Path:
    if cfg_weights_dir:
        return Path(cfg_weights_dir)
    if os.environ.get("TOTALSEG_WEIGHTS_PATH"):
        return Path(os.environ["TOTALSEG_WEIGHTS_PATH"])
    home = totalseg_home or os.environ.get("TOTALSEG_HOME_DIR") or (Path.home() / ".totalsegmentator")
    return Path(home) / "nnunet" / "results"


def dataset_files(weights_dir: Path, dataset_id: int) -> dict[str, str]:
    """{relative path: sha256} for every file of ``Dataset<id>_*`` (zips and macOS junk ignored)."""
    out: dict[str, str] = {}
    for d in sorted(Path(weights_dir).glob(f"Dataset{dataset_id:03d}_*")):
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and "__MACOSX" not in f.parts and not f.name.startswith("._") and f.name != ".DS_Store":
                out[str(f.relative_to(weights_dir))] = _sha256(f)
    return out


def tree_hash(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(f"{rel}:{files[rel]}\n".encode())
    return h.hexdigest()


def total_dataset_ids(fast: bool) -> list[int]:
    return [297] if fast else [291, 292, 293, 294, 295]


def fingerprint(weights_dir: Path, dataset_ids: list[int], ts_version: str | None) -> dict:
    """Model fingerprint for provenance: per-dataset tree hashes + one overall hash."""
    per = {}
    for i in dataset_ids:
        files = dataset_files(weights_dir, i)
        per[str(i)] = tree_hash(files) if files else None
    overall = hashlib.sha256(json.dumps({"ts": ts_version, "datasets": per}, sort_keys=True).encode()).hexdigest()
    return {"totalsegmentator_version": ts_version, "datasets": per, "sha256": overall}


def load_manifest(path: Path | str | None = None) -> dict:
    if path:
        return json.loads(Path(path).read_text())
    return json.loads(resources.files("emgmirror.ctseg.resources").joinpath("device_weights.json").read_text())


def verify(weights_dir: Path, dataset_ids: list[int], ts_version: str | None, manifest: dict) -> dict:
    """Raise :class:`ModelIntegrityError` unless every pinned file is present with its pinned hash.

    Extra files inside a pinned dataset folder are also an error (a swapped checkpoint under a new name)."""
    problems = []
    if manifest.get("totalsegmentator_version") and ts_version != manifest["totalsegmentator_version"]:
        problems.append(f"TotalSegmentator {ts_version} installed, {manifest['totalsegmentator_version']} pinned")
    for i in dataset_ids:
        pinned = manifest.get("datasets", {}).get(str(i))
        if pinned is None:
            problems.append(f"dataset {i} is not in the device manifest")
            continue
        files = dataset_files(weights_dir, i)
        if not files:
            problems.append(f"dataset {i} weights are missing from {weights_dir} (device images must ship them)")
            continue
        want = pinned["files"]
        for rel, h in want.items():
            if rel not in files:
                problems.append(f"missing {rel}")
            elif files[rel] != h:
                problems.append(f"changed {rel}")
        for rel in files:
            if rel not in want:
                problems.append(f"unexpected file {rel}")
    if problems:
        raise ModelIntegrityError("device model weights do not match the pinned manifest: " + "; ".join(problems[:10]))
    return fingerprint(weights_dir, dataset_ids, ts_version)


def build_manifest(weights_dir: Path, dataset_ids: list[int], ts_version: str) -> dict:
    """Used by scripts/ctseg_pin_device_weights.py to (re)write resources/device_weights.json."""
    data = {"about": "Pinned TotalSegmentator `total` weights for the device build. Regenerate only through a "
                     "controlled change (new SOUP register entry, re-verification).",
            "totalsegmentator_version": ts_version, "datasets": {}}
    for i in dataset_ids:
        files = dataset_files(weights_dir, i)
        if not files:
            raise FileNotFoundError(f"dataset {i} not found in {weights_dir}")
        data["datasets"][str(i)] = {"tree_sha256": tree_hash(files), "files": files}
    return data
