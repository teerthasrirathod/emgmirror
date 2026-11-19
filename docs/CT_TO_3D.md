# CT to patient-specific 3D models

**Research use, not for diagnosis or surgical planning.** "Medical grade" here means
DICOM-correct geometry in patient millimetres with a reported error. It is not a certified device.

`emgmirror ct build` reads the clinical CT in `01_clinical_CT/` and writes `results/ct/`
(meshes, `labels.nii.gz`, `volume.nii.gz`, `viewer.html`, `report.md`). `emgmirror ct serve`
starts a local page (127.0.0.1 only) where anyone can load their own CT folder or zip and get the
same outputs with a plain-language explanation. Nothing is sent off the machine by either command.

## 1. The scan

Measured by `scripts/ct_inventory.py` and again by `emgmirror.ct.dicom.scan_series` on every build.

| Series | Description | Plane | Slices | Matrix | Pixel mm | Slice spacing mm (from positions) | SliceThickness tag | Kernel | Use |
|---|---|---|---|---|---|---|---|---|---|
| 4 | Bone 0.5 | axial | 1007 | 512x512 | 0.468 | 0.300 | 0.5 | FC30 | **primary (bone)** |
| 7 | Sft Tissue 0.5 | axial | 1007 | 512x512 | 0.468 | 0.300 | 0.5 | FC03 | soft-tissue HU on the same grid |
| 5 | Bone 2.000 | coronal | 37 | 648x648 | 0.468 | 2.000 | 2 | FC30 | independent coarse check (stacks split at a 50 mm gap) |
| 6 | Bone 2.000 | sagittal | 46 | 648x648 | 0.468 | 2.000 | 2 | FC30 | independent coarse check (stacks split at 76 mm gaps) |
| 8 to 11 | scouts / screenshots / single reformats | mixed | 12 to 49 | | | not a volume | | FC03 | rejected: missing or duplicate positions |

Decisions:

- **Primary = series 4.** Finest voxels (0.468 x 0.468 x 0.300 mm), full coverage (301.8 mm), sharp
  kernel, which gives the best cortical edge. The brief said "0.5 mm"; the slice positions are
  0.300 mm apart. 0.5 mm is the nominal slice thickness of an overlapping reconstruction. Geometry
  uses the positions.
- **Soft tissue HU from series 7** (same grid, smooth kernel, lower noise). Bone from series 4.
- **The "2 mm series" are coronal and sagittal reformats**, not a second axial reconstruction, and
  each covers only part of the arm in separate stacks. They are still reconstructed independently of
  series 4 by the scanner, so each stack is segmented on its own and compared with the 0.3 mm
  result inside the common field of view.

## 2. What we took from where

Survey done 2026-11-10. Full notes with URLs are kept outside the repo; the points that shaped the design:

| Source | Licence | What we took | What we did not take |
|---|---|---|---|
| 3D Slicer Segment Editor | BSD-style | The workflow: threshold, keep islands, fill, smooth, export closed surface. Smoothing as a tunable factor. | No code. SurfaceWrapSolidify is "all rights reserved": not copied; holes are filled with SciPy. |
| TotalSegmentator | Apache-2.0 code | Nothing at run time. | `appendicular_bones` and `tissue_types` weights need a licence number and are non-commercial; it sends usage statistics unless disabled and downloads weights. No forearm task in the open set. Needs PyTorch and more RAM than this laptop has. |
| MONAI / nnU-Net | Apache-2.0 | Nothing: no public forearm or wrist CT weights were found. | |
| MOOSE, MuscleMap | Apache-2.0 / MIT code | Nothing: 16 GB RAM recommended, or forearm not covered. | |
| SimpleITK | Apache-2.0 | Image container, NIfTI I/O, resampling, connected components, distance maps. | |
| VTK docs (windowed sinc), Taubin 1995 | n/a | Non-shrinking lambda/mu smoothing instead of Laplacian; report volume change. | VTK itself is not a dependency. |
| Body-composition literature | n/a | HU windows: adipose -190 to -30, muscle -29 to +150 (trunk convention at 120 kV; stated as a convention, checked against this scan's histogram). | |
| Metal in CT literature | n/a | 12-bit data clip at 3071 HU; titanium about 8000 HU and steel above 13000 HU on an extended scale. The build detects whether a scan is clipped at 3071 HU or extended scale and reports which. | A fixed ">2500 to 3000 HU = metal" rule could not be sourced; the metal threshold is set from this scan's histogram and logged. |
| ORMIR-XCT, Buie dual threshold | GPL-3.0 / paper | The idea of separating cortical from trabecular bone by density inside a periosteal mask. | No code. HR-pQCT thresholds do not transfer to 0.3 to 0.5 mm clinical CT. |
| Reported CT bone-surface accuracy | papers | Benchmark for our numbers: about 0.2 to 0.3 mm mean surface error for sub-millimetre CT, threshold choice alone moves the surface by about 0.1 mm. | |

**Why no neural network:** the only pretrained model that covers radius and ulna is licence-gated and
non-commercial, phones home by default, and does not fit 7.6 GB RAM without a GPU. A classical
pipeline is offline by construction, every parameter is inspectable, and it does not fail silently
on metal. Cost: no automatic naming of single muscles and no learned separation of touching carpal
bones. Both limits are stated in the report.

No skills or agents were installed: the skill search returned nothing for DICOM, medical imaging,
mesh or 3D viewing.

Python packages added (all read before use, all offline, permissive): pydicom (MIT), SimpleITK
(Apache-2.0), nibabel (MIT), scikit-image (BSD-3), trimesh (MIT). The viewer is plain WebGL with no
third-party JavaScript.

## 3. Task graph

```
inventory ─┐
prior art ─┴─> plan ─┬─> dicom loader (geometry, HU, NIfTI) ──> segmentation (bone, hardware, soft tissue)
                     ├─> mesh (marching cubes, Taubin, export, QC) ─┐
                     ├─> viewer (offline HTML) ─────────────────────┤
                     └─> validation metrics ───────────────────────┴─> pipeline + CLI + anatomy bridge
                                                                        + local upload page ──> independent review
```

Owners: inventory and scaffolding on the small model; loader, mesh and viewer on the mid model;
segmentation and validation design on the large model; planning, integration and audit by the
orchestrator; a separate reviewer sees only outputs.

## 4. Performance plan

Machine: 2 cores, 7.6 GB RAM, no GPU.

- The volume is loaded once as int16 (528 MB), cached as NIfTI, and cropped to the body before any
  float work. Distance maps and smoothing run on float32 crops.
- Heavy steps stay in compiled code (NumPy, SciPy ndimage, SimpleITK, scikit-image).
- Independent structures are meshed in separate processes where memory allows (2 workers).
- Timings per step are written to `report.md`. A Rust kernel is only written if one step dominates
  and the library version is the bottleneck; the decision and its numbers are in section 6.

## 5. Decisions log

Each entry: what was decided, why it is the most defensible option, and how it was checked. Numbers for
the current build are in section 6 and in `results/ct/report.md`.

**Segmentation is classical and every threshold is measured on the scan** (body: half-way between the
air and soft-tissue peaks; bone: hysteresis from the soft/cortical half-maximum down to lean peak plus
6 noise SD; metal: per-object half-maximum; fat/lean: half-way between the in-body peaks). Each value
and the reason for it is written to `report.md`, so a reader can rerun it with any value fixed.

**Morphology on bounding boxes (2026-11-17).** Dilation, erosion and limited-range distance maps are
computed on the mask's bounding box grown by the radius. This is exact, not an approximation: no
voxel outside that box can be within the radius. It gave identical results to whole-array maps on
300 random cases and cut the metal and artefact steps from minutes to seconds.

**Body mask reaches the array end (2026-11-17).** The coarse body grid dropped the last incomplete
block of each axis, so the last 2 slices at the hand end were outside the body. Each fine voxel now
takes the value of the coarse block it lies in, the last block included.

**Bone envelopes: one bone at a time, largest first (2026-11-17).** An envelope may take any voxel
that is not another bone's thresholded bone or metal, and whatever it encloses completely becomes
part of it. Without this, a watershed basin seeded inside one bone's marrow kept that marrow out.

**Cavities closed only by slice-wise filling (2026-11-17, revised 2026-11-19).** A canal open at a
fracture or at one end is not a 3D hole but is a closed ring across the bone. Such a cavity is bone
interior when at most 15 % of its surface is open. An open canal has a small mouth (a few %), while a
fossa that looks ringed in one slice direction has a wide mouth (a quarter or more).
On 2026-11-19 the full build showed a 5 x 5 x 9 mm box of "muscle" inside one bone's marrow cavity.
The cause was the slice filling itself: one union of per-slice fills along the three axes leaves a
voxel empty only where its slices are open along all three axes. That set is a product of open rows,
columns and slices, which is a rectangular prism. The fill now repeats until nothing changes, which
gives the smallest set closed under slice filling. The prism's walls had also counted as "open"
surface: that cavity went from 0.144 open share (just under the 0.15 cut) to 0.027.
Test: `test_slice_fill_leaves_no_rectangular_prism`. The build now also lists, per bone, the tissue a bone
encloses in any slice and flags regions that fill most of their bounding box
(`validate.enclosed_tissue`, test `test_enclosed_tissue_finds_a_box_inside_the_bone_and_ignores_metal`).

**What the closing adds near the surface is given back only where a ball reaches it (2026-11-19).**
The closing (1.5 mm) also smears into concave bone surfaces. Earlier, every closing-added voxel
within 1.5 mm of the surface was given back. That also removed the seam under a thin cortex that
thresholding left dotted, and trabecular pores just under a porous cortex. Both then leaked out
through 1-voxel gaps and were labelled muscle: about 0.4 cm3 at one bone end, and speckle through
another. Now a voxel is given back only where a 0.75 mm ball reaches it from
outside the bone through non-bone space. A concavity passes, and a gap under 1.5 mm does not.
Parameter `give_back_ball_mm`. Test: `test_envelope_keeps_seam_under_dotted_cortex_but_returns_concavity`
(old rule keeps 0 % of the seam, new rule 100 %; both return the notch).

**Plain-language summary counts each bone once (2026-11-19).** The pipeline emits the whole bone and
its inner part (`ulna`, `ulna_medullary`). The summary added both, which reported a bone at about
1.4 times its volume. The tests had used a cortical-plus-medullary layout that the pipeline
never emits. Test: `test_whole_bone_plus_inner_part_not_double_counted`.

**No bone within 1 mm of air (2026-11-19).** The sharp FC30 kernel overshoots at the skin/air edge to
200-360 HU. The 0.3 mm primary's bone threshold (374 HU) keeps it out. The 2 mm sagittal reformat
measures a lower threshold of its own and called a strip along the skin "bone". That strip was the
cause of a 22 mm 95th-percentile distance. Skin and dermis are thicker than 1 mm, so real bone does not lie
within 1 mm of air, and the rule removes nothing real.
Depth is measured from air only: padding and a cut field of view do not count. Parameter
`skin_bone_gap_mm`. Test: `test_skin_edge_overshoot_is_not_bone`.

**Coarse bones are matched to primary bones by overlap, not by name (2026-11-19).** A 2 mm run can
split a bone in two (a named bone plus an unnamed "bone_4"). Comparing by name then measures part of the bone against all
of it: 101 mm at the 95th percentile on the reference scan, which is not a real surface error. Matched
by overlap, the figure was 8.9 mm. A run that names no bone at all had no per-bone figures before. Each coarse piece goes to the primary bone that holds at least half of its
voxels. When a bone is split or renamed, the report says so next to the numbers. Test:
`test_coarse_bones_are_matched_by_overlap_not_by_name`.

**The mesh check counts the cap where the scan cuts a structure (2026-11-19).** A mesh is closed at
the array edge (zero border before marching cubes). The label map's surface used to be sampled only
between voxels inside the array, so the centre of such a cap was measured to the nearest side wall.
That gave up to 7.5 mm for a bone cut by the scan edge and 33 mm for the skin. These were not mesh
errors. The space beyond the array now counts as background in this check only. The coarse
comparisons keep the old rule: there both masks are cut by the same field of view, and the cut faces
would hide differences. Test: `test_mesh_vs_mask_counts_the_cap_where_the_scan_cuts_a_solid`.

**The surface sensitivity is measured on the decision that places the delivered surface
(2026-11-19).** The bone threshold sweep moves the global hysteresis threshold. The delivered outer
surface, however, is cut at the local half-maximum between lean tissue and the local bone maximum, a
median of about 1000 HU on the reference scan against a threshold near 370 HU. The sweep therefore
describes an intermediate mask, and the report now labels it so. The build adds an edge sensitivity:
the outer surface shift when the edge moves to 40 % or 60 % of the way from lean tissue to the local
maximum. It is counted on the same envelope, so it costs one comparison, not a new segmentation.

**Every output file carries the disclaimer in its own header (2026-11-19).** That is the STL
80-byte header, a comment line in OBJ and PLY, `asset.extras` and the scene extras in GLB, the NIfTI
`descrip` field, a key in each JSON file, and a line in each Markdown file. A file copied out of
`results/`, for example to a print service, still says "research use, not for diagnosis or surgical
planning".

**Findings about the patient stay out of tracked files (2026-11-19).** This document is tracked in
git. Observations about one person's anatomy go only to `results/` (git-ignored): `report.md`, the
notes on the reference build in `results/ct/build_notes.md` and the EMG-on-CT results in
`results/ct/emg/`. That covers fractures, implants, which arm, bone sizes, tissue depths and where a
label sits in the body. What stays here are sizes of method errors that explain a decision, such as
a distance that was wrong because of a bug or a threshold level. They say nothing about the anatomy.

**Validation is self-consistency, and is labelled that way.** No manual ground truth or second scan
exists. The checks are: independent segmentation of the scanner's 2 mm coronal and sagittal
reformats, compared inside the common field of view; a threshold sweep of plus/minus 150 HU; HU of
air, fat, muscle and cortex against plausible bands; mesh against label map; and an analytic phantom
in the test suite. The report says in each table what the number does not show.

## 6. Results, limits and the Rust decision

The numbers for a build are in its `report.md`: the headline table, per-bone comparisons, the edge
sensitivity, the tissue enclosed by bone, mesh checks and failure modes. The notes on the reference
build are in `results/ct/build_notes.md`. Both are git-ignored because they describe one person's
anatomy. The test suite checks the method on synthetic data only.

**What the validation can and cannot show:**
- The checks compare the method with itself: independent 2 mm reconstructions of the same acquisition,
  an edge-level sweep, HU bands and mesh against label map. Shared errors of one acquisition (same
  patient position, same beam hardening, same rule) do not appear in them.
- A difference between the 0.3 mm and the 2 mm result does not say which of the two is wrong. The
  report gives the direction (coarse minus primary) and the asymmetric surface distances, not a verdict.
- The all-bone figures are dominated by the large bones. The report lists the weakest bone and the
  largest per-bone volume difference next to them.
- A manual segmentation of a few slices, or a second scan, would turn self-consistency into accuracy.

**Known limits of the method:**
- Bone meshes are watertight but can have a few self-intersecting triangle pairs. The volume is
  unaffected. Slicers for 3D printing usually tolerate a few, but no print was tested.
  Finite-element meshing needs a repair pass.
- Muscle and fat meshes are display surfaces, not closed solids. Muscle is one lean-tissue label
  (skin included); single muscles are not named.
- Bones can stay in several connected pieces near fractures and implants. The report gives the count
  and the share of the largest piece.
- Touching small bones (carpals, metacarpals) are one group, not separate bones.

**Rust decision: not written.** On the 2-core laptop a full build takes 43 to 66 min (51 min alone on the
machine). Segmentation takes about a third of that, validation about half, and meshing about a tenth. Validation is two more full
segmentations (the coarse stacks) plus the threshold sweep, using the same library calls. That time
sits in SciPy and SimpleITK distance maps and morphology, which are already compiled C, so a Rust
kernel would repeat the same arithmetic. The later builds add the fixpoint slice fill and the ball
give-back, three times per build. The cost is the number of passes, not the language. If speed
matters, cache the coarse segmentations between builds instead of writing a kernel.
