# Structured CT findings workflow

## Why this layer exists

The NVIDIA MedTech Days session frames a move from static anatomy toward multimodal, longitudinal and dynamic patient representations. The adjacent NV-Reason-CT work demonstrates a narrower, practical workflow: send a native 3D CT to a region-aware model, produce a structured report, and ask follow-up questions. That interaction pattern is useful for this product, but it is a different capability from segmentation and surface reconstruction.

Our existing pipeline already accepts a CT package, normalizes a canonical NIfTI, records intake geometry, plans anatomy models, creates label maps and meshes, and displays the CT alongside those outputs. Its segmentation planner reports model activity; it does not create radiology findings or prove fracture absence. The new findings workflow must remain a separate adapter family.

## Workflow boundary

1. **Intake:** retain the uploaded package and canonical CT geometry. Identify source and derived volumes explicitly.
2. **Task planning:** use scan modality, field of view, geometry quality, available model capabilities, and requested task to schedule work. Record not scheduled, unavailable, failed, partial, and completed tasks distinctly.
3. **Parallel analysis:** anatomy segmentation produces label maps and surfaces. A future finding/reasoning adapter may produce structured observations and evidence anchors. Neither result is inferred from the other.
4. **Normalize evidence:** map every proposed location back through orientation, crop, and resampling transforms to the original canonical CT volume. The sidecar schema uses LPS millimetres for evidence points. Reject or mark evidence unmappable rather than displaying a fabricated location.
5. **Review:** label model assertions as unreviewed until a qualified human review process changes the review state. A model's failure to mention a finding is not a negative. Only an explicit human-reviewed, complete-scope assessment can state absence.
6. **Viewer:** display concise structured observations with their source, model/task version, review state, and an action to open the source CT at the evidence point. Keep 3D anatomy visibility and finding evidence linked but independently controllable.

## Result contract

An `analysis.json` sidecar follows `schemas/ct_analysis_result.schema.json`; `manifest.json` points to it through `analysis.file`. The current upload app writes an explicit `not_scheduled` result until a finding-analysis adapter is configured. Segmentation continues to use `labels.nii.gz`, `labels.json`, mesh files, and `processing`. The findings sidecar records run status, region coverage, preprocessing transforms, finding assertions, source provenance, review status, and an evidence point in canonical CT coordinates.

Do not copy hidden chain-of-thought into the result. Store concise, reviewable observations and spatial evidence, not a generated narrative presented as proof. Any generated explanation must be traceable to a finding and source volume location.

## Model scope and gates

NV-Reason-CT is a candidate reference for the interaction pattern, not a whole-body or fracture solution. Its documented path is chest/abdomen, NIfTI input, LPS orientation, 2 mm isotropic resampling, and a region-aware fixed-size crop. Its processor documentation notes that the crop does not retain the source world origin. Integration therefore requires a tested crop/resampling transform, original-volume evidence mapping, immutable model/code revision, model and data license review, supported-hardware evaluation, and task-specific expert-reviewed datasets. No model or weights are downloaded by this change.

Start with a research-only adapter contract and viewer support. Do not schedule a reasoning model by default, do not describe its output as a diagnosis, and do not let it overwrite anatomy masks or human reference annotations. Add inference only after the adapter can prove coordinate round trips and the selected model is compatible with the intended use and data permissions.

## Release gates for a future adapter

- Input NIfTI hash and physical geometry are recorded; original geometry remains available.
- Every crop, orientation change, and resampling is captured in an invertible source-to-model transform.
- Output is schema-valid and uses stable region/anatomy IDs.
- Every finding location round-trips to the source CT within a declared physical tolerance and lies within its field of view.
- Model output is marked as a suggestion, with task/model/version and review state.
- Negative findings require complete human review; model omission is never represented as absence.
- Evaluation is separated by task and region, with enriched fracture challenges kept separate from natural-prevalence cohorts.
