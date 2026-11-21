# S2 mesh pipeline: software requirements and traceability

Research use, not for diagnosis or surgical planning.

Status: draft design record of 2026-11-22 for `src/emgmirror/ctmesh` (version 0.1.0). It follows
[REGULATORY_PATH.md](../REGULATORY_PATH.md) section 4 (requirements traced to tests, hazards tested in CI,
SOUP register). It is not yet under a signed document-control process; the IEC 62304 software safety class is
not yet assigned (plan for B or C). Dependencies: [SOUP_register.md](SOUP_register.md).

How to verify: `pip install -e ".[dev]" -r requirements-ctmesh.txt && pytest tests/ctmesh` (runs in CI:
`.github/workflows/ctmesh.yml`). Accuracy on public data: `scripts/ctmesh_benchmark.py`, results in
[results/S2_mesh.md](../results/S2_mesh.md).

Test ids are pytest node ids in `tests/ctmesh/`. "BENCH" means the benchmark report, not CI.

## Requirements

| Id | Requirement | Verified by |
|---|---|---|
| S2-REQ-001 | Read the S1 contract: `labels.nii.gz` (integer label map, 3-D, values 0..65535) and `labels.json` version 1. Refuse other versions, duplicate ids and non-integer maps. Label ids in the image but not in the JSON get a generic entry flagged `unlisted`. | `test_geometry_contract.py::test_contract_round_trip_keeps_oblique_geometry`, `::test_unlisted_ids_get_an_entry_and_bad_input_is_refused` |
| S2-REQ-002 | Mesh vertices are in patient LPS millimetres computed from the label map's origin, spacing and direction cosines (ITK convention), including oblique and improper (determinant -1) direction matrices. | `test_geometry_contract.py::test_index_to_lps_matches_simpleitk`, `test_surface_qc.py::test_known_point_lands_in_patient_lps`, `test_hazards.py::test_HAZ04_oblique_direction_cosines` |
| S2-REQ-003 | GLB output is in metres and glTF axes using the documented matrix `lps_to_gltf` (glTF +X = patient left, +Y = superior, +Z = anterior, scale 0.001; proper rotation). The matrix is written to `manifest.json` and to the GLB `asset.extras`. | `test_geometry_contract.py::test_lps_to_gltf_is_a_scaled_proper_rotation`, `::test_anatomical_directions_map_to_gltf_axes`, `test_exports_pipeline.py::test_marker_lands_in_the_right_place_in_every_export` |
| S2-REQ-004 | Each label present becomes one closed, outward-oriented, 2-manifold surface at the native grid resolution (`vtkSurfaceNets3D`, VTK >= 9.5). | `test_surface_qc.py::test_every_phantom_label_is_closed_manifold_and_within_one_voxel` |
| S2-REQ-005 | Labels that touch come from one SurfaceNets run (identical interface vertices before smoothing); after smoothing the two sides stay within 0.9 voxel of each other and their total volume matches the voxels within 5 %. | `test_surface_qc.py::test_touching_labels_meet_without_gap_or_overlap` |
| S2-REQ-006 | Voxels of a label that touch only along an edge or at a corner give a manifold surface (pinched corners are split). | `test_surface_qc.py::test_diagonal_voxels_are_split_into_a_manifold` |
| S2-REQ-007 | When the primary surface fails the QC gate, is thin, or changes volume by more than 10 %, the pipeline also tries discrete flying edges + windowed sinc and, for a volume problem, the exact voxel boundary; it keeps the best passing candidate and reports the method. | `test_surface_qc.py::test_flying_edges_fallback_is_closed`, `::test_every_phantom_label_is_closed_manifold_and_within_one_voxel`, BENCH (method column) |
| S2-REQ-008 | Smoothing removes voxel terraces without shrinking: on a 15 mm sphere (1.5 mm grid) mean normal error < 10 degrees and volume within 2 % of the true sphere; no vertex moves more than 0.45 voxel along any grid axis. | `test_surface_qc.py::test_smoothing_removes_terraces_without_shrinking`, tuning table in BENCH (`scripts/ctmesh_tune.py`) |
| S2-REQ-009 | QC gate for every shipped print mesh: watertight, consistent winding, accepted by manifold3d, no self-intersecting triangle pairs, HD95 against the label voxel boundary <= 1 voxel (geometric mean of the spacing). The web LOD gate: watertight and HD95 <= 1.5 voxel; its self-intersections are reported. | `test_surface_qc.py::test_surface_distance_and_gate`, `::test_ported_self_intersection_count`, `::test_old_and_new_qc_port_agree`, `test_exports_pipeline.py::test_manifest_follows_the_s3_contract` |
| S2-REQ-010 | Volume change (print mesh vs voxel count x voxel volume) is reported for every structure and flagged above 5 %. | `test_exports_pipeline.py::test_manifest_follows_the_s3_contract`, `test_hazards.py::test_HAZ02_scale_and_units` |
| S2-REQ-011 | Each level of detail (print, web) comes from one decimation pass of the native mesh, followed by repair; a LOD that fails its gate is retried with a smaller error cap and finally replaced by the native mesh. | `test_exports_pipeline.py::test_decimation_keeps_the_mesh_closed_and_close`, `::test_repair_drops_folded_fin` |
| S2-REQ-012 | Exports: per structure GLB, binary STL and 3MF (print LOD); `combined_web.glb` with `EXT_meshopt_compression` + `KHR_mesh_quantization`; `combined_print.glb`; `combined_print.3mf`; the label map and JSON copied through. STL and 3MF are in LPS mm. | `test_exports_pipeline.py::test_glb_round_trip_both_encodings`, `::test_manifest_follows_the_s3_contract`, `::test_marker_lands_in_the_right_place_in_every_export`; Khronos glTF-Validator (0 errors) in BENCH |
| S2-REQ-013 | Each GLB node carries the structure's id, name, display name, colour, SNOMED codes, volume and QC in `extras`. | `test_exports_pipeline.py::test_glb_round_trip_both_encodings`, `::test_marker_lands_in_the_right_place_in_every_export` |
| S2-REQ-014 | A DICOM-SEG (highdicom, BINARY) holds every structure with its SNOMED codes; every labelled voxel maps back to the same label in patient space. Without the original series, a geometry-only reference series is synthesised and the manifest says so. | `test_exports_pipeline.py::test_dicom_seg_round_trips_labels_in_patient_space` |
| S2-REQ-015 | `manifest.json` follows the S3 contract (version 1, units "m", axes, `lps_to_gltf`, structures with files, volume_ml, surface_area_cm2, qc fields, combined files). | `test_exports_pipeline.py::test_manifest_follows_the_s3_contract` |
| S2-REQ-016 | Laterality: a left structure stays on the patient's left in every export, whatever the voxel storage order; left/right pairs on the wrong side are flagged (`qc.laterality`, `self_checks.laterality_inconsistent`). | `test_hazards.py::test_HAZ01_left_right_flip`, `::test_HAZ01_swapped_names_are_flagged` |
| S2-REQ-017 | Scale and units are exact: sizes in STL/3MF are mm and in GLB metres; every run reads its GLBs back and checks them against the LPS meshes (`self_checks.glb_units`). | `test_hazards.py::test_HAZ02_scale_and_units` |
| S2-REQ-018 | Anisotropic grids (e.g. 0.6 x 0.6 x 3 mm) keep geometry within 0.6 mm and pass QC. | `test_hazards.py::test_HAZ03_anisotropic_spacing` |
| S2-REQ-019 | Structures cut by the scan edge are capped (closed) and flagged `cut_by_scan_edge`. | `test_hazards.py::test_HAZ05_scan_edge_cut_is_flagged`, `test_surface_qc.py::test_scan_edge_is_capped_and_flagged`, `test_exports_pipeline.py::test_forearm_phantom_end_to_end` |
| S2-REQ-020 | Determinism: the same input with the same software versions and parameters gives a byte-identical `manifest.json` and identical output files, with any number of worker processes. Run-specific data goes only to `run_log.json`. | `test_hazards.py::test_HAZ06_deterministic_output` |
| S2-REQ-021 | Provenance: `manifest.json` records software and dependency versions, input hashes, the parameters and their hash, the source models, per-structure mesh hashes and per-file hashes. | `test_hazards.py::test_HAZ06_deterministic_output` |
| S2-REQ-022 | Every output file carries the research-use statement (STL header, GLB `asset`, 3MF metadata, DICOM-SEG content description, manifest). | `test_exports_pipeline.py::test_marker_lands_in_the_right_place_in_every_export`, `::test_glb_round_trip_both_encodings` |
| S2-REQ-023 | Bone accuracy against the label map is reported per structure against the proposed acceptance criteria (mean surface distance <= 1 mm, HD95 <= 2 mm, laterality consistent). | BENCH (`results/S2_mesh.md`, `results/S2_mesh_structures.csv`) |
| S2-REQ-024 | Public fixtures: TotalSegmentator ground truth converts into the S1 contract with names, SNOMED codes and colours. | `test_geometry_contract.py::test_totalseg_to_contract`, `::test_totalseg_table_has_names_codes_and_colours`, `::test_snomed_triplet_accepts_dcmqi_keys` |

## Hazards covered (ISO 14971 seeds from REGULATORY_PATH.md section 4)

| Hazard | Risk control in S2 | Requirement | Test |
|---|---|---|---|
| Left/right flip | Geometry from the direction matrix only; laterality self-check of left/right pairs | S2-REQ-002, 016 | HAZ01 |
| Wrong scale / unit error (mm vs m) | Single documented transform; GLB read-back check every run | S2-REQ-003, 017 | HAZ02 |
| Anisotropic or oblique grids misplace anatomy | Index-to-LPS matrix used for every vertex; tested on oblique, improper and anisotropic grids | S2-REQ-002, 018 | HAZ03, HAZ04 |
| Organ truncated by the scan edge shown as complete | Closed cap plus `cut_by_scan_edge` flag | S2-REQ-019 | HAZ05 |
| Silent change of output between runs or versions | Deterministic output, provenance and hashes | S2-REQ-020, 021 | HAZ06 |
| Mesh misrepresents the label map (holes, self-intersections, shrinkage) | QC gate, fallbacks, volume reporting | S2-REQ-004..011 | `test_surface_qc.py` |

Not covered by S2 (owned elsewhere): wrong patient or series, stale or silently changed segmentation model
(S1 provenance; S2 copies `source_model` into the manifest), segmentation errors against the true anatomy
(needs independent clinical validation).

## Known limitations

- Accuracy is measured against the label map, not against the patient. The benchmark uses public ground-truth
  labels on a 1.5 mm grid.
- SurfaceNets can leave a non-manifold edge where two diagonal voxels connect around both ends of the edge, and
  smoothing can make pinched sheets touch; such labels go to the fallback (counted in the benchmark).
- Structures of one or two voxels are exported as their exact voxel boundary (cubes), because any smoothing
  would remove most of their volume.
- The DICOM-SEG references a synthesised geometry-only series unless the original CT series is passed in.
