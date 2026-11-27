# CT volume to 3D result path

**Research software; not for diagnosis or surgical planning.**

The current application path is `ctapp.server`: it accepts a CT upload, normalizes it for processing, invokes configured segmentation task(s), converts available labels to 3D surfaces with QC/provenance, and serves the `web/ct-viewer` result.

```text
DICOM/NIfTI upload
  → intake and geometry checks
  → canonical volume + retained source metadata
  → model/task planner and segmentation status
  → label map + QC
  → 3D surfaces + provenance
  → CT slices and 3D viewer
```

The active local entrypoint is `ct3d` (Python module: `emgmirror.ctapp.server`). The hosted container is defined in `deploy/ctapp/`. The older threshold-driven local builder and embedded CT page have been removed so there is one user-facing CT build path. Shared DICOM, geometry validation, and mesh benchmark helpers remain under `src/emgmirror/ct/` while the current pipeline uses them.

## Interpretation boundary

Input support means a file or series can be read while preserving its spatial geometry. It does not mean every model covers the anatomy in the scan. The result must distinguish segmented, not scheduled, unavailable, failed, and not detected structures. Resampling or other transformations must be recorded.

A segmentation is not fracture detection. Fracture candidates require a dedicated task and region-specific evaluation with expert-labeled cases. 3D overlays must map back to the original CT so reviewers can inspect the evidence.

Do not represent synthetic anatomy, reference meshes, or super-resolved detail as scan-derived findings. Preserve original CT data and scan-derived masks as separate provenance-bearing artifacts.

See `docs/ct_platform/` for dataset catalog/protocol, segmentation plan, geometry and mesh requirements, and evaluation notes.
