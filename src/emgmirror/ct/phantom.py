"""Tiny synthetic forearm-like CT with known geometry, used by the tests.

Two hollow bone tubes (cortical shell, marrow core) inside an elliptical limb of
muscle wrapped in fat, an optional metal plate on bone 0, and an optional open
plastic cradle that does not touch the limb. Built on a 3x supersampled grid and
averaged down so edges carry realistic partial volume.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import SimpleITK as sitk

from . import labels as L

HU = {"air": -1000, "fat": -100, "muscle": 55, "cortical": 1400, "marrow": 40, "metal": 8000, "cradle": 150}


@dataclass
class Phantom:
    image: sitk.Image  # int16 HU
    truth: sitk.Image  # uint8 labels (majority vote of the fine grid)
    analytic: dict = field(default_factory=dict)  # exact volumes (mm3) and sizes (mm)


def make_phantom(shape=(40, 80, 80), spacing=(0.9, 0.9, 1.5), origin=(-30.0, 12.0, 100.0),
                 noise_hu: float = 12.0, metal: bool = True, cradle: bool = True, seed: int = 0) -> Phantom:
    """`shape` is (nz, ny, nx); `spacing` and `origin` are (x, y, z) in mm."""
    f = 3
    nz, ny, nx = shape
    sx, sy, sz = spacing
    z = (np.arange(nz * f) + 0.5) / f * sz
    y = (np.arange(ny * f) + 0.5) / f * sy
    x = (np.arange(nx * f) + 0.5) / f * sx
    Z, Y, X = np.meshgrid(z, y, x, indexing="ij", sparse=True)
    cx, cy = nx * sx / 2, ny * sy / 2
    z0, z1 = 0.1 * nz * sz, 0.9 * nz * sz  # bones are shorter than the volume
    limb_a, limb_b, fat_t = 0.36 * nx * sx, 0.30 * ny * sy, 3.0
    bones = [  # centre offset (x, y), outer radius, cortical thickness
        ((-0.15 * nx * sx, 0.0), 6.0, 2.0),
        ((0.15 * nx * sx, 1.0), 5.0, 1.8),
    ]

    lab = np.zeros((nz * f, ny * f, nx * f), np.uint8)
    e = ((X - cx) / limb_a) ** 2 + ((Y - cy) / limb_b) ** 2
    lab[np.broadcast_to(e <= 1, lab.shape)] = L.FAT
    e_in = ((X - cx) / (limb_a - fat_t)) ** 2 + ((Y - cy) / (limb_b - fat_t)) ** 2
    lab[np.broadcast_to(e_in <= 1, lab.shape)] = L.MUSCLE
    inz = (Z >= z0) & (Z <= z1)
    analytic = {"bone_length_mm": z1 - z0, "bones": []}
    for k, ((ox, oy), r, t) in enumerate(bones):
        d2 = (X - cx - ox) ** 2 + (Y - cy - oy) ** 2
        lab[(d2 <= r * r) & inz] = L.cortical(k)
        lab[(d2 <= (r - t) ** 2) & inz] = L.medullary(k)
        analytic["bones"].append({"outer_radius_mm": r, "cortical_thickness_mm": t,
                                  "volume_mm3": float(np.pi * r * r * (z1 - z0)),
                                  "medullary_volume_mm3": float(np.pi * (r - t) ** 2 * (z1 - z0))})
    if metal:
        (ox, oy), r, _ = bones[0]
        plate = (np.abs(X - cx - ox) <= 3.0) & (Y - cy - oy <= -r) & (Y - cy - oy >= -r - 2.0) & \
                (Z >= z0 + 0.25 * (z1 - z0)) & (Z <= z0 + 0.75 * (z1 - z0))
        lab[plate] = L.METAL
        analytic["metal_volume_mm3"] = float(6.0 * 2.0 * 0.5 * (z1 - z0))
    hu = np.full(lab.shape, HU["air"], np.float32)
    hu[lab == L.FAT] = HU["fat"]
    hu[lab == L.MUSCLE] = HU["muscle"]
    hu[lab == L.METAL] = HU["metal"]
    for k in range(len(bones)):
        hu[lab == L.cortical(k)] = HU["cortical"]
        hu[lab == L.medullary(k)] = HU["marrow"]
    if cradle:
        rr = np.sqrt((X - cx) ** 2 + (Y - cy + 0.05 * ny * sy) ** 2)
        shell = (rr >= 0.46 * nx * sx) & (rr <= 0.46 * nx * sx + 2.0) & (Y > cy)
        hu[np.broadcast_to(shell, hu.shape)] = HU["cradle"]

    hu = hu.reshape(nz, f, ny, f, nx, f).mean(axis=(1, 3, 5))
    truth = lab[f // 2::f, f // 2::f, f // 2::f].copy()
    rng = np.random.default_rng(seed)
    hu = hu + rng.normal(0, noise_hu, hu.shape)
    image = sitk.GetImageFromArray(np.clip(np.rint(hu), -1024, 32000).astype(np.int16))
    t_img = sitk.GetImageFromArray(truth)
    for im in (image, t_img):
        im.SetSpacing(tuple(float(v) for v in spacing))
        im.SetOrigin(tuple(float(v) for v in origin))
    analytic["limb_semi_axes_mm"] = (limb_a, limb_b)
    analytic["fat_thickness_mm"] = fat_t
    return Phantom(image, t_img, analytic)
