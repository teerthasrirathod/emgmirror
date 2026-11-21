"""Helpers for the ctseg tests: a small synthetic CT and a fake model runner (no weights needed)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import SimpleITK as sitk

from emgmirror.ctseg import ontology
from emgmirror.ctseg.runners import ModelResult

SPACING = (1.5, 1.5, 2.0)
ORIGIN = (-60.0, -50.0, 100.0)


def local_id(source: str, name: str) -> int:
    return next(k for k, v in ontology.classes(source).items() if v == name)


def make_phantom(metal: bool = False, shape=(40, 64, 80)) -> tuple[sitk.Image, dict[str, np.ndarray]]:
    """[z, y, x] phantom: air, a body box, a liver blob, two lung blocks, a femur rod cut by the bottom edge."""
    z, y, x = np.indices(shape)
    hu = np.full(shape, -1000, np.int16)
    truth: dict[str, np.ndarray] = {}
    body = (np.abs(y - 32) < 26) & (np.abs(x - 40) < 34)
    hu[body] = -80  # fat
    truth["body_trunc"] = body
    liver = ((z - 12) / 9.0) ** 2 + ((y - 30) / 14.0) ** 2 + ((x - 25) / 14.0) ** 2 <= 1
    hu[liver] = 60
    truth["liver"] = liver
    lung_l = (z >= 24) & (z < 38) & (np.abs(y - 30) < 12) & (x >= 44) & (x < 60)
    lung_r = (z >= 24) & (z < 38) & (np.abs(y - 30) < 12) & (x >= 20) & (x < 36)
    hu[lung_l | lung_r] = -850
    truth["lung_upper_lobe_left"], truth["lung_upper_lobe_right"] = lung_l, lung_r
    femur = (z < 10) & ((y - 34) ** 2 + (x - 58) ** 2 <= 49)
    hu[femur] = 900
    truth["femur_left"] = femur
    if metal:
        bead = (z - 12) ** 2 + (y - 30) ** 2 + (x - 42) ** 2 <= 4
        hu[bead] = 3500
    img = sitk.GetImageFromArray(hu)
    img.SetSpacing(SPACING)
    img.SetOrigin(ORIGIN)
    return img, truth


class FakeRunner:
    """Returns label maps built from the phantom truth; records every call."""

    def __init__(self, truth: dict[str, np.ndarray], fail: set[str] | None = None,
                 extra: dict[str, dict[str, np.ndarray]] | None = None) -> None:
        self.truth, self.fail, self.calls = truth, fail or set(), []
        self.extra = extra or {}

    def versions(self):
        return {"fake": "1"}

    def _map(self, source: str, masks: dict[str, np.ndarray], image: sitk.Image) -> ModelResult:
        shape = sitk.GetArrayViewFromImage(image).shape
        arr = np.zeros(shape, np.uint16)
        for name, m in masks.items():
            arr[m] = local_id(source, name)
        img = sitk.GetImageFromArray(arr)
        img.CopyInformation(image)
        return ModelResult(source, img, ontology.classes(source), 0.01, {"fake": True})

    def run(self, source: str, image_path: Path, image: sitk.Image, workdir: Path) -> ModelResult:
        self.calls.append(source)
        if source in self.fail:
            raise RuntimeError(f"simulated failure of {source}")
        if source in self.extra:
            return self._map(source, self.extra[source], image)
        t = self.truth
        if source in ("totalsegmentator:total", "totalsegmentator:total_mr"):
            names = ["liver", "lung_upper_lobe_left", "lung_upper_lobe_right", "femur_left"]
            if source.endswith("_mr"):
                return self._map(source, {"liver": t["liver"], "femur_left": t["femur_left"],
                                          "lung_left": t["lung_upper_lobe_left"]}, image)
            return self._map(source, {n: t[n] for n in names}, image)
        if source in ("totalsegmentator:body", "totalsegmentator:body_mr"):
            return self._map(source, {"body_trunc": t["body_trunc"]}, image)
        if source == "totalsegmentator:liver_segments":
            z = np.indices(t["liver"].shape)[0]
            grown = np.zeros_like(t["liver"])
            grown[8:16, 20:40, 10:40] = True  # deliberately larger than the liver
            return self._map(source, {"liver_segment_1": grown & (z < 12), "liver_segment_2": grown & (z >= 12)}, image)
        return self._map(source, {}, image)
