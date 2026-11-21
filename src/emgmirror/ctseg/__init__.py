"""Segmentation engine for the hosted CT-to-3D service (milestone S1 of docs/ct_platform/PLAN.md).

Research use, not for diagnosis or surgical planning.

One entry point, :func:`emgmirror.ctseg.engine.segment` (CLI: ``python -m emgmirror.ctseg run``):

    CT (NIfTI, or a DICOM folder) -> input checks -> TotalSegmentator ``total`` (or ``total_mr``)
    -> region detection -> only the open subtasks for the regions in the field of view
    -> MOOSE where limbs / trunk wall are present -> priority fusion into one canonical label map
    -> per-structure QC -> ``labels.nii.gz`` + ``labels.json`` + ``provenance.json``

Only commercial-safe weights are allowed (:mod:`emgmirror.ctseg.allowlist`). The model packages
(TotalSegmentator, MOOSE) are optional imports: the rest of the package, and its tests, work without them.

Conventions: images are ``SimpleITK.Image`` in LPS mm; the output label map is uint16 on exactly the grid
of the image the engine segmented (the input CT, or ``image.nii.gz`` when an oversized input was resampled).
"""

DISCLAIMER = "Research use, not for diagnosis or surgical planning."
CONTRACT_VERSION = 1
ENGINE_VERSION = "0.1.0"
