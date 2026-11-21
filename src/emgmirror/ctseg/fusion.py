"""Fuse several models' label maps into one canonical uint16 label map.

Rule (also in docs/ct_platform/S1_segmentation.md):

1. Every model label is mapped by *name* to the canonical table (:mod:`emgmirror.ctseg.ontology`). The same
   structure from two models (``skull`` from ``total`` and from ``craniofacial_structures``) is one id.
2. Every label gets a **rank** from its source (``LAYERS``), optionally overridden per label (``LABEL_RANK``):

   ====  ===========================================================  ================================
   rank  source                                                       role
   ====  ===========================================================  ================================
   10    ``body`` / ``body_mr`` (trunk, extremities)                  container, fills gaps only
   11    ``head`` from ``craniofacial_structures``                    container
   12    ``trunk_cavities`` (abdominal/thoracic cavity, ...)          container
   20    MOOSE ``clin_ct_body_composition`` (muscle, fat)             tissue
   35    ``headneck_muscles``                                         extra structures
   40    MOOSE ``clin_ct_peripheral_bones``                           limb bones not in ``total``
   45    ``head_glands_cavities``, ``headneck_bones_vessels``,         extra structures
         ``craniofacial_structures``
   50    ``total`` / ``total_mr``                                     primary model
   60    ``liver_segments`` (inside ``liver``), ``lung_vessels``       refinement of a parent
         (inside the lungs), ``vertebrae_pp_refined`` (inside
         vertebrae or background)
   ====  ===========================================================  ================================

3. A voxel takes a model's label when the model's rank is **strictly higher** than the rank already
   painted there (background has rank 0), and, for refinement layers, when the voxel currently holds one of
   the allowed parent labels. Sources are painted in ascending rank (plan order breaks ties), so equal ranks
   never overwrite each other: the first one wins.
4. Refinement labels carry a ``part_of:<parent>`` flag (``liver_segment_4`` is ``part_of:liver``) so a
   consumer can union them back into the parent.

Probabilities are not fused in S1 (no model writes them by default); ranks are a deterministic, documented
stand-in until per-structure validation Dice is available to rank sources per structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import SimpleITK as sitk

from . import ontology
from .runners import ModelResult


@dataclass(frozen=True)
class Layer:
    rank: int
    inside: re.Pattern | None = None  # parent label names a refinement may overwrite
    inside_background: bool = False  # refinement may also grow into background
    part_of: str | None = None


_LUNG = re.compile(r"^(lung_(upper|middle|lower)_lobe_(left|right)|lung_(left|right))$")
LAYERS: dict[str, Layer] = {
    "totalsegmentator:body": Layer(10),
    "totalsegmentator:body_mr": Layer(10),
    "totalsegmentator:trunk_cavities": Layer(12),
    "moose:clin_ct_body_composition": Layer(20),
    "totalsegmentator:headneck_muscles": Layer(35),
    "moose:clin_ct_peripheral_bones": Layer(40),
    "totalsegmentator:head_glands_cavities": Layer(45),
    "totalsegmentator:headneck_bones_vessels": Layer(45),
    "totalsegmentator:craniofacial_structures": Layer(45),
    "totalsegmentator:total": Layer(50),
    "totalsegmentator:total_mr": Layer(50),
    "totalsegmentator:liver_segments": Layer(60, re.compile(r"^liver$"), part_of="liver"),
    "totalsegmentator:lung_vessels": Layer(60, _LUNG, part_of="lung"),
    "totalsegmentator:vertebrae_pp_refined": Layer(60, re.compile(r"^vertebrae_[CTL]\d+$"), inside_background=True),
}
LABEL_RANK: dict[tuple[str, str], int] = {("totalsegmentator:craniofacial_structures", "head"): 11}


@dataclass
class Fused:
    labels: sitk.Image  # uint16 canonical ids, same grid as the inputs
    sources: list[str]
    label_sources: dict[int, dict[str, int]]  # canonical id -> {source: voxels kept}
    part_of: dict[int, str]
    discarded: dict[str, int] = field(default_factory=dict)  # source -> predicted voxels not kept


def layer(source: str) -> Layer:
    if source not in LAYERS:
        raise KeyError(f"no fusion layer defined for '{source}'")
    return LAYERS[source]


def fuse(results: list[ModelResult]) -> Fused:
    if not results:
        raise ValueError("nothing to fuse")
    ref = results[0].labels
    shape = sitk.GetArrayViewFromImage(ref).shape
    fused = np.zeros(shape, np.uint16)
    rank = np.zeros(shape, np.uint8)
    src = np.zeros(shape, np.uint8)
    table = ontology.table()
    order = sorted(range(len(results)), key=lambda k: (layer(results[k].source).rank, k))
    part_of: dict[int, str] = {}
    discarded: dict[str, int] = {}
    for k in order:
        r = results[k]
        if r.labels.GetSize() != ref.GetSize():
            raise ValueError(f"{r.source}: label map grid {r.labels.GetSize()} differs from {ref.GetSize()}")
        L = layer(r.source)
        arr = sitk.GetArrayViewFromImage(r.labels)
        n = max(int(arr.max()), max(r.classes) if r.classes else 0) + 1
        lut = np.zeros(n, np.uint16)
        rlut = np.zeros(n, np.uint8)
        for lid, name in r.classes.items():
            if lid >= n:
                continue
            lab = ontology.label(name)
            lut[lid] = lab.id
            rlut[lid] = LABEL_RANK.get((r.source, name), L.rank)
            if L.part_of:
                part_of[lab.id] = L.part_of
        canon = lut[arr]
        new_rank = rlut[arr]
        write = (canon > 0) & (new_rank > rank)
        if L.inside is not None:
            ok = np.zeros(65536, bool)
            ok[[lab.id for lab in table.values() if L.inside.match(lab.name)]] = True
            ok[0] = L.inside_background
            write &= ok[fused]
        discarded[r.source] = int(np.count_nonzero(canon > 0) - np.count_nonzero(write))
        fused[write] = canon[write]
        rank[write] = new_rank[write]
        src[write] = k + 1
        del canon, new_rank, write
    nsrc = len(results) + 1
    key = fused.astype(np.int64).ravel() * nsrc + src.ravel()
    counts = np.bincount(key)
    label_sources: dict[int, dict[str, int]] = {}
    for kk in np.flatnonzero(counts):
        cid, s = divmod(int(kk), nsrc)
        if cid == 0 or s == 0:
            continue
        label_sources.setdefault(cid, {})[results[s - 1].source] = int(counts[kk])
    out = sitk.GetImageFromArray(fused)
    out.CopyInformation(ref)
    return Fused(out, [r.source for r in results], label_sources, part_of, discarded)
