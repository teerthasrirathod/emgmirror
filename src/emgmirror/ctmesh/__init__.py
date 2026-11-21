"""CT label map to 3D meshes (milestone S2 of docs/ct_platform/PLAN.md).

Input: ``labels.nii.gz`` (uint16 label map on the CT grid) plus ``labels.json`` (the S1 contract).
Output: per-structure GLB/STL/3MF, ``combined_web.glb``, ``combined_print.glb``, a DICOM-SEG file, the label map
copied through and ``manifest.json`` (the S3 contract). See ``pipeline.run``.

Research use, not for diagnosis or surgical planning.
"""

DISCLAIMER = "Research use, not for diagnosis or surgical planning."
__version__ = "0.1.0"

from .contract import Label, LabelMap, load_labelmap, save_labelmap  # noqa: E402
from .geometry import LPS_TO_GLTF  # noqa: E402

__all__ = ["DISCLAIMER", "Label", "LabelMap", "load_labelmap", "save_labelmap", "LPS_TO_GLTF", "run"]


def run(*args, **kwargs):
    """Lazy alias for :func:`emgmirror.ctmesh.pipeline.run` (keeps ``import emgmirror.ctmesh`` light)."""
    from .pipeline import run as _run
    return _run(*args, **kwargs)
