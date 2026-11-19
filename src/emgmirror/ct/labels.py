"""Label ids used in labels.nii.gz.

0            background (air, table, cradle, anything outside the body)
1            fat (adipose tissue inside the skin envelope)
2            muscle / lean soft tissue (one compartment; single muscles are not resolved by CT)
4            metal hardware
10 + 2k      cortical (dense) part of bone k, k = 0, 1, 2 ...
11 + 2k      medullary (inner, marrow / trabecular) part of bone k
"""

from __future__ import annotations

BACKGROUND, FAT, MUSCLE, METAL = 0, 1, 2, 4
BONE_BASE = 10
MAX_BONES = 100

COLORS = {"skin": "#d9b8a0", "fat": "#e8c170", "muscle": "#b5524c", "metal": "#4aa3df",
          "cortical": "#efe9dc", "medullary": "#c9a27a"}


def cortical(k: int) -> int:
    return BONE_BASE + 2 * k


def medullary(k: int) -> int:
    return BONE_BASE + 2 * k + 1


def is_bone(label: int) -> bool:
    return BONE_BASE <= label < BONE_BASE + 2 * MAX_BONES


def bone_index(label: int) -> int:
    return (label - BONE_BASE) // 2


def default_names(n_bones: int, bone_names: list[str] | None = None) -> dict[int, str]:
    """id -> structure name, e.g. {1: 'fat', 2: 'muscle', 4: 'metal', 10: 'radius_cortical', ...}."""
    names = {FAT: "fat", MUSCLE: "muscle", METAL: "metal"}
    for k in range(n_bones):
        base = bone_names[k] if bone_names and k < len(bone_names) else f"bone_{k + 1}"
        names[cortical(k)] = f"{base}_cortical"
        names[medullary(k)] = f"{base}_medullary"
    return names
