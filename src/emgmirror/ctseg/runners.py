"""Model runners. Each returns a label map on (or resampled onto) the input grid plus its class map.

The model packages are imported lazily, so the engine and its tests work without them installed. Tests pass a
fake runner with the same interface (:class:`Runner`).

TotalSegmentator usage statistics are switched off before every call: ``send_usage_stats`` is written as
``false`` into ``$TOTALSEG_HOME_DIR/config.json`` (the file TotalSegmentator reads before deciding to send).
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import SimpleITK as sitk

from . import allowlist, ontology


@dataclass
class ModelResult:
    source: str  # "totalsegmentator:total"
    labels: sitk.Image  # integer label map, local ids of ``classes``
    classes: dict[int, str]
    seconds: float = 0.0
    meta: dict = field(default_factory=dict)


class Runner(Protocol):
    def run(self, source: str, image_path: Path, image: sitk.Image, workdir: Path) -> ModelResult: ...

    def versions(self) -> dict[str, str | None]: ...


def _version(pkg: str) -> str | None:
    try:
        return importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return None


def disable_totalseg_usage_stats(home: Path | str | None = None) -> Path:
    """Write ``send_usage_stats: false`` into TotalSegmentator's config.json; return the file path.

    Sets ``TOTALSEG_HOME_DIR`` when ``home`` is given. Keeps any other keys (e.g. the random install id) but
    never a licence key: one would unlock non-commercial tasks, so it is removed."""
    if home is not None:
        os.environ["TOTALSEG_HOME_DIR"] = str(home)
    root = Path(os.environ.get("TOTALSEG_HOME_DIR") or (Path.home() / ".totalsegmentator"))
    root.mkdir(parents=True, exist_ok=True)
    cfg_path = root / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            cfg = {}
    cfg.pop("license_number", None)
    cfg.setdefault("totalseg_id", "totalseg_ctseg")
    cfg.setdefault("prediction_counter", 0)
    cfg["send_usage_stats"] = False
    cfg_path.write_text(json.dumps(cfg, indent=4))
    return cfg_path


def usage_stats_disabled(home: Path | str | None = None) -> bool:
    root = Path(home or os.environ.get("TOTALSEG_HOME_DIR") or (Path.home() / ".totalsegmentator"))
    try:
        return json.loads((root / "config.json").read_text()).get("send_usage_stats") is False
    except (OSError, json.JSONDecodeError):
        return False


def _onto(img: sitk.Image, ref: sitk.Image) -> sitk.Image:
    """Nearest-neighbour resample of a label map onto ``ref`` when the grids differ."""
    same = (img.GetSize() == ref.GetSize()
            and np.allclose(img.GetSpacing(), ref.GetSpacing(), atol=1e-4)
            and np.allclose(img.GetOrigin(), ref.GetOrigin(), atol=1e-3)
            and np.allclose(img.GetDirection(), ref.GetDirection(), atol=1e-5))
    if same:
        out = sitk.Cast(img, sitk.sitkUInt16)
        out.CopyInformation(ref)
        return out
    return sitk.Resample(sitk.Cast(img, sitk.sitkUInt16), ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0,
                         sitk.sitkUInt16)


class TotalSegmentatorRunner:
    def __init__(self, fast: bool = False, device: str = "cpu", home: str | None = None,
                 weights_dir: str | None = None, report_dir: Path | None = None) -> None:
        self.fast, self.device, self.home, self.report_dir = fast, device, home, report_dir
        if weights_dir:
            os.environ["TOTALSEG_WEIGHTS_PATH"] = str(weights_dir)
        allowlist.check_weights_dir(os.environ.get("TOTALSEG_WEIGHTS_PATH"))

    def versions(self) -> dict[str, str | None]:
        return {"TotalSegmentator": _version("TotalSegmentator"), "nnunetv2": _version("nnunetv2"),
                "torch": _version("torch")}

    def run(self, source: str, image_path: Path, image: sitk.Image, workdir: Path) -> ModelResult:
        src, task = source.split(":", 1)
        assert src == "totalsegmentator"
        allowlist.allowed(source)
        disable_totalseg_usage_stats(self.home)
        from totalsegmentator.python_api import totalsegmentator  # optional dependency

        fast = self.fast and task in ("total", "total_mr", "body", "body_mr")
        report = None
        if self.report_dir is not None:
            report = Path(self.report_dir) / f"totalsegmentator_{task}.json"
        t0 = time.time()
        out = Path(workdir) / f"ts_{task}.nii.gz"
        totalsegmentator(str(image_path), str(out), ml=True, task=task, fast=fast, device=self.device,
                         quiet=True, report=str(report) if report else None)
        seconds = time.time() - t0
        allowlist.check_weights_dir(os.environ.get("TOTALSEG_WEIGHTS_PATH"))  # nothing unexpected downloaded
        lab = _onto(sitk.ReadImage(str(out)), image)
        meta = {"task": task, "fast": fast, "device": self.device}
        if report and report.exists():
            meta["report"] = report.name
        return ModelResult(source, lab, ontology.classes(source), seconds, meta)


class MooseRunner:
    def __init__(self, device: str = "cpu") -> None:
        self.device = {"gpu": "cuda", "cpu": "cpu", "mps": "mps"}[device]

    def versions(self) -> dict[str, str | None]:
        return {"moosez": _version("moosez")}

    def run(self, source: str, image_path: Path, image: sitk.Image, workdir: Path) -> ModelResult:
        src, model = source.split(":", 1)
        assert src == "moose"
        allowlist.allowed(source)
        from moosez import moose  # optional dependency

        t0 = time.time()
        out_dir = Path(tempfile.mkdtemp(prefix=f"moose_{model}_", dir=workdir))
        files, models = moose(str(image_path), [model], str(out_dir), self.device)
        seconds = time.time() - t0
        lab = _onto(sitk.ReadImage(str(files[-1])), image)
        return ModelResult(source, lab, ontology.classes(source), seconds, {"model": model, "device": self.device})


class DefaultRunner:
    """Dispatches ``totalsegmentator:*`` and ``moose:*`` to the real runners."""

    def __init__(self, fast: bool = False, device: str = "cpu", totalseg_home: str | None = None,
                 weights_dir: str | None = None, report_dir: Path | None = None) -> None:
        self.ts = TotalSegmentatorRunner(fast, device, totalseg_home, weights_dir, report_dir)
        self._moose: MooseRunner | None = None
        self.device = device

    def versions(self) -> dict[str, str | None]:
        return {**self.ts.versions(), "moosez": _version("moosez")}

    def run(self, source: str, image_path: Path, image: sitk.Image, workdir: Path) -> ModelResult:
        if source.startswith("totalsegmentator:"):
            return self.ts.run(source, image_path, image, workdir)
        if source.startswith("moose:"):
            self._moose = self._moose or MooseRunner(self.device)
            return self._moose.run(source, image_path, image, workdir)
        if source.startswith("cads:"):
            raise NotImplementedError("CADS is allowlisted (open weights only) but not wired into S1")
        raise allowlist.LicenceError(f"unknown model source '{source}'")
