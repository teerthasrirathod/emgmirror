# S1 software requirements: segmentation engine

Research use, not for diagnosis or surgical planning, until licensed.

| | |
|---|---|
| Item | `emgmirror.ctseg` (segmentation engine, milestone S1) |
| Status | Draft for design control. Not yet reviewed or approved under a quality system. |
| Intended use | Draft v1 in [../REGULATORY_PATH.md](../REGULATORY_PATH.md) section 2: adult CT → 3D models of bony anatomy (pelvis/acetabulum, spine, shoulder girdle, proximal femur, humerus) for pre-operative orthopaedic and trauma planning, after clinician review and approval. |
| Software safety class | To be decided in the risk file (plan for IEC 62304 class B or C). |
| Related | [SOUP_register.md](SOUP_register.md), [../S1_segmentation.md](../S1_segmentation.md), [../results/S1_benchmark.md](../results/S1_benchmark.md) |

Each requirement is verified by the listed tests in `tests/ctseg/`. `tests/ctseg/test_ctseg_traceability.py`
fails if a requirement has no test, or if a listed test does not exist. "Device" means `--build device`;
"both" means research and device builds.

## 1. Models and licences

| ID | Requirement | Build | Verified by |
|---|---|---|---|
| SRS-001 | The device build shall run only TotalSegmentator `total`. Any other model (`body`, subtasks, MOOSE, CADS, NVIDIA) shall fail the licence check before any model runs. | device | `test_ctseg_device.py::test_device_build_allows_total_only`, `test_ctseg_device.py::test_device_config_defaults_and_explicit_research_models_fail`, `test_ctseg_device.py::test_device_run_outputs_only_intended_use_structures`, `test_ctseg_device.py::test_cli_device_licence_check` |
| SRS-002 | The research build shall accept only allowlisted commercial-safe models. Licence-key TotalSegmentator tasks, CADS `reference`/`research`, nnInteractive, MedSAM2, VoxTell, SuPreM and NV-Segment-CTMR shall be refused, with the reason. | research | `test_ctseg_allowlist.py::test_open_totalsegmentator_tasks_pass`, `test_ctseg_allowlist.py::test_everything_else_is_refused`, `test_ctseg_allowlist.py::test_reason_is_given_for_known_non_commercial_models`, `test_ctseg_allowlist.py::test_cads_needs_the_open_licence`, `test_ctseg_allowlist.py::test_config_check_collects_all_problems` |
| SRS-003 | A configured TotalSegmentator licence key shall be an error, and any key found in TotalSegmentator's config shall be removed. | both | `test_ctseg_allowlist.py::test_a_configured_licence_key_fails`, `test_ctseg_engine.py::test_usage_stats_are_switched_off_in_totalsegmentator_config` |
| SRS-004 | A weights folder holding an nnU-Net dataset outside the build's allowlist shall fail the check. In the device build only the `total` datasets are allowed. | both | `test_ctseg_allowlist.py::test_weights_dir_with_licence_key_weights_fails`, `test_ctseg_allowlist.py::test_missing_weights_dir_is_fine`, `test_ctseg_device.py::test_device_weights_folder_may_hold_only_total` |
| SRS-005 | The allowlist shall agree with TotalSegmentator's own licence table and task ids. | both | `test_ctseg_allowlist.py::test_allowlist_matches_installed_totalsegmentator` |
| SRS-006 | TotalSegmentator usage statistics shall be switched off before every run, and provenance shall record that. | both | `test_ctseg_engine.py::test_usage_stats_are_switched_off_in_totalsegmentator_config`, `test_ctseg_engine.py::test_provenance_records_models_licences_and_privacy` |

## 2. Model registry and change detection

| ID | Requirement | Build | Verified by |
|---|---|---|---|
| SRS-010 | The device build shall verify every weight file of the `total` datasets it uses against SHA-256 values pinned in `resources/device_weights.json`, together with the TotalSegmentator version, **before** running. A changed, missing or extra file, an unpinned dataset or another version shall stop the job without output. | device | `test_ctseg_device.py::test_verify_passes_and_fingerprint_is_stable`, `test_ctseg_device.py::test_verify_detects_model_changes`, `test_ctseg_device.py::test_device_run_refuses_unpinned_weights_before_running` |
| SRS-011 | The shipped manifest shall pin all `total` datasets (1.5 mm: 291–295; 3 mm: 297), each with a checkpoint and a consistent tree hash. | device | `test_ctseg_device.py::test_shipped_manifest_pins_every_total_dataset` |
| SRS-012 | Every result shall carry a model fingerprint in `provenance.json`. The fingerprint shall change whenever the weights change. | both | `test_ctseg_hazards.py::test_model_change_changes_the_provenance_fingerprint`, `test_ctseg_device.py::test_device_run_outputs_only_intended_use_structures` |
| SRS-013 | Provenance shall list each model run with licence, weight ids, status and runtime, plus software versions, settings and output hashes. It shall contain no input file names. | both | `test_ctseg_engine.py::test_provenance_records_models_licences_and_privacy` |

## 3. Input

| ID | Requirement | Build | Verified by |
|---|---|---|---|
| SRS-020 | Scans above the refuse cap shall be refused from the header alone. Scans above the working cap shall be resampled slab by slab, never loaded at full size, and the labels shall come with the matching `image.nii.gz`. | both | `test_ctseg_inputs.py::test_refuse_from_header_alone`, `test_ctseg_inputs.py::test_oversized_nifti_is_resampled_slabwise_before_full_load`, `test_ctseg_engine.py::test_resampled_input_writes_the_matching_image` |
| SRS-021 | Voxel spacing outside 0.2–10 mm, or a field of view longer than 2.5 m, shall be refused as a wrong scale. An in-plane field of view under 40 mm shall be flagged `implausible_scale`, and the device build shall refuse it. | both / device | `test_ctseg_hazards.py::test_grossly_wrong_spacing_is_refused`, `test_ctseg_hazards.py::test_moderately_wrong_scale_is_flagged_and_refused_by_the_device_build` |
| SRS-022 | Slices thicker than 5 mm shall be refused. Thicker than 3 mm shall be flagged. | both | `test_ctseg_inputs.py::test_slice_thickness_warns_then_refuses`, `test_ctseg_engine.py::test_thick_slices_refused` |
| SRS-023 | From a DICOM folder, the finest volumetric CT series shall be chosen with the research pipeline's geometry rules. The chosen series, the reason and the alternatives shall be recorded (UID only as a hash). A folder with no volumetric series shall be refused. | both | `test_ctseg_hazards.py::test_finest_series_is_chosen_and_recorded`, `test_ctseg_hazards.py::test_series_record_reaches_labels_json`, `test_ctseg_hazards.py::test_localizer_only_folder_is_refused`, `test_ctseg_inputs.py::test_dicom_folder_matches_the_nifti_geometry` |
| SRS-024 | CT and MR shall be told apart (DICOM tag, else intensities). MR shall route to `total_mr` in the research build and be refused in the device build. A possibly unrescaled CT shall be flagged, and the device build shall refuse it. | both / device | `test_ctseg_inputs.py::test_ct_is_recognised_and_clean`, `test_ctseg_inputs.py::test_mr_like_intensities_route_to_mr`, `test_ctseg_inputs.py::test_unrescaled_ct_is_warned_about`, `test_ctseg_engine.py::test_mr_is_routed_to_total_mr`, `test_ctseg_device.py::test_device_run_refuses_mr` |
| SRS-025 | Metal shall be detected and structures within 10 mm flagged. | both | `test_ctseg_inputs.py::test_metal_is_flagged`, `test_ctseg_qc.py::test_near_metal_and_thick_slices` |
| SRS-026 | Unreadable files and sheared geometry shall be refused with a plain message. Headers off-orthonormal by ≤ 1e-2 shall be repaired and flagged. | both | `test_ctseg_inputs.py::test_not_an_image_is_refused`, `test_ctseg_inputs.py::test_sheared_header_is_refused`, `test_ctseg_inputs.py::test_slightly_non_orthonormal_header_is_repaired`, `test_ctseg_cli_benchmark.py::test_run_refuses_unreadable_input` |

## 4. Output

| ID | Requirement | Build | Verified by |
|---|---|---|---|
| SRS-030 | Outputs shall follow the shared contract: uint16 `labels.nii.gz` on the segmented grid; `labels.json` version 1 with id, name, display, rgb, snomed, source_model and flags per present label; `provenance.json`. No intermediate files shall remain. | both | `test_ctseg_engine.py::test_outputs_follow_the_shared_contract`, `test_ctseg_engine.py::test_no_intermediate_files_left` |
| SRS-031 | The device build shall output only the intended-use structures (36 `total` labels, mapped in `device.INTENDED_USE`). Every result shall be marked `not_approved` until clinician sign-off. | device | `test_ctseg_device.py::test_intended_use_table_maps_to_total_labels`, `test_ctseg_device.py::test_device_run_outputs_only_intended_use_structures` |
| SRS-032 | Label ids shall be stable: `total` ids 1–117, unique ids, snake_case names, valid colours, SNOMED codes with laterality modifiers. | both | `test_ctseg_ontology.py::test_total_keeps_its_own_ids`, `test_ctseg_ontology.py::test_every_model_class_is_in_the_table_and_ids_are_unique`, `test_ctseg_ontology.py::test_names_are_snake_case_and_colours_valid`, `test_ctseg_ontology.py::test_snomed_codes_present_with_laterality`, `test_ctseg_ontology.py::test_moose_names_follow_totalsegmentator_spelling` |
| SRS-033 | A failed optional model shall be reported, and all other structures delivered. A failed base model shall fail the job. | research | `test_ctseg_engine.py::test_failed_optional_model_gives_partial_result`, `test_ctseg_engine.py::test_failed_base_model_fails_the_job` |

## 5. Safety checks (hazards)

| ID | Requirement | Build | Verified by |
|---|---|---|---|
| SRS-040 | **Left/right:** every `*_left`/`*_right` structure shall be checked against its partner and the midline. A mirrored image or a negated x axis shall be flagged `laterality_suspect`. The device build shall list failures and warn. | both | `test_ctseg_hazards.py::test_mirrored_image_fails_the_laterality_check`, `test_ctseg_hazards.py::test_swapped_pair_is_caught_in_either_build`, `test_ctseg_hazards.py::test_header_with_negated_x_axis_is_caught` |
| SRS-041 | **Cut by scan edge:** structures touching the image border shall be flagged `cut_by_scan_edge`, with the faces touched. The device build shall list them for the reviewer. | both | `test_ctseg_hazards.py::test_cut_structure_is_listed_for_the_reviewer`, `test_ctseg_qc.py::test_edge_volume_and_hu_flags` |
| SRS-042 | **Implausible structure:** complete structures outside wide volume bands, structures with mean HU outside their tissue band, very small structures and fragmented solid structures shall be flagged. | both | `test_ctseg_qc.py::test_edge_volume_and_hu_flags`, `test_ctseg_qc.py::test_fragmented_and_very_small`, `test_ctseg_qc.py::test_hu_bands_by_tissue`, `test_ctseg_qc.py::test_mr_skips_hu_checks`, `test_ctseg_hazards.py::test_wrong_scale_shows_as_implausible_volume` |
| SRS-043 | **Wrong scale, wrong series, model change:** see SRS-021, SRS-023, SRS-010 and SRS-012. | both | `test_ctseg_hazards.py::test_grossly_wrong_spacing_is_refused`, `test_ctseg_hazards.py::test_finest_series_is_chosen_and_recorded`, `test_ctseg_hazards.py::test_model_change_changes_the_provenance_fingerprint` |

## 6. Fusion and model selection (research build)

| ID | Requirement | Build | Verified by |
|---|---|---|---|
| SRS-050 | Labels from several models shall be fused by rank: higher ranks overwrite, ties keep plan order, refinements stay inside their parent and carry `part_of:`. | research | `test_ctseg_fusion.py::test_primary_model_beats_container_whatever_the_order`, `test_ctseg_fusion.py::test_refinement_only_inside_its_parent_and_flagged`, `test_ctseg_fusion.py::test_lower_rank_extra_model_only_fills_gaps`, `test_ctseg_fusion.py::test_equal_rank_first_in_plan_wins`, `test_ctseg_fusion.py::test_refined_vertebrae_may_grow_into_background_but_not_into_organs`, `test_ctseg_fusion.py::test_head_container_does_not_cover_organs`, `test_ctseg_fusion.py::test_grid_mismatch_and_unknown_source_are_errors` |
| SRS-051 | Only the subtasks and MOOSE models for regions in the field of view shall run. | research | `test_ctseg_regions.py::test_chest_scan_runs_chest_subtasks_only`, `test_ctseg_regions.py::test_liver_segments_need_a_liver`, `test_ctseg_regions.py::test_limbs_and_lumbar_trigger_moose`, `test_ctseg_regions.py::test_tiny_blobs_do_not_count_as_a_region`, `test_ctseg_regions.py::test_minimal_profile_mr_and_explicit_tasks`, `test_ctseg_engine.py::test_pipeline_order_and_region_selection`, `test_ctseg_engine.py::test_minimal_profile` |

## 7. Accuracy (validation, not yet met)

| ID | Requirement | Build | Verified by |
|---|---|---|---|
| SRS-060 | Accuracy shall be measured per intended-use structure on public labelled CT: Dice, HD95 and mean surface distance in mm, and laterality. The benchmark shall report the intended-use structures first. Acceptance (proposal, REGULATORY_PATH.md §4): bone MSD ≤ 1 mm, HD95 ≤ 2–3 mm, laterality 100 %, on 150–300 independent patients from ≥ 3 sites. **Not yet verified.** S1 has a 16-subject 3 mm CPU smoke benchmark only. | device | `test_ctseg_cli_benchmark.py::test_score_subject`, `test_ctseg_cli_benchmark.py::test_subject_selection_is_test_split_and_spread` |

## Open items

- Vertebral level consistency (wrong-level hazard): the S1 benchmark found a one-level shift of T6-T10 in a
  5 mm cardiac CT (s0684). Add a level check (count and order of vertebrae against ribs and sacrum) and a
  requirement with tests before the spine is claimed.
- Risk file (ISO 14971) linking each hazard to these requirements; software safety classification.
- Clinician review and approval workflow (the `not_approved` state is set here; the sign-off itself belongs to the backend and viewer).
- Verification on the GPU worker at 1.5 mm and on independent multi-site data, including Indian data.
- Written terms from the MOOSE authors (Zenta), NVIDIA and the CADS share-alike review before any of them can move from research to device.
