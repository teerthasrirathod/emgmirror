"""Coordinate frames.

* Meshes inside the pipeline are in DICOM patient **LPS millimetres** (x to the patient's left, y posterior,
  z superior), computed from the label map's own origin / spacing / direction (oblique grids included).
* glTF 2.0 is right-handed, +Y up, metres; a model faces the viewer along +Z. We map the patient in anatomical
  position so the viewer looks at the patient's front:

      glTF +X = patient left  (= +x LPS)
      glTF +Y = superior      (= +z LPS)
      glTF +Z = anterior      (= -y LPS)

  times 0.001 (mm to m). The matrix is a proper rotation (det +1) times a uniform scale, so normals and
  winding survive unchanged. It is written to ``manifest.json`` (``lps_to_gltf``) and to the GLB ``asset.extras``,
  so a viewer can go back to LPS mm with its inverse.
"""

from __future__ import annotations

import numpy as np

LPS_TO_GLTF = np.array([
    [0.001, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.001, 0.0],
    [0.0, -0.001, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
])
GLTF_TO_LPS = np.linalg.inv(LPS_TO_GLTF)
AXES_NOTE = "glTF (+Y up, from patient LPS)"


def apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply a 4x4 affine to (n, 3) points."""
    pts = np.asarray(pts, float)
    return pts @ m[:3, :3].T + m[:3, 3]


def lps_to_gltf(pts_mm: np.ndarray) -> np.ndarray:
    return apply(LPS_TO_GLTF, pts_mm)


def gltf_to_lps(pts_m: np.ndarray) -> np.ndarray:
    return apply(GLTF_TO_LPS, pts_m)


def index_to_lps_matrix(origin, spacing, direction) -> np.ndarray:
    """4x4 matrix mapping continuous voxel index (i, j, k) = (x, y, z) order to LPS mm (ITK convention)."""
    D = np.asarray(direction, float).reshape(3, 3)
    m = np.eye(4)
    m[:3, :3] = D @ np.diag(np.asarray(spacing, float))
    m[:3, 3] = np.asarray(origin, float)
    return m
