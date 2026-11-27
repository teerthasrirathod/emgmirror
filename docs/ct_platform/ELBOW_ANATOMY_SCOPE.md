# Elbow scope: anatomy, source masks, and meaningful output

## What the Nature/Zenodo source actually gives us

Wang et al. describe 201 healthy elbow CT cases, with surgeon segmentation and STL surfaces for the **distal humerus and proximal ulna**. It is a strong same-scan reference for the humeroulnar part of the elbow, but it is not a full elbow annotation set: it has no radius reference mask, no fracture cases, and no whole-body context. Its measurements target elbow arthroplasty geometry, not fracture sensitivity. The Zenodo record states CC BY 4.0.

Local case 001 is a right elbow CT with size 512 × 512 × 105, spacing 0.455 × 0.455 × 1.721 mm, in an LPS physical frame. Its reference mask has a different z origin and reversed through-plane direction from the CT. The source mask is therefore nearest-neighbour resampled onto the CT physical grid before comparison. No CT geometry is changed. Visual overlay of the physically aligned labels supports this case-specific mapping:

| Source mask value | Structure represented | Evidence/status |
|---:|---|---|
| 1 | Distal humerus, right | Visible CT/mask overlay; cross-checked against the source's named humerus surface |
| 2 | Proximal ulna, right | Visible CT/mask overlay; cross-checked against the source's named ulna surface |
| no reference value | Proximal radius, right | Not annotated; do not treat missing mask as negative |

The annotated 3D surfaces have a z coordinate offset from the CT/mask physical frame. Do not load those STLs into the patient CT frame until their transform is independently established. The voxel masks are the safe reference after physical resampling. The review overlay is [`elbow_case001_reference_overlay.png`](elbow_case001_reference_overlay.png).

The tracked script [`scripts/compare_elbow_reference.py`](../../scripts/compare_elbow_reference.py) reproduces the local case comparison and writes [`results/elbow_case001_comparison.json`](results/elbow_case001_comparison.json). The local MOOSE output is on the CT grid. Against the mapped same-side labels, case-001 Dice is 0.525 for the humerus and 0.398 for the ulna. The right humerus prediction has four connected components and the right ulna prediction has two. Full-field HD95 is high (108.3 mm and 49.8 mm); it is affected by differences between the cropped expert reference extent and the larger/disconnected model masks. Keep these as debugging signals, not benchmark claims. The model also predicts a right radius, but no source radius mask exists here to score it.

## Elbow anatomy that the product should represent

Treat these as separate anatomy entities and relationships. A joint is not a bone label, and a normal joint space is not a mesh defect.

| Target | Required product representation | CT/reference status for this source |
|---|---|---|
| Humerus, distal region | Bone identity plus in-scan voxel mask/surface, with coverage bounds | Expert reference available, distal extent only |
| Radius, proximal region (head and neck) | Separate bone identity/mask/surface; preserve its articulation with capitulum and ulna | Visible on elbow CT, but not source-annotated; model output is unvalidated here |
| Ulna, proximal region (trochlear notch, olecranon, coronoid process, radial notch) | Bone identity/mask/surface; preserve notch and articular contour | Expert reference available, proximal extent only |
| Humeroulnar articulation | Relationship between trochlea and trochlear notch; joint-space geometry/QC | FIPAT TA2 1773; relationship derived from bone surfaces, not a fourth bone |
| Humeroradial articulation | Relationship between capitulum and radial head | FIPAT TA2 1774; radius reference absent in this dataset |
| Proximal radioulnar articulation | Relationship between radial head and radial notch of ulna | FIPAT TA2 1775; radius reference absent in this dataset |
| Elbow joint complex | Parent/group for the three articulations above | FIPAT TA2 1772 |
| Ulnar collateral ligament; radial collateral ligament; annular ligament; quadrate ligament | Catalog entities and attachment relations; show as CT-derived masks only when a capable protocol/model and review support it | FIPAT TA2 1776–1779; not annotated in this elbow CT release |

The ulna and radius are separate bones and should never be fused to make the elbow appear connected. The humerus articulates with both; the proximal radius also rotates against the ulna. Surface proximity and the expected joint space are evaluated as relationships, not forced mesh contact.

## Planner gap revealed by a focal elbow scan

The current region planner derives its body-region evidence from the baseline segmentation. Its upper-limb marker is humerus; radius and ulna are outputs of the MOOSE peripheral-bones task, not baseline inputs to that planner. Therefore, adding radius/ulna names to the marker list would not make an elbow-only scan trigger MOOSE if the baseline model produced no humerus marker. A selected skeletal profile currently schedules MOOSE regardless of region evidence; other profiles can still miss a focal scan. The durable fix is an independent scan-region locator or an explicit user/task selection, with the planner recording why a task ran or was skipped. This source audit does not silently change that scheduling behavior.

## Coverage semantics the viewer should use

For each anatomy identity, record coverage and evidence independently:

- `segmented`: a mask exists for the correct structure on the declared physical grid.
- `partial`: a mask exists but source field-of-view or annotation extent is incomplete.
- `not_annotated`: the scan may show the structure, but this reference has no expert label (case-001 radius).
- `not_scheduled`, `unavailable`, `failed`, `not_detected`: model execution states; none means the anatomy is absent.
- `not_resolvable_on_source`: use for soft tissues or fine structures the acquisition/task cannot reliably resolve. Do not render these as negative findings.
- `not_in_field_of_view`: only after a geometry-aware FOV check.

Keep fracture findings separate from bone segmentation. A hole, disconnected component, or absent predicted bone voxel is not itself evidence of fracture. Fracture output needs a separate lesion identity, source-CT evidence, localization, review status, and a fracture-labeled validation cohort. The 201-case elbow cohort is healthy, so it cannot validate fracture detection or hairline-fracture sensitivity.

## Sources

- Wang et al., [Dataset of 201 healthy elbow CT scans with humeroulnar segmentation, 3D models, and anatomical features](https://www.nature.com/articles/s41597-026-07609-1), *Scientific Data* (2026), DOI [10.1038/s41597-026-07609-1](https://doi.org/10.1038/s41597-026-07609-1).
- [Zenodo record 20305534](https://zenodo.org/records/20305534), CC BY 4.0; scope summarized by the [NLM Dataset Catalog](https://datasetcatalog.nlm.nih.gov/dataset?q=0002963501).
- FIPAT, [Terminologia Anatomica 2, Part 2](https://fipat.library.dal.ca/wp-content/uploads/2020/09/FIPAT-TA2-Part-2.pdf), TA2 entries 1772–1779 for elbow joint, its articulations, and listed ligaments.
- NCBI Bookshelf, [Elbow joint anatomy](https://www.ncbi.nlm.nih.gov/sites/books/NBK532948/?report=reader) and [elbow collateral ligaments](https://www.ncbi.nlm.nih.gov/books/NBK560907/?report=printable).
