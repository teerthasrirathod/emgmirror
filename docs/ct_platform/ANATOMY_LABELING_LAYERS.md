# Anatomy identity → model classes → CT masks → surfaces

## Why this audit exists

Anatomical names, model output labels, voxel masks, and 3D meshes are different records. A name being present in the UI does not prove that a model can produce it, and a model class does not prove the predicted voxels are anatomically correct. This note audits the current implementation and sets the data contract needed to close the elbow gap without manufacturing geometry.

## Findings in this checkout

These counts were read from `src/emgmirror/ctseg/resources/label_table.json` and `task_classes.json`:

| Current inventory | Count | What the count means |
|---|---:|---|
| App-internal label rows | 224 | Display/merge vocabulary; integer IDs are local to this app. |
| Model task class entries | 306 | Class IDs across all configured tasks, including repeated labels from different tasks/models. |
| Distinct model output names | 224 | Every current internal label name has at least one model task mapping. |
| FIPAT/TA2 identifiers in internal labels | 0 | No TA2 entity ID, Latin official name, hierarchy reference, or mapping confidence is stored. |

The current Z-Anatomy machine-readable TA2 export contains **7,192 nonblank numbered rows**, with IDs in the range 0–7228 (IDs are not a dense count). This count was calculated from the downloaded `TA2.csv` working copy on 2026-11-26 (SHA-256 `0f9092a328b27dcd15d696d9f9a4087deb229a1aad21b75876657622de835974`). Treat it as a count from a repackaged index, not as a definitive count of CT-segmentable structures or a claim that every row is an independent entity. The official TA2 publication is split into parts and is the normative source; its hierarchies and errata need to be parsed/validated before publishing a definitive count. TA2 also contains general terms, regions, planes, movements, and groups/parts that should not each become a voxel label.

A deliberately shallow string comparison against the export found **83 candidate exact/normalized name matches, 2 ambiguous matches, and 139 unmatched names** among the 224 app labels. This is only a triage result: the comparison checks the export's English/Latin official-name columns and basic left/right suffix variants, not synonyms, parent/child semantics, or anatomy review. It is not safe to install these candidates as accepted mappings. For the left/right variants, the correct result is usually one TA2 structure identity plus a laterality modifier, not two invented TA2 IDs.

The row-by-row triage is in [`anatomy_mapping_candidates.csv`](anatomy_mapping_candidates.csv). Every row names its model task/class providers and explicitly marks the TA2 result as an unreviewed candidate, ambiguous, or unmatched. It is a review ledger, not runtime metadata.

The implementation evidence for the elbow is specific:

- `humerus_left` is local label 69 and is mapped from TotalSegmentator `total` class 69, `total_mr` class 30, and MOOSE `clin_ct_peripheral_bones` class 11.
- `radius_left` is local label 294 and is mapped only from MOOSE class 19.
- `ulna_left` is local label 302 and is mapped only from MOOSE class 30.
- `runners.py` returns a discrete label image and class-name map. `_onto` keeps or nearest-neighbour-resamples that result to the input grid. The renderer later meshes these masks; it does not infer bone ends or close anatomical gaps.
- On the local Visible Human elbow CT, the wider-context MOOSE crop predicted all three names and aligned to the source physical grid, but the radius mask still had poor proximal coverage against the reference and a large humerus-to-radius surface gap. This is upstream segmentation error. The reference source manifest independently warns that its elbow annotations are incomplete/truncated. A completed full-volume inference was not obtained, so it cannot be used to claim a full-scan fix.

These facts mean the current failure is not “the app has no word for radius.” It has the name and an output-class map. The missing part is a validated, scan-aligned radius voxel mask at the proximal elbow, followed by QC that detects the omission before surfacing.

### Root-cause resolution for the current whole-skeleton demo

The current whole-skeleton demo uses BoneHub Visible Human `01_Male`. Its source dataset documentation states that the elbow is not fully captured in the source CT: distal humerus and proximal radius/ulna are incomplete because image data are missing/truncated. The imported masks and CT use the same grid; the build script rejects spacing, origin, direction, or size mismatches instead of resampling. The displayed surfaces are meshed from those masks, not inferred or extended. In the generated manifest, the left humerus is 2.81 mm from the volume boundary and the left radius/ulna are 0.94 mm away; the existing `cut_by_scan_edge` flag is false because the masks stop just inside the edge, which is why the dataset-level limitation was easy to miss.

The names `Humerus, left/right`, `Radius, left/right`, and `Ulna, left/right` are already anatomically appropriate display names, and the model class map already contains these structures. Replacing those names with FIPAT terms cannot restore the missing CT samples or reference voxels. The separate elbow model case is a different person and cannot be inserted into this scan. A same-scan MOOSE prediction was evaluated but did not provide a sufficiently complete elbow mask, so it is not a safe replacement for the source labels.

The viewer/build contract now carries `source_status: partial` and a reason on the six affected arm-bone records. This status describes incomplete source reference coverage; it is separate from scan-edge mesh QC and from model inference status. A genuinely complete elbow for this whole-skeleton result requires a different CT that captured the joint, or expert-reviewed new masks from valid same-patient CT voxels. This sample cannot support a faithful full elbow reconstruction from its available CT.

## The four layers and their records

### 1. Anatomical knowledge / nomenclature

Use FIPAT TA2 as the named-term authority, with an external stable identity (`TA2:<term-number>`), official Latin term, English equivalents, synonyms, term category, parent/child relation, source edition/revision, and citation. Preserve the FIPAT hierarchy and distinguish official terms from synonyms and related terms. Keep local display names as aliases, not as identity.

TA2 is a nomenclature, not a CT segmentation ontology. A TA2 term can be a region, a group, a part, or a structure whose boundaries cannot be independently distinguished on a routine CT. Do not turn every TA2 row into a promised model output. Add separate, explicit properties for `structure_kind`, `laterality_policy`, `ct_visibility`, `segmentability_level`, and `scope_reason` (for example: not a spatial structure, below CT resolution, contrast-dependent, not separable from parent, outside current model coverage).

Do not bundle and redistribute a transformed copy of the full TA2 hierarchy without a license review. IFAA publishes TA2 under CC BY-ND 4.0. Store the minimum identifiers/mappings needed by the app, cite the source/version, and confirm whether a proposed derived catalog needs permission.

### 2. Segmentation ontology / model output vocabulary

Represent each class as `(model_id, model_version, task_id, class_id, source_dataset, class_name)`. Map it explicitly to one or more stable anatomy identities with a relation such as `exact`, `broader_than`, `narrower_than`, or `composite_of`, and record mapping evidence/reviewer. This prevents “MOOSE class 19” or “TotalSegmentator class 69” from becoming permanent anatomy identity.

The present `task_classes.json` already performs a useful first-stage mapping from source/class integer to app label name. It is not a segmentation ontology yet: it has no TA2/FMA/UBERON identity, no equivalence relation, no composite semantics, and no source-model release or mapping review in the table. nnU-Net's `dataset.json` design similarly treats class names and integer IDs as a model dataset contract; it does not make those names a complete anatomical ontology. Model capability must remain versioned and task-specific.

### 3. Voxel annotation / segmentation evidence

For each output structure, store a mask/probability reference on a declared physical grid and preserve source CT geometry. A result row should include:

```json
{
  "anatomy_id": "TA2:<id-or-null>",
  "local_label_id": 69,
  "model_output": {"model": "moose", "version": "...", "task": "clin_ct_peripheral_bones", "class_id": 19},
  "mask": {"uri": "...", "grid_id": "source-ct", "encoding": "binary-or-probability"},
  "geometry": {"size": [0, 0, 0], "spacing_mm": [0, 0, 0], "origin_mm": [0, 0, 0], "direction": [0, 0, 0, 0, 0, 0, 0, 0, 0], "frame": "LPS"},
  "coverage": "present-or-partial-or-not-detected-or-not-scheduled-or-unavailable-or-failed",
  "qc": {"touches_fov_edge": false, "components": 0, "confidence": null, "review": "unreviewed"}
}
```

Use real enum values and full matrices in the implementation; the abbreviated example shows the separation of responsibilities. Keep model prediction, manual correction, and reference annotation provenance distinct. In 3D Slicer/MONAI Label, a clinician or trained annotator can review/refine a prediction against linked CT slices and save the correction as a new annotation version rather than silently overwriting the source. That is a practical way to create elbow ground truth and hard cases for retraining/benchmarking.

An empty mask must not be called “not in scan” unless a validated FOV locator establishes that. A model omission is not evidence of a fracture. A fracture is a finding on the original CT—such as cortical/trabecular discontinuity, a fracture line, or displaced fragments—requiring a separate fracture-detection/localization task and its own voxel evidence and validation. A segmentation hole may instead be an inference failure, partial-volume effect, artifact, or true defect.

This separation is also reflected in fracture-specific CT research: FracNet formulates rib fracture detection as voxel-level fracture segmentation, uses expert annotated fracture voxels, and reports detection sensitivity/false positives separately from segmentation Dice. Its results are specific to rib fractures and its evaluated cohort; they do not validate hairline fracture detection for every bone or scan type. The transferable design point is to preserve a fracture-instance mask/probability in the CT grid and link each finding to the bone anatomy identity, rather than infer a fracture from an incomplete bone mask.

### 4. Reconstruction / viewer

Mesh only the accepted mask, with source anatomy ID, source mask/version, geometry, mesher version, and mesh QC attached. Render the CT and segmentation in the same physical frame; provide linked multiplanar CT views and label overlays so a user can inspect the voxels behind each surface. Preserve the original CT and make any resampled grids explicit. Never bridge a joint, fill a missing bone head, or synthesize a distal/proximal bone end to make a surface look connected.

For the elbow, the joint space between humerus, radius, and ulna is normal; the articulating bones are not supposed to fuse into one continuous surface. The pass condition is complete, anatomically located articular surfaces and correct spatial relationship to CT, not mesh-to-mesh contact. Validate humerus, radial head/neck, and ulna/olecranon against reviewed voxel references in physical coordinates. Then inspect the derived surfaces in linked CT slices and report the measured mask-to-reference and surface-to-mask errors.

## How the open-source tools fit

- **MOOSE / TotalSegmentator:** inference providers. Their task-specific integer classes are evidence of what that exact model/task can output, not a body-wide label authority.
- **nnU-Net:** task dataset/training/inference framework. Its explicit label dictionary is the right shape for versioned model classes; model-specific label dictionaries still need ontology mappings.
- **MONAI Label + 3D Slicer:** interactive annotation and review path. Use it to correct/approve masks, capture reference annotations and edits, and export geometry-aligned labels; do not treat an automatically generated mask as truth without review/QC.
- **3D Slicer:** reference environment for checking original CT, orientation, linked slices, segmentation overlays, and surfaces in one physical coordinate frame. Its editing workflow is useful as a validation harness even if the product viewer remains web-based.

## Implementation sequence that closes the actual gap

1. Add a versioned anatomy identity record and an explicit mapping table; seed TA2 IDs for the current 224 labels. Produce a report of exact, ambiguous, composite, unmatched, and non-CT-segmentable terms. Do not import all TA2 terms as if all were model classes.
2. Preserve model/task/version/class metadata through fusion into the segmentation result. Add mask-source provenance, per-structure coverage states, and FOV/edge assessment.
3. Build an elbow reference set with CT plus reviewed masks for humerus, radius, and ulna, including proximal radius and olecranon. The 201-case Nature/Zenodo cohort now supplies useful distal-humerus/proximal-ulna references, but not radius masks, fracture labels, or full elbow bone coverage. Add a radius-annotated source and fracture-positive/negative cases before claiming three-bone elbow or fracture validation. Include more than one scan/reconstruction; store source, annotation author/reviewer, geometry, and revision.
4. Run MOOSE and TotalSegmentator against the same reference in the intended inference context. Score Dice/surface distance and specifically radial-head/olecranon coverage, false connections, missing components, laterality, and FOV clipping. A crop-only pass is not a production pass.
5. Change or add a model/task only when the reference comparison shows the mask is deficient. Keep postprocessing limited to evidence-preserving operations with measured bounds; no geometric bridge or synthetic fill for anatomic completeness.
6. Generate meshes from accepted masks, then validate the rendered surfaces against the original CT in linked slices and physical coordinates. The UI should say whether each elbow structure was segmented, partial, not detected, not scheduled, unavailable, failed, or reference/manual.

## Sources consulted

- IFAA/FIPAT, [IFAA terminologies and TA2 format/status](https://ifaa.net/committees/anatomical-terminology-fipat/fipat-ifaa-terminologies/).
- FIPAT, [TA2 official publication index](https://fipat.library.dal.ca/TA2/); [IFAA official FIPAT page](https://ifaa.net/committees/anatomical-terminology-fipat/).
- Z-Anatomy, [machine-readable TA2.csv](https://github.com/Z-Anatomy/Models-of-human-anatomy/blob/master/TA2.csv), used for the reproducible working count and candidate-name comparison; verify against the normative FIPAT parts before treating either as definitive.
- Halle, Kikinis, Neumann, [TA2Viewer paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC11551863/), explaining TA2 as a nomenclature and its import/hierarchy/errata policy.
- nnU-Net, [dataset format reference](https://github.com/MIC-DKFZ/nnUNet/blob/master/documentation/reference/dataset-format.md), for model dataset class dictionaries.
- Project MONAI, [MONAI Label repository documentation](https://github.com/Project-MONAI/MONAILabel), for model-assisted annotation and 3D Slicer/OHIF integration.
- Zhou et al., [FracNet rib-fracture detection and voxel segmentation study](https://pmc.ncbi.nlm.nih.gov/articles/PMC7670192/), as an example of a separate, annotated fracture task and separate detection/segmentation metrics.

## Current status

The viewer now reports incomplete source annotation on the affected arm bones in the Visible Human sample. The confirmed root cause for this specific demo is missing/truncated elbow image data in the source CT. The current sample's meshes cannot be made into a faithful full elbow by relabeling or smoothing. The Nature/Zenodo elbow case is now a second, elbow-focused evaluation lane: it provides same-scan expert references for distal humerus and proximal ulna, but not radius or fracture ground truth. It does not belong inside the whole-body subject. A complete three-bone elbow comparison still needs a same-scan radius reference; fracture claims need a separate reviewed fracture cohort.
