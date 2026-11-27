# CT-to-3D platform: plan for a hosted, any-body-part service

Research use, not for diagnosis or surgical planning (until licensed; see [REGULATORY_PATH.md](REGULATORY_PATH.md)).

Status: plan initialized 2026-11-22; implementation snapshot updated 2026-11-26. Owner decisions recorded below.

## Implementation snapshot (2026-11-26)

This is the current code state, not a claim of clinical readiness or complete anatomy coverage:

- Intake has registered SimpleITK volume and ZIP DICOM-series adapters. It writes a NIfTI derivative and
  records source/canonical geometry and conversion history. ZIP packages can hold paired files and multiple
  DICOM series; selection still follows the documented primary-series policy.
- Intake now uses a rough 24-bytes-per-voxel planning estimate against `CTAPP_MAX_WORKING_BYTES` (default
  2 GiB); `CTAPP_MAX_INPUT_VOXELS` remains an optional additional cap. It covers intake-side buffers only,
  not model-specific workspace. It is not streaming inference or a guarantee that every accepted scan fits.
- The CT app uses TotalSegmentator and schedules MOOSE peripheral bones for its `skeletal` profile. The
  hosted Docker image currently excludes `moosez`, so that optional task is unavailable there; the result
  must say so and must not imply complete peripheral-bone coverage. MOOSE terms/weights remain an explicit
  deployment decision in the owner decisions below.
- Region marker patterns and model-task triggers are now in the packaged `planning_catalog.json`; each task
  is checked against the model-to-anatomy label map and the model allowlist. This removes a second hard-coded
  task table, while the current marker definitions still need broader validation against focused scans.
- Anatomy status distinguishes segmented, partial, not scheduled, unavailable, failed, and not detected.
  A missing label does not prove absence; field-of-view status remains `not_assessed` until a validated
  method can establish whether an anatomy is in the scan.
- The app produces scan-linked segmentation meshes, but the finding-analysis sidecar remains
  `not_scheduled`. No fracture-detection model is integrated or validated. The project therefore does not
  yet meet the goal of reliable hairline-fracture visualization.
- Benchmark alignment now compares the full physical grid (size, spacing, origin, direction), not only
  array dimensions. This guards the evaluation pipeline; it does not establish model accuracy.

The table in section 1 is a **historical pre-migration audit**, not a description of the current code.

**Owner decisions**
- **Goal (2026-11-21):** a regulated **medical device** that doctors use before surgery (planning) and during surgery (reference display), give feedback on, and that grows toward surgical use. The route, stages and first intended use are in [REGULATORY_PATH.md](REGULATORY_PATH.md), which overrides the "research only" recommendation in section 2.1 below. Until a licence or a permitted clinical investigation exists, outputs stay labelled research use, and the engineering follows device design controls from now on.
- **Commercial:** yes, the service may be commercial. Only commercial-safe weights are allowed (section 3). For the device build, only TotalSegmentator's Apache `total` task until MOOSE, NVIDIA and CADS terms are confirmed in writing.
- **Wording:** a copywriting pass chooses the public wording. It must make no clinical claims before licensing.
- **Tiers:** browser by default, de-identified upload as opt-in, now. Migrate later as needed.
- **Cloud:** Google Cloud account with ₹30,000 in credits, expiring 13 January 2027. Use them for GPU inference and rendering-heavy work, starting with S1/S2 benchmarks and the S4 GPU worker, before they expire. At the estimated $0.20–0.60 per full scan that is very roughly 600–1,700 scans (to be re-measured).
- **Location:** the platform lives inside the `emgmirror` repo for now.
- **Data controller:** the owner.
- **Where work runs:** development runs in Claude Code cloud sessions and GPU jobs on Google Cloud, not on the 2-core laptop. Only public datasets are used there; the owner's own CT never leaves the laptop.
Evidence: five reports in [research/](research/):
- [audit.md](research/audit.md): the current code;
- [seg.md](research/seg.md): segmentation models;
- [render.md](research/render.md): viewers and meshing;
- [host.md](research/host.md): hosting, privacy, regulation and licences;
- [critique.md](research/critique.md): the adversarial re-check.

Where they disagree, **critique.md wins**, and this plan follows it.

## 1. Historical baseline before the CT platform migration

| Part | Today | Verdict |
|---|---|---|
| Segmentation | Classical HU thresholds, 5 tissue classes (fat, "muscle", bone cortex/medulla, metal), bone naming tuned on one forearm | **Replace.** Lungs and bowel gas become "fat"; contrast-filled vessels can become "bone"; organs, vessels, brain and individual muscles are not separated. |
| Meshing | Binary marching cubes, Taubin smoothing, then two decimations; soft tissue on a 1 mm grid | **Rewrite.** Staircase artefacts and lost detail. Keep the mesh QC gates. |
| Viewer | Hand-written WebGL in one huge self-contained HTML file: two fixed lights, orthographic view, every bone the same off-white, milky non-sorted transparency, ~1 mm slices with 2 fixed windows | **Replace.** This is why it "looks bad". |
| Backend | FastAPI on 127.0.0.1, one thread, one job at a time (43–66 min each), JSON files as the database, an endpoint that reads any server folder, no users, no de-identification, files kept forever | **Replace** for hosting. |
| DICOM geometry, tilt detection, mm-based validation, mesh QC, 151 tests, plain-language error layer, provenance | Solid | **Keep** and reuse. |

## 2. Decisions the owner must make first

1. **Intended use.** "Post-diagnosis medical analysis" is a medical-device purpose:
   - India: CDSCO software as a medical device, class B/C likely;
   - US: FDA 510(k), product code LLZ, Class II;
   - EU: MDR Rule 11, class IIa/IIb.

   A disclaimer does not change this: regulators judge every claim the site makes.
   **Superseded (2026-11-21):** the owner chose the medical-device route. See [REGULATORY_PATH.md](REGULATORY_PATH.md). The rule that still holds: no clinical claims anywhere until a licence or a permitted clinical investigation covers them.
2. **Commercial or not.** This decides which model weights are legal on the service. The recommended stack (section 3) is commercial-safe. Strictly non-commercial use would also allow nnInteractive and the licensed TotalSegmentator tasks (hands, feet, muscle/fat, high-res heart), but they would have to be removed later if you go commercial.
3. **Where the scan is processed.**
   **Recommendation:** two tiers.
   - **Quick, the default:** the model runs inside the user's browser, and the scan never leaves their device.
   - **Full, opt-in:** the browser removes names, dates and IDs from the scan, then uploads it to a GPU server, which deletes it after 24 h (results after 7 days).
4. **Budget and region.** A GPU server that shuts down when idle costs nothing between scans. Budget about **$0.20–0.60 per full scan** until measured (the earlier "$0.04" covered only one model). India region first.

## 3. Recommended stack (licences re-verified, see critique.md)

| Layer | Choice | Licence | Confidence |
|---|---|---|---|
| Main segmentation | **TotalSegmentator v2 `total`**: 117 structures, whole body, 1.5 mm model. Add open subtasks by detected region: `body`, `lung_vessels`, `vertebrae_pp_refined`, `craniofacial_structures`, `headneck_*`, `head_glands_cavities`, `trunk_cavities`, `liver_segments`. MR scans: `total_mr` (50 structures). | Code and these weights Apache-2.0 | High |
| Hands, forearms, feet, lower legs, muscles, fat | **MOOSE 3.2**: `clin_ct_peripheral_bones` (31 labels incl. radius, ulna, carpals, fingers, tibia, toes), `clin_ct_muscles`, `clin_ct_body_composition`. Run only when that region is in the scan. | Weights CC BY 4.0, code Apache-2.0; confirm per-site data terms with the authors in writing | Medium |
| Second opinion / extra head-neck structures (full tier) | **CADS `open`** weights, pinned with `-license open`. The default `reference` weights and CADS-region are not commercial-safe. | CC BY-SA 4.0 | Medium-low |
| Click-to-fix (phase 2) | **NV-Segment-CT (VISTA3D)** at a pinned revision | NVIDIA Open Model License: commercial use allowed, model card says research-only | Medium |
| **Never on the service** | nnInteractive, MedSAM2, VoxTell, SuPreM, NV-Segment-CTMR, licensed TotalSegmentator tasks (incl. `face`, `appendicular_bones`, `tissue_types`), MeshLib | Non-commercial | — |
| Classical HU segmenter (today's code) | Retire from the default path; keep as a test baseline and for metal-artefact flags | own | High |
| Label names and colours | SNOMED codes and colours from SlicerTotalSegmentator's `.term.json` (Apache-2.0), Slicer General Anatomy colours | Apache-2.0 | High |
| Meshing | VTK ≥ 9.5 **`vtkSurfaceNets3D`**: one closed surface per label at native resolution. Fallback: discrete flying edges + windowed-sinc smoothing. One decimation pass into web and print versions (meshoptimizer). Repair with trimesh and manifold3d. QC in mm (HD95, mean surface distance, volume change) using the existing `mesh_qc`. | BSD / MIT / Apache, no GPL in anything shipped to browsers | High (smoothing defaults need tuning) |
| Exports | GLB via glTF-Transform (meshopt compression, metres, glTF axes, SNOMED code and colour in metadata), STL and 3MF for printing, NIfTI and **DICOM-SEG** (highdicom) for clinical tools | MIT | High |
| Viewer | React app: **three.js / React-Three-Fiber** for the 3D model (PBR materials, ambient occlusion, clipping planes with capped cut faces, the CT slice drawn inside the 3D view, click-to-isolate, volume in mL) plus **NiiVue 1.0** for slices (window/level drag, zoom, oblique MPR, volume rendering). Move slices to **Cornerstone3D/OHIF** in phase 2 for DICOM-SEG and clinical measurements. | MIT / BSD-2 | Medium (NiiVue 1.0 is a release candidate) |
| Browser tier | **BrowSeg**: TotalSegmentator compiled to WebAssembly/WebGPU, Apache tasks only | Apache-2.0 | Medium: identical results proven only for liver tasks; needs our own check on `total`; Chrome/Edge only; ~3.7 GB RAM; no compressed or multi-frame DICOM yet |
| Backend | FastAPI (stateless), resumable upload (tus) of the **de-identified NIfTI**, S3-compatible storage with encryption and automatic deletion, Redis + Arq job queue, Postgres for job records, live progress via SSE | permissive | High |
| GPU worker | Container with weights baked in, TotalSegmentator usage statistics **off**, no internet access, 24 GB GPU (L4/A10G), one job at a time, time and memory limits; serverless provider that scales to zero (Modal or RunPod; region availability to check) | — | High for the pattern, low for cost until measured |
| Demo gallery | Pre-computed results from the TotalSegmentator dataset (1,228 CTs, **CC BY 4.0**) | CC BY 4.0 | High |

## 4. Safety and privacy rules for the hosted service

### Optional findings/reasoning layer

Keep structured finding analysis separate from segmentation and meshing. A future region-aware 3D model may produce concise observations and evidence locations, but it must not be treated as a fracture segmenter or a substitute for human review. The optional result contract and coordinate-mapping gates are in [REASONING_WORKFLOW.md](REASONING_WORKFLOW.md). Do not schedule a model by default until scope, terms, and source-coordinate round trips are reviewed.

- **Upload path.** No raw DICOM ever reaches the server:
  - the browser de-identifies (DICOM PS3.15 Annex E), drops scouts, screenshots and dose reports, converts to NIfTI, and uploads that;
  - head scans: the face is cropped or skin is never rendered, because a head CT surface is a recognisable face (no commercial-safe defacing model is verified yet).
- **Remove from today's code before any hosting:**
  - the `{"path": ...}` job, which reads any server folder;
  - the localhost guard as the only access control;
  - "Your scan stays on this computer" wording in server mode.
- **Access and logging:**
  - jobs use unguessable IDs and short-lived signed links;
  - a "delete now" button that gives a deletion receipt;
  - logs carry the job ID only.
- **Untrusted files:**
  - DICOM is read in a sandbox, with size caps checked *before* memory is allocated;
  - only listed transfer syntaxes are accepted, nested zips are refused, and the real decompressed size is checked.
- **Abuse limits:** rate limit and CAPTCHA on upload; a budget alarm.
- **Legal paperwork:**
  - a DPDP Act consent screen and a DPIA;
  - parental consent for minors;
  - a NOTICE page with model attributions;
  - a penetration test before launch.
- **Owner's own scan.** The owner's own CT (`01_clinical_CT/`) is never used on the hosted service or in cloud development. Development and tests use public datasets only.

## 5. Quality: what "many times better" means, measurably

| Measure | Today | Target |
|---|---|---|
| Structures | 5 tissue classes, forearm bone names | 117+ named structures (organs, bones, vessels, muscles), any region |
| Accuracy | self-consistency only | Dice and surface distance per structure on public labelled CTs, published; TotalSegmentator reports mean Dice ≈ 0.93 on its own test split |
| Time | 43–66 min on the laptop | Under 5 min server (full tier); a few minutes in the browser (quick tier) |
| Mesh | marching cubes, double decimation | closed, smooth, per-structure meshes at native resolution; surface error in mm reported per structure |
| Viewer | flat lighting, one colour, 1 mm slices | PBR + ambient occlusion, anatomical colours, clipping with CT slice, W/L, zoom, volume rendering, mobile-capable |
| Honesty | — | Per-structure confidence badge (model agreement, volume range, mean HU, cut by scan edge); banners for children, slices thicker than 3 mm, metal, MR, cone-beam CT |

Known limits to state loudly:
- The models segment **normal anatomy**. Tumours (except the open liver-lesion and lung-nodule tasks), fractures, implants and amputations will be mis-segmented.
- Adults only, as validated.
- Scans with slices thicker than 5 mm are refused.

## 6. Milestones

The laptop has 2 cores and no GPU, so model work (S1, S5) runs in a cloud dev environment on public data. Each milestone ends with tests and a short report.

| # | Milestone | Done when |
|---|---|---|
| S0 | Decisions (section 2) + new repo layout | Owner signs off. Suggest a separate repo/package (`ctplatform`), with `dicom.py`, `validate.py`, `mesh_qc` and the security tests ported. |
| S1 | Segmentation engine | One command: NIfTI in, label map plus per-structure JSON out. Region detection, then TotalSegmentator `total` + region subtasks, then MOOSE where limbs are present; one shared label table (SNOMED); QC flags. Benchmarked on 20–50 public CTs from different regions, with Dice/NSD per structure. |
| S2 | Mesh pipeline | SurfaceNets per label → GLB, STL, 3MF, NIfTI, DICOM-SEG. Every mesh passes `mesh_qc` (closed, no self-intersections), HD95 ≤ 1 voxel against the label map, correct patient-mm placement (tested on oblique phantoms). |
| S3 | Viewer | React + three.js model view + NiiVue slices, from the S2 outputs. Loads in under 3 s on a mid-range phone for the web version. Screenshot comparison against today's viewer. |
| S4 | Backend + GPU worker | Upload → queue → GPU → results → auto-delete, end to end in a staging environment with public data. Load test with 10 parallel jobs; security tests ported and passing. |
| S5 | Browser tier | BrowSeg `total` result compared against server TotalSegmentator on 20 public CTs (voxel agreement reported). Capability check routes weak devices to the gallery or the full tier. |
| S6 | Public research demo | Privacy policy, terms (research use only), consent, DPIA, NOTICE page, pen test, published benchmark, India region. |
| P2 | Clinical research partners | On-premises or single-tenant deployment (MONAI Deploy or Kaapana), hospital-side de-identification, OHIF/Cornerstone3D, VISTA3D click-to-fix, ethics approval, data processing agreements. Start the quality-system documents (ISO 13485 / IEC 62304 / ISO 14971) here. |
| P3 | Regulated product (optional) | One narrow indication first (e.g. bone 3D models for orthopaedic planning, as Axial3D did), CDSCO licence, FDA 510(k) with Mimics/ScanIP/Axial3D as predicates. |

## 7. Open questions for the owner

1. Do you accept **research-and-education-only** wording for the public site, with "post-diagnosis analysis" moved to a later regulated product?
2. Commercial plans: yes, maybe later, or never?
3. Is the two-tier design OK: browser by default, de-identified upload as opt-in?
4. Monthly cloud budget for GPU and storage, and do you have a cloud account (or should the first demo use Grand Challenge's free algorithm hosting)?
5. A new separate repo for the platform, or a folder inside `emgmirror`?
6. Who will be the data controller (you personally, a university, a company)? This matters for the DPDP paperwork.
