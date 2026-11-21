# Use specification: CT 3D Viewer (S3)

Status: **draft groundwork**, 2026-11-22. Prepared for the usability engineering file under IEC 62366-1:2015+A1:2020, clause 5.1 (use specification), with the first list of use-related hazards (clause 5.3–5.5 input to the ISO 14971 risk file). It is not a validated usability file: no formative or summative evaluation has been done yet.

The software today is a research build. The intended use below is the **draft** from [REGULATORY_PATH.md](../REGULATORY_PATH.md) section 2. It is recorded here so the interface can be designed for it, and it makes no claim that the software meets it.

## 1. Intended use (draft, from REGULATORY_PATH.md §2)

> A cloud-based software service that converts DICOM CT images of adult patients into 3D surface models of bony anatomy (pelvis/acetabulum, spine, shoulder girdle, proximal femur and humerus). After review, editing and approval by a qualified clinician, the models support pre-operative planning in orthopaedic and trauma surgery. To be used with the source images and clinical judgment. Not for diagnosis, lesion or tumour detection, measurement-based treatment decisions, children, MR or cone-beam CT input, intra-operative navigation, or registration to the patient.

The viewer's part of that use: show the models with their source CT, let a qualified clinician review, correct and approve them, and show approved models read-only on a planning or theatre screen.

Labelling is set when the software is built (`VITE_LABEL_STATUS`), never from the URL:
- `research` (default): "Research use only. Not for clinical use."
- `investigational`: "Investigational device: for use only in an approved clinical investigation."

## 2. Users

| User group | Role in the viewer | Training and knowledge assumed | Notes |
|---|---|---|---|
| **Reviewing clinician**: radiologist, orthopaedic or trauma surgeon, or another clinician qualified under the site procedure | Reviews models against the CT, corrects them, approves a version | Reads CT in axial/coronal/sagittal; knows radiological left/right convention; site training on this software (to be written) | Only this group may approve. S3 records a typed name and role; real authentication and role checks come with the backend |
| **Planning surgeon / surgical team** | Views approved models before surgery and on a theatre screen (reference mode) | Surgical anatomy; may not read CT daily | Must be able to see at a glance whether a model is approved, which patient and study it is, and which side is which |
| **Theatre staff** operating the screen | Opens the saved result, rotates or switches view on request | Basic tablet/PC use; no imaging training | Gloved or hurried use; distance viewing |
| **Researcher** (research build only) | Explores public data, exports meshes | Varies | No clinical use |

Excluded users: patients, lay people, and anyone using it for diagnosis.

## 3. Use environments

| Environment | Conditions | Design consequences |
|---|---|---|
| **Planning office / reading room** | Desktop or laptop, mouse and keyboard, normal or dim light, network available | Full interface: review panel, CT slices, downloads |
| **Operating theatre (reference only)** | Wall screen or tablet at 1–3 m, bright light, gloved or sterile-covered touch, interruptions, network may be absent | Reference mode: read-only, high contrast (black, white, yellow), controls ≥ 48 px, text ≥ 15–17 px, orientation letters 26 px, no hover-only information, works offline after "Save for offline use" |
| **Phone (out of theatre)** | 390 px wide, touch, variable light | Same functions in a narrow layout; not intended as the theatre screen |

## 4. Operating principle and main tasks

The user opens a result (one CT study and its models). The 3D view and CT slices show the same study. Every version of the models has a **result version hash** = SHA-256 of (delivered manifest + web model + the list of corrections). An approval is stored against that exact hash; if the hash changes, the approval no longer applies.

| # | Task | Primary operating function? | Where |
|---|---|---|---|
| T1 | Confirm the patient/study before anything else | yes | Identifier bar (study date, series, study ID, result version), always visible |
| T2 | Confirm the approval status | yes | Status pill in the identifier bar; red "NOT APPROVED" bar in reference mode |
| T3 | Orient: know left/right, anterior/posterior, superior/inferior | yes | L/R/A/P/S/I letters on all four edges of the 3D and oblique views; NiiVue orientation letters on slices; presets named by patient side |
| T4 | Compare each structure with the CT | yes | CT slices tab (with colour overlay), CT on the cut face in 3D |
| T5 | Correct a model: exclude a structure or erase a wrong region | yes | Review panel → Erase surface; info card → Exclude from model; every edit is listed with Undo |
| T6 | Approve the current version | yes | Review panel step 4 (needs reviewer, CT slices opened, attestation) |
| T7 | Withdraw an approval | yes | Review panel |
| T8 | Keep a record | no | "Download review record (JSON)" with the audit history |
| T9 | Save a result for theatre and view it offline | yes | Reference mode → Save for offline use |
| T10 | Rotate, zoom, cut, isolate a structure | yes | 3D toolbar, keyboard, touch |
| T11 | Read volume / QC notes for a structure | no | Info card; "Cut by scan edge" badge |

## 5. Use-related hazards and mitigations

Severity is a placeholder for the ISO 14971 file. "Test" names the Playwright test in `web/ct-viewer/tests/` that checks the mitigation exists; it does not prove the mitigation is effective. Only summative evaluation can show that.

| ID | Hazardous situation (use error) | Possible harm | UI mitigation in S3 | Test | Open items |
|---|---|---|---|---|---|
| H1 | **Wrong side**: user takes the patient's left for the screen's left, or a mirrored model is shown | Wrong-side planning | L/R/A/P/S/I letters on every edge of the 3D and oblique views, computed from the camera every frame; presets named by the patient's side ("View from left (patient's left side)"); radiological convention on slices with orientation letters; mesh axes fixed by `lps_to_gltf` | `device.spec.ts` › orientation markers follow the camera | Phantom laterality test through the whole pipeline (S1/S2 CI); summative test of left/right reading |
| H2 | **Wrong patient / wrong study** | Planning on another patient's anatomy | Study date, series description and study ID (hash of the source CT) shown at all times on every view and in reference mode; a red "Study details missing" message when the result has none | `device.spec.ts` › labelling banner and study identifiers; › missing study identifiers are flagged | De-identified header must carry a site-matchable identifier (S4); compare the CT hash when slices load |
| H3 | **Stale result**: an old or superseded model is used, e.g. a cached copy in theatre or a result regenerated after approval | Plan based on outdated models | Result version hash recomputed from the delivered files on every load; approval valid only for that exact hash; manifest is fetched network-first; offline copies show "saved for offline use <time>" and "Offline" | `device.spec.ts` › a changed result is not shown as approved | Server-side "superseded" flag and a forced check when reconnecting (S4) |
| H4 | **Unapproved model taken as approved** | Reliance on uncorrected segmentation | Every result opens "Not approved"; status pill always visible; reference mode shows a red "NOT APPROVED" bar; Downloads dialog states the approval status | `device.spec.ts` › models open as not approved; › reference mode | Block reference mode for unapproved results in the investigational build? (decision for the clinical lead) |
| H5 | **Approval without real review** | As H4 | Approve needs a named reviewer with a role, the CT slices opened in the session, and an explicit attestation; the attestation clears on any change | `device.spec.ts` › approval needs reviewer, slices and attestation | Authenticated accounts with qualification checks; per-structure checklist |
| H6 | **Edit after approval goes unnoticed** | As H4 | Any edit changes the version hash, the approval lapses at once, the history records "invalidate" | `device.spec.ts` › any edit invalidates the approval | Server-side append-only audit trail |
| H7 | **Misread scale / size** (mm vs cm, perspective distortion, decimated surface) | Wrong implant or approach sizing | Volumes shown in mL with one decimal; QC badge when the smoothed surface deviates > 2 mm; STL/3MF stated to be in millimetres; perspective fov 30° to limit distortion; no measurement tool | not yet | A scale bar in 3D and slices; measurement tools only after validation; footer states measurements are approximate |
| H8 | **Truncated anatomy read as real** (cut by the scan edge) | Wrong assessment of a bone's extent | "Cut by scan edge" badge and explanation ("the flat end is the scan edge, not anatomy"); marker in the structure list | `smoke.spec.ts` › cut-by-scan-edge badge is shown | Show the scan edge as a plane in 3D |
| H9 | **Network loss in theatre** | Display unavailable at the critical moment | Reference mode "Save for offline use" caches the app, the model and the CT; offline state shown | `device.spec.ts` › reference mode works offline once saved | Kiosk/packaged build for theatre PCs; storage eviction policy |
| H10 | **Accidental change in theatre** | As H6 | Reference mode is read-only: no edit, approval or download controls | `device.spec.ts` › reference mode: read-only | — |
| H11 | **Information only on hover** missed on touch screens | Missed QC warning | Reference mode has no hover tooltip; tapping a structure opens the info card with the same information and badges | `device.spec.ts` › reference mode: no hover-only controls | — |
| H12 | **Poor visibility at distance or in bright light** | Misreading the model or status | High-contrast reference palette, 48 px targets, large status text and orientation letters; WCAG AA contrast checked in both themes | `a11y.spec.ts`, `device.spec.ts` | Summative test at 1–3 m in theatre lighting |
| H13 | **Research build used clinically** | Use of an unapproved tool | Build-time label on every screen; research build says "Not for clinical use" | `device.spec.ts` › labelling banner | Device build must exclude research-only weights (REGULATORY_PATH §4) |

## 6. What S3 does not do yet (needed before clinical use)

- **Identity:** the reviewer's name and role are typed in; there is no sign-in, no qualification check and no electronic signature (21 CFR Part 11 / equivalent).
- **Records:** review records live in the browser's local storage and can be exported as JSON. A device needs a server-side, append-only audit trail tied to the result.
- **Edited models:** corrections apply in the viewer only; downloads are still the automatic models (the Downloads dialog says so). Exporting corrected STL/3MF/GLB is needed before approved models are printed or used elsewhere.
- **Evaluation:** no formative or summative usability evaluation yet. IEC 62366-1 summative tests need at least 15 users per user group (REGULATORY_PATH §4).
- **Scope:** the viewer shows every structure in the result; the draft intended use covers bony anatomy only. The device build should restrict what is shown and approved to the intended-use structures.

## 7. Traceability (start)

| Requirement | Implemented in | Verified by |
|---|---|---|
| Models open as not approved | `src/review/review.ts` (`isApproved`), `src/ui/StatusBar.tsx` | `device.spec.ts` |
| Approval bound to version hash | `src/review/review.ts` (`versionOf`), `src/lib/hash.ts` | `device.spec.ts` |
| Edits: exclude, erase, undo | `src/App.tsx` (`commit`), `src/model/engine.ts` (`setErasures`, `surfacePoint`) | `device.spec.ts` |
| Labelling | `src/lib/build.ts`, `src/ui/copy.ts` | `device.spec.ts` |
| Study identifiers | `src/ui/StatusBar.tsx`, manifest `study` | `device.spec.ts` |
| Orientation markers | `src/lib/orientation.ts`, `src/ui/OrientationMarkers.tsx` | `device.spec.ts` |
| Reference mode, offline | `src/App.tsx`, `public/sw.js`, `src/lib/offline.ts` | `device.spec.ts` |
