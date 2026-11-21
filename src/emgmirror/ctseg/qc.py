"""Per-structure QC flags on the fused label map.

Flags (strings in ``labels.json`` ``labels[].flags``):

* ``cut_by_scan_edge``: the structure touches a face of the image array, so it continues outside the scan
  (a cut femur is not a short femur). Volume checks are skipped for cut structures.
* ``volume_out_of_range``: complete structure outside a deliberately wide adult plausibility band
  (``VOLUME_ML``). Bands catch gross failures (a liver of 50 mL, a spleen of 3 L); they are not reference
  ranges and say nothing about disease.
* ``hu_out_of_range``: mean HU outside the band for the structure's tissue class (CT only, ``HU_BANDS``).
* ``very_small``: fewer than 10 voxels.
* ``fragmented``: for single solid organs and bones, the largest connected piece holds < 50 % of the voxels.
* ``near_metal``: metal (>= ``metal_hu``) within ``metal_margin_mm`` of the structure; streak artefacts.
* ``thick_slices`` (every structure) and ``thin_structure_thick_slices`` (vessels, ribs, nerves, airway
  walls, muscles of the neck) when the coarsest spacing exceeds ``warn_slice_mm``.
* ``mr_no_hu_check``: MR scan; HU checks were not applied.
* ``multi_source``: more than one model contributed voxels; ``part_of:<parent>``: refinement of a parent.
"""

from __future__ import annotations

import re

import numpy as np
import SimpleITK as sitk
from scipy import ndimage

from . import ontology
from .inputs import ScanInfo

# Wide adult plausibility bands (mL) for complete structures. Sources: textbook adult organ sizes, widened
# about 2x each way so that only gross segmentation failures fall outside.
VOLUME_ML: dict[str, tuple[float, float]] = {
    "liver": (500, 4500), "spleen": (25, 2000), "kidney_left": (50, 500), "kidney_right": (50, 500),
    "pancreas": (15, 250), "gallbladder": (1, 250), "stomach": (20, 3000), "heart": (200, 1800),
    "brain": (800, 2000), "urinary_bladder": (5, 1500), "prostate": (5, 300), "thyroid_gland": (2, 150),
    "adrenal_gland_left": (0.5, 40), "adrenal_gland_right": (0.5, 40), "esophagus": (3, 200),
    "trachea": (5, 150), "lung_upper_lobe_left": (150, 4000), "lung_lower_lobe_left": (150, 4000),
    "lung_upper_lobe_right": (150, 4000), "lung_middle_lobe_right": (40, 2000),
    "lung_lower_lobe_right": (150, 4000), "lung_left": (500, 5500), "lung_right": (500, 6000),
    "femur_left": (150, 1500), "femur_right": (150, 1500), "humerus_left": (50, 700), "humerus_right": (50, 700),
    "sacrum": (50, 600), "hip_left": (150, 1500), "hip_right": (150, 1500), "skull": (300, 1500),
    "eye_left": (3, 15), "eye_right": (3, 15), "mandible": (30, 250),
}

# Mean-HU bands per tissue class (CT). Contrast agent raises vessels, kidneys, bladder and bowel, so those
# bands are wide; the checks mainly catch a label on the wrong tissue (bone labelled as organ, air as fat).
HU_BANDS: dict[str, tuple[float, float]] = {
    "bone": (100, 1800), "cartilage": (-50, 600), "lung": (-1000, -350), "air": (-1024, 300),
    "fat": (-200, -20), "vessel": (-100, 800), "contrast_organ": (-950, 1200), "soft": (-150, 300),
}
_HU_SKIP = re.compile(r"^(body_.*|head|abdominal_cavity|thoracic_cavity|mediastinum|pericardium|skin)$")
_CONTRAST = re.compile(r"^(kidney_.*|urinary_bladder|gallbladder|colon|small_bowel|duodenum|stomach|esophagus|"
                       r"kidney_cyst_.*)$")
_LUNG = re.compile(r"^lung_(upper|middle|lower)_lobe_.*|^lung_(left|right)$")
_AIR = re.compile(r"^(trachea|lung_airways|larynx_air|sinus_.*|nasal_cavity_.*|nasopharynx|oropharynx|"
                  r"hypopharynx|auditory_canal_.*)$")
_CART = re.compile(r"^(costal_cartilages|thyroid_cartilage|cricoid_cartilage|intervertebral_discs|lung_airways_wall)$")
_THIN = re.compile(r"(artery|arteries|vein|veins|vena|rib_|optic_nerve|airways_wall|scalene|platysma|"
                   r"thyro|constrictor|styloid|eye_lens|carotid|jugular)")
_SOLID = re.compile(r"^(liver|spleen|kidney_(left|right)|pancreas|gallbladder|heart|brain|urinary_bladder|"
                    r"prostate|femur_.*|humerus_.*|scapula_.*|sacrum|hip_.*|sternum|vertebrae_[CTL]\d+|eye_.*)$")


def hu_band(name: str, kind: str) -> tuple[float, float] | None:
    if _HU_SKIP.match(name):
        return None
    if _LUNG.match(name):
        return HU_BANDS["lung"]
    if _AIR.match(name):
        return HU_BANDS["air"]
    if _CART.match(name):
        return HU_BANDS["cartilage"]
    if _CONTRAST.match(name):
        return HU_BANDS["contrast_organ"]
    if kind in ("bone", "fat", "vessel"):
        return HU_BANDS[kind]
    if kind == "heart":
        return HU_BANDS["vessel"]
    if name.startswith("teeth"):
        return (300, 3500)
    return HU_BANDS["soft"]


_FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")


def structure_qc(labels: sitk.Image, image: sitk.Image, scan: ScanInfo, *, label_sources: dict[int, dict[str, int]],
                 part_of: dict[int, str], warn_slice_mm: float = 3.0, metal_hu: float = 3000.0,
                 metal_margin_mm: float = 10.0) -> dict[int, dict]:
    """Return {canonical id: {"name", "flags", "stats"}} for every label present."""
    lab = sitk.GetArrayViewFromImage(labels)
    n = int(lab.max()) + 1
    flat = lab.ravel()
    counts = np.bincount(flat, minlength=n)
    is_ct = scan.modality == "CT"
    if is_ct:
        hu = sitk.GetArrayViewFromImage(image).ravel().astype(np.float64)
        sums = np.bincount(flat, weights=hu, minlength=n)
        sq = np.bincount(flat, weights=hu * hu, minlength=n)
        del hu
    voxel_ml = float(np.prod(labels.GetSpacing())) / 1000.0
    # scan-edge contact: labels present on each face of the array ([z, y, x] order)
    faces = {"x_min": lab[:, :, 0], "x_max": lab[:, :, -1], "y_min": lab[:, 0, :], "y_max": lab[:, -1, :],
             "z_min": lab[0], "z_max": lab[-1]}
    on_face = {f: set(np.unique(a).tolist()) - {0} for f, a in faces.items()}
    near_metal: set[int] = set()
    if is_ct and scan.metal_voxels:
        metal = sitk.GetArrayViewFromImage(image) >= metal_hu
        sl = ndimage.find_objects(metal.astype(np.uint8))[0]
        sp = np.array(labels.GetSpacing())[::-1]
        r = np.maximum(1, np.round(metal_margin_mm / sp)).astype(int)
        box = tuple(slice(max(0, s.start - k), min(dim, s.stop + k)) for s, k, dim in zip(sl, r, lab.shape))
        m = metal[box]
        st = np.zeros(tuple(2 * k + 1 for k in r), bool)
        zz, yy, xx = np.ogrid[tuple(slice(-k, k + 1) for k in r)]
        st[(zz * sp[0]) ** 2 + (yy * sp[1]) ** 2 + (xx * sp[2]) ** 2 <= metal_margin_mm ** 2] = True
        dil = ndimage.binary_dilation(m, structure=st)
        near_metal = set(np.unique(lab[box][dil]).tolist()) - {0}
    objs = ndimage.find_objects(lab)
    thick = scan.slice_mm > warn_slice_mm
    out: dict[int, dict] = {}
    for cid in np.flatnonzero(counts):
        cid = int(cid)
        if cid == 0:
            continue
        L = ontology.by_id().get(cid)
        name = L.name if L else f"label_{cid}"
        kind = L.kind if L else "other"
        nvox = int(counts[cid])
        flags: list[str] = []
        stats: dict = {"voxels": nvox, "volume_ml": round(nvox * voxel_ml, 3)}
        edges = [f for f in _FACES if cid in on_face[f]]
        stats["touches_edge"] = edges
        if edges:
            flags.append("cut_by_scan_edge")
        sl = objs[cid - 1] if cid - 1 < len(objs) else None
        if sl is not None:
            stats["bbox_zyx"] = [[s.start, s.stop] for s in sl]
        if is_ct:
            mean = sums[cid] / nvox
            sd = float(np.sqrt(max(0.0, sq[cid] / nvox - mean * mean)))
            stats.update(mean_hu=round(float(mean), 1), sd_hu=round(sd, 1))
            band = hu_band(name, kind)
            if band is not None:
                stats["hu_band"] = list(band)
                if not band[0] <= mean <= band[1]:
                    flags.append("hu_out_of_range")
        else:
            flags.append("mr_no_hu_check")
        if name in VOLUME_ML:
            stats["volume_band_ml"] = list(VOLUME_ML[name])
            lo, hi = VOLUME_ML[name]
            if not edges and not lo <= nvox * voxel_ml <= hi:
                flags.append("volume_out_of_range")
        if nvox < 10:
            flags.append("very_small")
        if _SOLID.match(name) and sl is not None and nvox >= 10:
            comp, nc = ndimage.label(lab[sl] == cid)
            if nc > 1:
                sizes = np.bincount(comp.ravel())[1:]
                frac = float(sizes.max()) / nvox
                stats.update(components=int(nc), largest_component_fraction=round(frac, 3))
                if frac < 0.5:
                    flags.append("fragmented")
            else:
                stats.update(components=1, largest_component_fraction=1.0)
        if cid in near_metal:
            flags.append("near_metal")
        if thick:
            flags.append("thick_slices")
            if _THIN.search(name):
                flags.append("thin_structure_thick_slices")
        srcs = label_sources.get(cid, {})
        stats["sources"] = srcs
        if len(srcs) > 1:
            flags.append("multi_source")
        if cid in part_of:
            flags.append(f"part_of:{part_of[cid]}")
        out[cid] = {"name": name, "flags": flags, "stats": stats}
    return out
