# Audit of the current CT-to-3D pipeline (read-only, 2026-11-21)

Files read: `src/emgmirror/ct/{webapp,pipeline,dicom,segment,mesh,viewer,labels,explain(part)}.py`, `static/ct.html`, `static/viewer_template.html`, `docs/CT_TO_3D.md`, `docs/VALIDATION.md` (this one covers EMG simulator validation, not CT, so it doesn't apply here), `docs/EMG_ON_CT.md`, and the test names in `tests/test_ct_*.py` (151 tests). I opened no patient data. The audit made no web claims except where a URL is cited.

## 1. Architecture as-is

**Data flow**
1. `ct.html` calls `POST /api/ct/jobs` (webapp.py:284). It then sends one `PUT /api/ct/jobs/{id}/files?name=` per file, raw body, 3 parallel JS workers (ct.html:158-168). Zips are unpacked on the server (webapp.py:332-342). Alternatively, `{"path": "C:/..."}` makes the server read a folder on its own disk (webapp.py:295-298).
2. `POST /start` (webapp.py:358) puts the job on an in-process `queue.Queue`. One daemon thread runs one job at a time, forever (webapp.py:218-233).
3. That thread runs `subprocess.run([python, -m, emgmirror.ct.pipeline, --input, --out, --status])` with no timeout and no memory limit (webapp.py:127-132).
4. `pipeline.build` (pipeline.py:494-630) runs these steps:
   - `dicom.scan_series`: header-only pydicom read of every file, grouped by SeriesInstanceUID.
   - `choose_primary`, `choose_soft`, `choose_coarse`.
   - `load_series` to an int16 HU `sitk.Image`, written to `volume.nii.gz`.
   - `segment()` writes `labels.nii.gz` and `labels.json`.
   - `hu_sanity`.
   - `_structures` / `_mesh_all`: per structure, marching cubes, Taubin smoothing and decimation, then export to STL/OBJ/PLY plus `combined.glb`.
   - Validation: `bone_sanity`, `enclosed_tissue`, a threshold sweep, edge sensitivity, and full re-segmentation of every coarse series (`_coarse_checks`).
   - `explain.build_summary`, then `_viewer_volume` (at most 6 M voxels), then `viewer.write_viewer` (one self-contained HTML).
   - `report.md`, `results.json`, `summary.json`.
5. Progress goes to `progress.json` (pipeline.py:45-79). The page polls `GET /api/ct/jobs/{id}` every 2 s (ct.html:193-205). When the job is done, the page shows `summary.json` tiles and loads `/viewer` in an iframe (ct.html:230). Files download through `/files/{path}`.

**Job model**
- One job per folder: `results/ct_jobs/<16 hex>/{status.json, progress.json, log.txt, upload/, out/}` (webapp.py:160-256).
- State machine: `created → queued → running → done|failed`.
- `status.json` is guarded by an in-process `threading.RLock` and atomic `tmp.replace` with Windows retry loops (webapp.py:147-157).
- On startup, jobs left queued or running are marked failed if `server_pid` is dead (webapp.py:263-268).

**Storage:** the local filesystem only. No database, no object store, no retention or TTL.

**What runs where:** everything runs on one machine. Uvicorn is bound to `127.0.0.1:8010` (cli.py:463). Pipeline compute happens in a child process. The viewer is fully client-side WebGL plus Canvas2D. Runtime: 43-66 min per full build on a 2-core, 7.6 GB laptop. Validation is about half of that and segmentation about a third (CT_TO_3D.md §6).

Doc and code disagree on meshing: the doc says "Independent structures are meshed in separate processes (2 workers)" (CT_TO_3D.md §4), but `_mesh_all` is a sequential loop (pipeline.py:193).

## 2. Segmentation actually used

The method is classical, with no learning (segment.py:1, "no learning"). Every threshold is measured from the scan's histogram.

**Labels (labels.py:1-9)**
- 0 background, 1 fat, 2 "muscle" (all lean soft tissue, skin shell included), 4 metal.
- `10+2k` cortical and `11+2k` medullary for bone k, with at most 100 bones (labels.py:14).
- There are **no organs, vessels, lungs, brain, airway, or individual muscles**.

**Steps (segment.py:1072-1246)**
- **Padding detection** (`_padding`, :351).
- **Body:** threshold at half-way between the air and soft-tissue peaks, clamped to [-700, -300] (:380). A 4 mm opening on a 1 mm coarse grid (:42, :393) is followed by component filtering on volume ≥ 2 cm³ and median HU in [-300, 300] (:46-47, :402). Internal holes are filled 3D-capped plus slice-wise (:453-454).
- **Bone thresholds** (`_bone_thresholds`, :594-628):
  - Lean peak in [-50, 150] and cortical peak in [700, 2600].
  - High (seed) threshold = (lean + cortical) / 2.
  - Low threshold = `max(lean + 6σ, 150 HU)` (:622).
- **Metal:** seed ≥ `max(3000 HU, cortical tail + 500)` (:50-52), with a per-object half-maximum edge.
- **Bone mask:** hysteresis (`_bone_low`, :631-658), with no bone within 1 mm of air (:61, :641-647). Then marker/watershed-style separation (`_separate`, :699) and per-bone envelopes with hole filling (`_envelopes`, :982). The outer layer is re-cut at the local half-maximum, following Hangartner 2007 (:71-73, `_local_surface`, :1025).
- **Cortical vs medullary:** Otsu of envelope HU after 0.5 mm normalised-convolution smoothing (:1162-1184).
- **Fat vs lean:** half-way between in-body fat and lean peaks, falling back to -30 HU (:1307-1316), after a 0.5 mm median filter. Uses the soft-kernel series when one exists on the same grid (:1256-1270). Artefacts near metal are in-painted (:1332-1349).

**Body-part assumptions**
- **Naming is forearm-only.** `_name_bones` (:1457-1583) needs exactly two long bones parallel to the limb axis (:1485), then votes radius, ulna, `humerus_distal` or `carpals_hand`. Any other anatomy gets `bone_1..n`.
- pipeline.py:431: `guess = "forearm" if ... radius/ulna ... else "unknown"`.
- `explain._KNOWN_BONES` holds only arm bones (explain.py:22-31). The limits text says "tuned on one forearm scan" (explain.py:56, report.py:33).
- Several defaults are limb-tuned: `skin_mm = 1.5` ("forearm dermis", :91), `body_open_radius_mm` justified by finger thickness (:42-43), and `skin_bone_gap_mm` measured on "this forearm" (:61-63).

**Concrete failures on other body parts (from reading the code; not run)**
- **Lungs, bowel gas and airway become "fat".** The body hole fill (:453-454) pulls internal air into `body`. Then `fat = softc & (val < t_fl)` (:1357), and air is far below t_fl. The only reaction is a flag (:1354-1356).
- **Contrast-filled vessels, heart chambers and enhancing kidneys can become "bone".** Iodine at roughly 150-400 HU passes the low threshold (≥150 HU, :622). It only needs to connect to a voxel at or above the seed threshold.
- **Brain and every abdominal organ collapse into label 2 ("muscle").**
- **The [-300, 300] median-HU body-piece rule (:47, :402) is untested on lung-heavy chest slabs.** UNVERIFIED whether a thorax passes.
- **Touching vertebrae and ribs:** separation depends on 1 mm distance-map saddles (:76-77). This is untested on the spine.

## 3. Mesh generation and its quality problems

**Pipeline (mesh.py:82-144)**
- Crop to the bounding box with a 1-voxel zero pad, so surfaces close at the scan edge (:95).
- `skimage.measure.marching_cubes(vol, level=0.5)` runs **on the binary mask cast to float** (:95, :102).
- Index-to-LPS mapping: `origin + (idx*spacing) @ D.T` (:104-106). This is correct for oblique directions; test `test_non_identity_direction` covers it.
- Merge vertices, drop degenerate faces, invert if volume < 0 (:108-112). Drop islands below `min_component_voxels` (:113-114).
- Taubin λ/μ smoothing: 20 iterations, λ 0.5, μ -0.53, uniform umbrella weights (:28-30, :155-177).
- `fast_simplification` decimation to `max_faces` (:69-79, :131-137).

**Parameters (pipeline.py:36-42)**
- Bone and metal at native resolution: 400 k faces, islands below 30 voxels dropped.
- Soft tissue resampled to **1 mm** and thresholded at 0.5 volume fraction (pipeline.py:204-211): 250 k faces, skin islands below 2000 voxels dropped.
- **Second decimation for the viewer:** bone 120 k, skin 80 k, muscle 100 k, fat 100 k, metal 80 k, medullary 50 k (pipeline.py:42, :391-396). If `_decimate` throws, the error is swallowed silently (:393-396).

**Coordinate frames:** everything is DICOM LPS mm (mesh.py:3, README at :369-372). NIfTI gets LPS-to-RAS through SimpleITK (dicom.py:347-352), which is correct. **GLB is written in LPS millimetres with no transform** (mesh.py:338-367). glTF 2.0 defines metres, +Y up and right-handed (https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html, "Coordinate System and Units"). In standard glTF viewers the model therefore shows up 1000× too large and lying on its side. STL/OBJ in LPS are fine for Slicer if the user picks LPS (Slicer's default STL frame is UNVERIFIED).

**Quality problems**
1. **Binary-mask isosurface.** The surface comes from a 0/1 mask, not the HU field, so sub-voxel edge information is thrown away. The result is terracing at each voxel step, which 20 uniform Taubin iterations only soften (mesh.py:95-102, 119-127).
2. **Soft tissue on a 1 mm grid** (pipeline.py:36) gives blocky skin, muscle and fat. The reason given is RAM ("does not fit in 7.6 GB").
3. **Two decimation passes** with plain QEM (`fast_simplification`, no feature or boundary weights) lead to faceting and slivers.
4. **Each label is meshed independently**, so adjacent surfaces (muscle/fat/bone) neither share boundaries nor nest. The result is gaps and overlaps. Muscle and fat are "display surfaces, not closed solids" (CT_TO_3D.md §6).
5. **Self-intersections remain** in bone meshes and are only reported (`self_intersections`, mesh.py:206-261; doc §6). No repair or remeshing is done.
6. **Muscle is one lean blob that includes skin**, so there is no anatomical surface for individual muscles.
7. **Files written per structure:** STL, OBJ, PLY plus GLB, with no per-vertex normals and no label metadata beyond node names. The GLB also has no LOD and no Draco/meshopt compression.

## 4. Viewer and rendering

**Library:** a hand-written WebGL1/2 renderer with no third-party JS (CT_TO_3D.md §2), plus Canvas2D for the slices. Everything ships as **one self-contained HTML file**. All data is gzip+base64 inside a `<script type=application/json>` (viewer.py:35-36, 104-112; template 139, 159-216), served into an iframe (ct.html:101, 230).

**What it renders:**
- One mesh per bone (all the same colour), a hidden medullary mesh per bone, and metal.
- **Skin, visible by default at 18 % opacity** (pipeline.py:177). Muscle (45 %) and fat (35 %) start hidden.
- Three orthogonal MPR slices from a downsampled volume, with an optional label overlay.
- A 2-point measure tool in 3D and a ruler in 2D.

**Why it looks bad**
1. **Primitive shading** (template 609-613):
   - Fragment shader at `precision mediump`.
   - Two fixed directional lights, ambient constant 0.30, Blinn spec at power 24 ×0.18.
   - `if(n.z<0.0)n=-n` forces two-sided lighting, which hides winding errors and flattens the look.
   - No SSAO, shadows, environment lighting, tone mapping or fog.
2. **Orthographic projection only** (template 637). No perspective depth cue.
3. **Every bone has the same colour,** `L.COLORS["cortical"] = #efe9dc` (pipeline.py:167; labels.py:16). Adjacent bones can't be told apart, and the off-white has low contrast against the light canvas `#ebeeed` (template 9).
4. **Transparency is not order-independent.** Meshes are sorted per object by bounding-box diagonal and centroid depth, then drawn in front-cull and back-cull passes (template 650-659). With the default-on 18 % skin over everything, the result is a milky veil and popping or incorrect overlaps.
5. **Mesh input is already degraded:** binary marching cubes, a 1 mm soft grid and two decimations (section 3). The JS recomputes area-weighted vertex normals without a crease angle (template 193-203), which gives shading noise on slivers.
6. **Small viewport.** The 3D view is one cell of a 2-column grid with `min-height: 380px` (template 31, 36), nested in an iframe of `min(78vh, 820px)` (ct.html:41).
7. **Slices are low-resolution and rigid.**
   - Downsampled to at most 6 M voxels (pipeline.py:39, 379), about 1 mm against the native 0.3-0.47 mm.
   - Two hard-coded windows, bone [500, 2000] and soft [40, 400] (template 245). No W/L drag, no 2D zoom or pan.
   - Smoothing quality is 'medium' (template 387).
   - No volume rendering, MIP or oblique MPR.
8. **Performance architecture.**
   - The whole volume, labels and meshes are inlined as base64, about +33 % size, then decoded on the main thread at load.
   - CPU brute-force ray picking over every triangle of every visible mesh, on each click (template 548-578).
   - No BVH, no LOD and no streaming.

## 5. Backend weaknesses for hosting

| Issue | Where | Consequence when hosted |
|---|---|---|
| `{"path": ...}` job reads any server folder | webapp.py:295-298, 244 | Arbitrary server-side directory read and enumeration. Must be removed. |
| Localhost-only Host/Origin guard is the only access control | webapp.py:42, 54-68, 269 | No auth, users, sessions or CSRF tokens. On a public host it either blocks everyone or, if loosened, exposes everything. |
| No per-user isolation | `list_jobs` returns every job (webapp.py:275-282). `DELETE`, download and viewer only need the 64-bit job id. | Any user sees and deletes others' scans (PHI exposure). |
| Single process, single worker thread, one job at a time | webapp.py:166-168, 218-233 | A 43-66 min job blocks every other user. The RLock is per process, so several uvicorn workers would race on `status.json`. |
| Subprocess with no timeout or rlimit | webapp.py:130-132 | A hostile or huge scan hangs or OOMs the only worker. |
| Job persistence = JSON files with Windows retry loops | webapp.py:135-157; pipeline.py:82-92 | Not crash-safe across nodes. No DB, no queue broker, no restart or resume. A PID-liveness check (webapp.py:263-268) is meaningless across hosts. |
| Windows-specific code | `_pid_alive` via `ctypes.windll.kernel32` (webapp.py:101-124); `GetShortPathNameW` and the ASCII-path workarounds (dicom.py:320-391); `WINDOWS_RESERVED` names (webapp.py:43, 75) | Harmless on Linux (guarded) but dead weight. The PID check is wrong in containers. |
| Upload limits | 6 GB and 20 000 files per job (webapp.py:44-45). No global disk quota, rate limit, per-user cap, or retention/TTL (data kept "until the user deletes them", webapp.py:5-6). | Easy disk-filling DoS. Indefinite PHI retention. |
| Upload protocol | One PUT per file, each rewriting `status.json` under the lock (webapp.py:301-356) | Thousands of requests and disk rewrites per study. No resumable or multipart upload (S3 presigned / tus). |
| Zip handling | `safe_unzip` (webapp.py:81-98): paths are sanitised and indexed (good). The size check uses **declared** `file_size` from the central directory (:86, :97). | Real bytes are bounded only if CPython's `ZipExtFile` stops at the declared size (UNVERIFIED for the deployed Python). 6 GB of decompressed budget per job is far too large for multi-tenant use. No nested-zip or file-type allowlist. |
| Path traversal | Upload names go through `safe_relpath` (webapp.py:71-78). Download does `resolve()` plus a parent check (webapp.py:379-385). `os.walk` does not follow symlinks (dicom.py:217). | Adequate, and tested. Keep. |
| Untrusted DICOM parsing | Header read: preamble `DICM` check plus `force=False` (dicom.py:62-73), good. Full `pydicom.dcmread` plus `pixel_array` with no size limits (dicom.py:300-301). `Rows`, `Columns` and slice count are uncapped before `np.empty((n, rows, cols))` (dicom.py:298). | Memory DoS from crafted headers. Compressed transfer syntaxes run third-party codecs on untrusted bytes (which handlers are installed: UNVERIFIED). |
| Format coverage | Every file is treated as one slice. Enhanced/multi-frame CT (per-frame IPP in functional groups) fails "ImagePositionPatient missing" (dicom.py:159-162). No NIfTI/NRRD input. | Many modern scanner exports are rejected. |
| PHI | Raw uploads keep the full DICOM headers on disk with no de-identification. `log.txt` holds tracebacks with paths (pipeline.py:649). The UI says "Your scan stays on this computer" (ct.html:56). | Needs anonymisation on ingest, encryption at rest, consent and retention policy, and honest UI copy. |
| Polling cost | Every `GET /jobs/{id}` re-reads `summary.json` and `rglob`s outputs (webapp.py:362-370) | Fine locally, wasteful at scale. |
| Viewer served as one huge HTML | viewer.py:104-112 | Can't be cached or CDN'd, and can't be lazy-loaded. |

## 6. What is good and worth keeping

- **DICOM geometry discipline** (dicom.py):
  - Geometry comes only from IOP/IPP, never SliceThickness (:1-4, :209-210).
  - Consistency checks on matrix, PixelSpacing and IOP across slices, plus an orthonormality check (:144-169).
  - Duplicates dropped with a tolerance (:173-181). Gap-based stack splitting (:82-95).
  - **Gantry-tilt and shear detection** by line fit with jitter tolerance (:98-117, 191-200). Tilted stacks are refused with a plain message (:241-246, :294-295).
  - Rescale slope/intercept handled (:302-307).
  - Primary series chosen by finest voxel volume, then sharp kernel, then slice count, with a logged reason (:239-257). Soft-kernel pairing on the same grid (:260-280).
  - Tests: `test_gantry_tilted_series_is_reported_and_not_loaded`, `test_small_position_rounding_is_not_tilt`, `test_one_decimal_position_tags_flipping_by_0_1_mm_are_not_tilt`, `test_coronal_reformat_same_physical_point`, `test_nifti_roundtrip_and_affine`.
- **NIfTI I/O** with correct LPS-to-RAS conversion and the disclaimer in `descrip` (dicom.py:347-372).
- **Validation harness** (validate.py), which is reusable with any segmenter:
  - `accuracy_floor` (:149).
  - `overlap_metrics` (:299): Dice, MSD and HD95 **in mm**, restricted to the common FOV, with signed or asymmetric distances.
  - `threshold_sweep` (:441), `hu_sanity` (:495), `bone_sanity` (:561).
  - `mesh_vs_mask` (:653), with correct handling of the scan-edge cap.
  - `enclosed_tissue` (:691). `volume_fraction_resample` (:423).
  - Independent coarse-reconstruction comparison matched by overlap, not name (pipeline.py:236-318).
  - Edge-sensitivity in mm (pipeline.py:336-348).
  - The doc labels all of this honestly as self-consistency, not accuracy (CT_TO_3D.md §5-6).
- **Mesh QC:** watertight, winding, Euler number, edge-connected components, a vectorised self-intersection count, volume-vs-voxels %, smoothing displacement in mm, and volume change per stage (mesh.py:35-40, 115-142, 206-295). Keep these as QA gates.
- **Analytic phantom** (phantom.py) and **151 tests** across dicom (14), segment (16), mesh (14), validate (14), webapp (32), pipeline (12), report (14), explain (24), viewer (7) and emg_bridge (4). The webapp security tests are worth keeping as a regression suite: `test_zip_upload_is_unpacked_without_escaping_the_job`, `test_upload_names_cannot_escape_the_upload_folder`, `test_download_cannot_read_outside_the_output_folder`, `test_no_unguarded_routes`, `test_damaged_or_encrypted_zip_is_refused_and_leaves_nothing`.
- **Error reporting:** plain-language `ValueError`s surfaced to the UI (pipeline.py:646-652). A failed build keeps the last good output (pipeline.py:540; test `test_a_build_that_cannot_read_its_input_keeps_the_last_good_build`). Per-step timings and weighted progress (pipeline.py:31-79).
- **Provenance:** every parameter and measured threshold, with its reason, goes into `results.json` and `report.md` (pipeline.py:610-627). A disclaimer is in every output header (mesh.py:303-335).
- **Plain-language summary layer** (explain.py): defensive, deterministic, with a glossary and limits. Reusable once the body-part-specific text is generalised.
- **Viewer bits:** a sound payload format (uint16 vertex quantisation, error at most extent/131070, viewer.py:8-12) and correct LPS↔index math and orientation letters in MPR (template 221-234, 312-327). Some UX ideas carry over: structure groups, per-structure explanation and opacity, scale bars, an HU readout.
- **Metal handling ideas:** detects clipped vs extended HU scale (segment.py:1094-1099), adds a blooming rim, and in-paints the artefact region (segment.py:1332-1349).

## 7. Component decisions

| Component | Decision | Reason |
|---|---|---|
| `dicom.py` scan, geometry, tilt, series choice | **Keep** (harden) | Correct and well tested. Add size caps before allocation, multi-frame (Enhanced CT) support, de-identification on ingest, a transfer-syntax allowlist, and optionally tilt *correction* by resampling instead of refusal. |
| `write_nifti` / `read_nifti` | **Keep core, drop the Windows 8.3 hacks** | LPS→RAS is correct. The ASCII-path workaround isn't needed on Linux. |
| `segment.py` classical segmenter | **Replace** (keep as fallback or QA baseline) | Only 5 tissue classes, forearm-tuned. Lungs become fat (:453-454, :1357) and contrast vessels can become bone (:622). Can't reach "any body part, organs, muscles, vessels". Swap in a pretrained multi-organ CT model. Candidates and their code vs weight licences are for the research task; none were verified in this audit. The doc's earlier notes on licence-gated, non-commercial weights for some tasks are in CT_TO_3D.md §2. |
| `_name_bones` forearm naming | **Remove** | The model's own label set replaces it. |
| `labels.py` uint8 scheme | **Rewrite** | Needs a label table of 100+ classes with standard names and colours (e.g. SNOMED/FMA-style IDs) instead of the 10+2k bone encoding. |
| `mesh.py` marching cubes, Taubin, decimation | **Rewrite** | Use multi-label surface extraction from smoothed or probabilistic masks (or the HU field for bone) at native resolution. Use feature-preserving remeshing, nested shared boundaries, repair of self-intersections, and LOD. Export GLB in metres +Y-up (or document the transform) with Draco/meshopt. |
| `mesh_qc`, `self_intersections` | **Keep** | Good automated QA gates for any new mesher. |
| `validate.py` | **Keep** (generalise) | Mm-based surface metrics and common-FOV logic carry over. Add Dice/NSD against public labelled datasets per organ. Coarse re-segmentation should become optional or offline, since it is about half the runtime. |
| `phantom.py` and test suite | **Keep** and extend | Regression safety. Add multi-organ synthetic and real public test cases. |
| `pipeline.py` orchestration | **Rewrite** | A monolithic 650-line `build()` with forearm logic baked in (:431). Split into stage tasks (ingest → convert → segment (GPU) → mesh → QA → package) with persisted artefacts. |
| `webapp.py` FastAPI + JSON-file jobs + thread queue | **Replace** | Needs auth, multi-tenant isolation, a real DB, a job broker with GPU workers, timeouts and rlimits, object storage, resumable uploads, retention/TTL, and audit logging. Remove the `path` job and the localhost guard. Keep `safe_relpath`, `safe_unzip`'s path logic and the security tests as specs. |
| `ct.html` upload page | **Rewrite** | Hard-coded local or Windows wording (:56, :75, :133). No accounts, no resumable upload, polling only. |
| `viewer.py` + `viewer_template.html` | **Replace** | Single inline HTML, hand-rolled shading, orthographic projection, uniform bone colour, non-OIT transparency, ~1 mm slices with 2 fixed windows, CPU picking, no volume rendering. Move to an established web medical/3D viewer stack with MPR, W/L, VR/MIP, segmentation overlays and mesh rendering, streaming data from storage. Candidates are for the research task (UNVERIFIED here). Keep the payload quantisation idea and the UX features. |
| `explain.py` / `report.py` | **Keep, rewrite content** | The structure is good and safe. The text and bone dictionary are forearm-only (explain.py:22-31, 56, 280). |
| `emg_bridge.py` | **Keep, out of scope** | Project-specific and forearm-only. Not part of the hosted service. |
| Disclaimers in every file | **Keep** | Still needed for a research-only hosted tool. |

Key paths: `src/emgmirror/ct/{webapp,pipeline,segment,mesh,dicom,viewer,validate}.py`,
`src/emgmirror/ct/static/{ct.html,viewer_template.html}`, `docs/CT_TO_3D.md`.
