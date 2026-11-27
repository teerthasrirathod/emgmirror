# CT Dataset Collection and Curation Protocol

**Protocol version:** 1.0.0
**Manifest schema:** `schemas/ct_dataset_manifest.schema.json`
**Status:** Research and engineering evaluation only. This protocol does not establish clinical performance or authorize clinical use.

This protocol defines how to assemble reusable cohorts for varied CT inputs, scan geometries, anatomy segmentation, and future fracture evaluation. It keeps scan intake, anatomy/model coverage, and fracture labels as separate evaluation questions. No cohort is required to contain every body region: focused scans are valid and their limited field of view must be recorded honestly.

## Cohorts and what each can measure

| Cohort | Sampling and purpose | Appropriate evaluation |
|---|---|---|
| Input and geometry coverage | Deliberately diverse formats and geometries: single files, paired/multi-file packages where supported, DICOM series, varied dimensions, slice counts, spacing, orientation, and focused or broad fields of view. | Read success, geometry preservation, conversion provenance, and unsupported-case reporting. It says nothing by itself about segmentation accuracy. |
| Anatomy segmentation | Cases with reviewed reference masks for selected structures. Include broad coverage across head, spine, torso, pelvis, arms/hands, and legs/feet as available, plus focused regional studies. | Per-structure segmentation metrics only where a reference annotation is complete and in field of view. Missing labels are excluded from negative counts and reported as unscored. |
| Fracture challenge | Enriched, expert-reviewed positive and difficult cases, including subtle/hairline, nondisplaced, complex, and common fracture patterns, together with reviewed negatives. | Sensitivity and error analysis on the defined challenge set. Its enriched prevalence must not be used to estimate population prevalence or positive predictive value without appropriate sampling weights and correction. |
| Prevalence evaluation | Consecutive cases or a documented probability sample from the intended evaluation population. Preserve the natural prevalence and relevant case mix; do not balance or enrich classes. | Performance at the sampled prevalence, with uncertainty intervals and sampling design reported. Do not merge with the enriched challenge set for prevalence estimates. |

Use multiple patients and, where governance permits, multiple sites and scanner vendors. A whole-body cohort is useful for broad anatomy coverage; it does not replace focused head, spine, torso, pelvis, arm/hand, leg/foot scans. Conversely, focused scans must not be described as whole-body coverage.

### Split design and leakage control

Every case record includes `dataset_id`, `patient_group_id`, `site_id`, `split`, and a `split_strategy`. Use opaque pseudonymous IDs. Keep every scan from one patient in exactly one split, including repeat studies and derived versions. A patient-grouped split may use train, validation, internal test, and external test partitions. For a site-held-out strategy, reserve one or more entire pseudonymous sites for `external_test`; no site may occur in another split. Freeze the split manifest before tuning models. Record the strategy and any held-out site pseudonyms; never identify a real institution in the manifest.

When cohort membership differs within one dataset, retain a common patient-level split. Do not use challenge-set composition to tune a model and then report that same set as an untouched test. Site and patient grouping reduce leakage but do not prove independence; document known overlaps between public datasets where possible.

## Privacy and source handling

- Use random or keyed pseudonymous IDs for cases, patient groups, sites, and reviewers. Never put patient names, dates of birth, exact dates/times, accessions, DICOM UIDs, institution/facility names, addresses, or free-text DICOM descriptions in repository metadata.
- Keep any re-identification key and source-system mapping outside the repository in an access-controlled environment. Do not use raw source identifiers as pseudonyms.
- De-identify or minimize source metadata before placing files under the local `data/ct_eval/` root. `.gitignore` only prevents routine Git tracking; it does not de-identify, encrypt, or secure local data.
- Store source files and derived files outside Git. Use only data whose acquisition, consent, ethics, license, and use terms permit the intended research. Do not upload private or clinical scans to external tools or services without the required authorization and safeguards.
- Prefer a small technical metadata allowlist: source kind/format and adapter IDs, dimensions, spacing, orientation, coordinate frame, intensity scaling, scanner manufacturer/model where safe, acquisition parameters, reconstruction kernel, and anonymized protocol category. Do not retain arbitrary headers or free text.
- Review every proposed extension field for re-identification risk. Technical metadata can still be identifying in combination; apply the data controller's disclosure-risk review.

The manifest records source paths relative to the local data root. Paths must not be absolute, contain `..`, or resolve outside the root. The optional validator checks these properties and referenced files; it cannot determine whether a scan is legally obtained or truly de-identified.

## Canonical volume and geometry records

Record the original source volume geometry as dimensions in voxel order, spacing in millimetres, origin, direction matrix, and named coordinate frame. Preserve orientation and intensity scaling. If conversion or resampling creates a derived volume, give it a separate artifact record with its own geometry, parent artifact, conversion/resampling history, software/version, and reason. Never replace the original geometry with a resampled geometry or silently claim the two are identical. Record warnings such as incomplete series, inconsistent slice spacing, truncation, or uncertain orientation.

The format and adapter identifiers are descriptive and extensible. Adding a new format must not require changes to anatomy labels or the fracture schema.

## Anatomy, field of view, and model processing

Use stable `anatomy_id` values aligned with the repository ontology/catalog where an entry exists. The catalog may grow; validators must not hardcode a list of bones. For each anatomy annotation, record separately:

- `fov_status`: `in_fov`, `partial_fov`, `out_of_fov`, `uncertain`, or `not_applicable`;
- `annotation_status`: `annotated`, `not_annotated`, `uncertain`, or `not_applicable`;
- optional reference finding status: `present`, `absent`, `uncertain`, `not_assessed`, or `not_applicable`;
- optional reference mask path and annotation provenance.

An absent annotation, a missing mask, and `not_annotated` mean “unknown/unscored,” never “anatomy absent.” Use `absent` only when a reviewer explicitly assessed the structure and confirmed absence within the stated scope.

Keep model execution status separate from reference annotation status. For each model/task and anatomy identity, preserve `segmented`, `not_scheduled`, `unavailable`, `failed`, `not_detected`, `partial`, or `uncertain`, along with model/task/version, output artifact, and error/QC information as appropriate. `not_detected` is a model result, not a radiologist-confirmed negative. A scan may be a valid geometry case even when no applicable model is available.

Reference masks should share the referenced volume's grid or explicitly record their own geometry and mapping. Do not silently resample a reference mask. Report segmentation metrics only for structures with appropriate reviewed reference masks and sufficient in-field coverage; publish per-structure counts and exclusions.

## Fracture assessment and annotation

Fracture review is a separate cohort task. Record one explicit assessment for each defined scan or anatomical scope: `positive`, `negative`, `uncertain`, `unreviewed`, or `not_applicable`. A negative requires complete review of the stated scope by the configured reference process. Missing reports, absent labels, and not-detected model output are not negatives. A positive assessment requires one or more lesion instances.

Each lesion should capture a stable lesion ID, anatomy identity, laterality, fracture type, acuity/healing state when known, visibility/subtlety, review/adjudication state, and localization. Localization may be a binary mask or a point/box/polygon in voxel coordinates or physical coordinates; include the referenced artifact and coordinate frame. Keep uncertainty explicit. Do not invent details that readers cannot establish from available evidence.

Use and document a subtlety rubric consistently. Suggested ordinal anchors:

| Score | Visibility on the source CT |
|---|---|
| 1 | Obvious displacement, cortical disruption, or clear fracture line on routine review. |
| 2 | Clear fracture with careful inspection; minimal ambiguity. |
| 3 | Subtle but visible fracture line/cortical step on thin slices or targeted multiplanar review. |
| 4 | Hairline or nondisplaced finding requiring focused bone-window review; limited reader agreement expected. |
| 5 | Borderline/indeterminate appearance; retain as uncertain unless adjudication establishes a fracture. |

The rubric is a starting protocol and must be calibrated by qualified reviewers before use. Record slice thickness, reconstruction kernel, and review display conditions because visibility depends on acquisition and review. Report sensitivity by subtlety strata and do not infer hairline performance from anatomy segmentation Dice.

## Review workflow and quality control

The target workflow uses appropriately qualified readers, preferably independent primary and secondary reviews with a third-reader adjudication path for disagreement. This describes the intended curation process; it does not imply those reviewers or approvals are currently in place. Record pseudonymous reviewer identifiers or roles, review completeness, disagreement, adjudication status, and protocol version. Define the review qualifications and adjudication rules before collecting labels.

Track case-level review status separately from per-anatomy annotation coverage and fracture assessment. Audit geometry, FOV, and annotation completeness; report audit selection and outcomes rather than claiming a fixed audit rate without a defined sampling plan. Preserve uncertain and unreviewed cases for workflow accounting, but exclude them from binary metrics unless a predeclared analysis method handles uncertainty.

## Versioning, reporting, and intended use

One JSON object per line in `dataset_manifest.jsonl` is the registry format. Each record identifies the manifest/schema version, dataset and cohort membership, patient-group split, source artifacts, geometry, annotations, and provenance. Version the annotation protocol, schema, cohort composition, and split manifest. Treat changes to labels, adjudication, or splits as a dataset release change; never silently rewrite test labels after model tuning.

Publish denominators, inclusion/exclusion rules, data source/license, patient/site grouping strategy, natural or enriched sampling design, per-structure coverage, annotation completeness, reviewer agreement, and confidence intervals. This repository currently supports research and engineering evaluation only; clinical diagnosis, operative planning, or intraoperative reference use requires separate validation, governance, and applicable regulatory authorization.
