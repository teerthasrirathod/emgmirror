# S1: segmentation engine (`emgmirror.ctseg`)

Research use, not for diagnosis or surgical planning.

Milestone S1 of [PLAN.md](PLAN.md): one command from a CT to a canonical label map with per-structure QC,
using only commercial-safe weights. Code in `src/emgmirror/ctseg/`, tests in `tests/ctseg/`, extra
dependencies in `requirements-ctseg.txt`. Benchmark: [results/S1_benchmark.md](results/S1_benchmark.md).

## Use

```bash
pip install -e ".[dev]" -r requirements-ctseg.txt
```

```bash
python -m emgmirror.ctseg run --input ct.nii.gz --out result/ --device gpu
```

```bash
python -m emgmirror.ctseg run --dicom path/to/dicom_folder --out result/ --fast --device cpu
```

```bash
python -m emgmirror.ctseg check-licences --weights-dir "$TOTALSEG_WEIGHTS_PATH"
```

From Python: `emgmirror.ctseg.engine.segment(input, out_dir, SegConfig(...))`.

Options: `--profile minimal|standard|skeletal` (default `standard`), `--fast` (TotalSegmentator 3 mm models where they
exist), `--modality auto|ct|mr`, `--tasks a,b` (explicit subtasks instead of region selection), `--no-moose`,
`--config file.json` (any `SegConfig` field).

`skeletal` is a research profile: it keeps the base result, uses full-resolution TotalSegmentator models, and
always schedules the MOOSE peripheral-bone task for CT even when the base model emits no limb marker. Scheduling
does not guarantee execution: `moosez` and its model weights must be installed. The current `deploy/ctapp`
Dockerfile excludes `moosez`, so hosted runs report that task as unavailable and peripheral coverage remains
incomplete. The pipeline does not claim that unsupported or out-of-field anatomy was detected. The 3 mm
`--fast` option is rejected for this profile.

## Builds: research and device

Since 2026-11-22 the owner has chosen the medical-device route ([REGULATORY_PATH.md](REGULATORY_PATH.md)). The
engine therefore has two builds:

| | `--build research` (default) | `--build device` |
|---|---|---|
| Models | `total`/`total_mr`, `body`, region subtasks, MOOSE (allowlist) | TotalSegmentator `total` only; anything else fails the licence check |
| Weights | not pinned (fingerprint recorded) | every weight file verified against `resources/device_weights.json` before running; the weights folder may hold only `total` datasets |
| Input | CT or MR | CT only; scale or HU-calibration doubts are refused |
| Output | every structure found | only the 36 intended-use bone labels (`device.INTENDED_USE`); `qc.device` lists cut and laterality-failed structures; status `not_approved` until clinician sign-off |

Intended-use structures (first intended use, REGULATORY_PATH.md section 2):

| Group | `total` labels |
|---|---|
| Pelvis / acetabulum | `hip_left`, `hip_right`, `sacrum` |
| Spine | `vertebrae_C1`–`C7`, `vertebrae_T1`–`T12`, `vertebrae_L1`–`L5`, `vertebrae_S1` |
| Shoulder girdle | `scapula_left`, `scapula_right`, `clavicula_left`, `clavicula_right` |
| Proximal femur | `femur_left`, `femur_right` (TotalSegmentator labels the whole femur in view) |
| Humerus | `humerus_left`, `humerus_right` |

`total` has no separate acetabulum label: the acetabulum is part of `hip_*`. Design records:
[device/S1_software_requirements.md](device/S1_software_requirements.md) (requirements traced to tests) and
[device/SOUP_register_ctseg.md](device/SOUP_register_ctseg.md) (every package and weight with version, licence and hash).
Re-pin the weights only as a controlled change: run `scripts/ctseg_pin_device_weights.py`, then regenerate the
register with `scripts/ctseg_soup_register.py`.

```bash
TOTALSEG_WEIGHTS_PATH=/opt/weights/device python -m emgmirror.ctseg run --build device --input ct.nii.gz --out result/
```

## Outputs (the shared contract S2 consumes)

| File | Content |
|---|---|
| `labels.nii.gz` | uint16 label map, same grid and affine as the segmented image. Disclaimer in the header. |
| `labels.json` | `{"version": 1, "labels": [...], "qc": {...}}`. Each label: `id`, `name` (TotalSegmentator snake_case), `display`, `rgb`, `snomed` (`category`, `type`, and `modifier` for laterality, DICOM code triplets `CodingSchemeDesignator`/`CodeValue`/`CodeMeaning`; `null` only for `visceral_fat`), `source_model` (the model that contributed most voxels), `flags`. Only labels present in the map are listed. |
| `provenance.json` | Engine version, settings, run plan and why, each model with licence, weight ids, status and runtime, software versions, TotalSegmentator run reports (file paths removed), confirmation that usage statistics are off, SHA-256 of the input and outputs. No file names (scan file names often contain patient identifiers). |
| `image.nii.gz` | Only when the input was too large and was resampled: the image the labels belong to. `qc.image` says which image the labels match (`"input"` or `"image.nii.gz"`). |

`qc` holds `scan` (modality and how it was decided, size, spacing, slice spacing, metal volume, flags),
`grid`, `regions` (detected regions with marker volume in mL), `warnings`, `failed_models`,
`structures` (per name: voxels, volume_ml, mean/sd HU, faces touched, bounding box, connected components,
voxels per source model, the plausibility bands used) and `fusion.discarded_voxels` per model.

Ids are stable: `total` keeps its own ids 1–117; every other structure has a fixed id ≥ 200 in
`resources/label_table.json`. The table is regenerated by `scripts/ctseg_build_resources.py`, which appends new
names and never renumbers. Colours and SNOMED codes come from SlicerTotalSegmentator (Apache-2.0, see
`resources/NOTICE`). Where it has no colour, a tissue-class colour is used, shifted slightly per structure.

## Pipeline

1. **Licence check** of the configuration and of `TOTALSEG_WEIGHTS_PATH` (before anything is loaded).
2. **Input**: NIfTI, or a DICOM folder through `emgmirror.ct.dicom` (IOP/IPP geometry, tilt refusal, finest
   series). The voxel count is read from the header first. Above `refuse_voxels` (2·10⁹) the scan is refused.
   Above `max_voxels` (512·512·1000) it is read in slabs and resampled to a coarser grid that fits, so the
   full-resolution volume is never held in memory.
3. **Input checks**: CT vs MR (DICOM `Modality`, otherwise intensities; `--modality` overrides). Slice spacing
   above 3 mm gives a warning; above 5 mm the scan is refused. Metal (≥ 3000 HU, ≥ 0.1 mL) is flagged. A
   possible raw CT without its rescale intercept, or a field of view without air, gives a warning.
4. **Base model**: TotalSegmentator `total` (MR: `total_mr`), plus `body` (`body_mr`).
5. **Region detection** from marker-structure volumes (≥ 5 mL): head, neck, thorax, abdomen, pelvis, spine,
   upper/lower limb, lumbar spine, and limb soft tissue (`body_extremities` ≥ 50 mL).
6. **Region subtasks** (open weights only): head → `craniofacial_structures`, `head_glands_cavities`;
   neck → `headneck_bones_vessels`, `headneck_muscles`; thorax → `lung_vessels`, `trunk_cavities`;
   abdomen → `liver_segments` (only with a liver), `trunk_cavities`; spine → `vertebrae_pp_refined`.
7. **MOOSE** (CT only): limbs in view → `clin_ct_peripheral_bones` (radius, ulna, carpals, hand and foot bones,
   tibia, fibula, patella: the only commercial-safe source for these). Lumbar trunk wall in view →
   `clin_ct_body_composition` (skeletal muscle, subcutaneous and visceral fat). MOOSE `clin_ct_muscles` is not
   used because it only repeats muscles that `total` already segments.
8. **Fusion**, **QC**, outputs.

A failing optional model is recorded in `failed_models` and `provenance.json`, and every other structure is
still delivered. Only a failure of the base model fails the job.

## Fusion rule

Each model label is mapped by name to the canonical table, so the same structure from two models is one id.
Each label gets a rank from its source:

| Rank | Source | Role |
|---|---|---|
| 10 | `body` / `body_mr` | container, fills gaps only |
| 11 | `head` (from `craniofacial_structures`) | container |
| 12 | `trunk_cavities` | container |
| 20 | MOOSE `clin_ct_body_composition` | tissue |
| 35 | `headneck_muscles` | extra structures |
| 40 | MOOSE `clin_ct_peripheral_bones` | limb bones not in `total` |
| 45 | `head_glands_cavities`, `headneck_bones_vessels`, `craniofacial_structures` | extra structures |
| 50 | `total` / `total_mr` | primary model |
| 60 | `liver_segments` (inside `liver`), `lung_vessels` (inside the lungs), `vertebrae_pp_refined` (inside vertebrae or background) | refinement |

A voxel takes a label only if that label's rank is strictly higher than the rank already there (background
is 0). A refinement may only overwrite its allowed parents. Sources are painted in ascending rank, and equal
ranks keep the first one in plan order, so the result does not depend on the order the models finished.
Refinement labels carry the flag `part_of:<parent>` (`liver_segment_4` → `part_of:liver`, lung vessels →
`part_of:lung`), so a viewer or the benchmark can merge them back into the parent.

No probabilities are fused in S1. The ranks are a fixed, documented stand-in until per-structure validation
Dice from the GPU benchmark can rank sources structure by structure.

## QC flags

| Flag | Meaning |
|---|---|
| `cut_by_scan_edge` | Touches a face of the image; continues outside the scan. No volume check. |
| `laterality_suspect` | A `*_left` structure lies to the patient's right of its `*_right` partner, or more than 10 mm right of the midline (mirrored image or left/right header error). |
| `volume_out_of_range` | Complete structure outside a wide adult plausibility band (`qc.VOLUME_ML`). |
| `hu_out_of_range` | Mean HU outside the band for its tissue class (bone, lung, air, fat, vessel, contrast-capable organ, soft tissue). CT only. |
| `very_small` | Fewer than 10 voxels. |
| `fragmented` | Single solid organ or bone whose largest piece holds < 50 % of its voxels. |
| `near_metal` | Metal within 10 mm. |
| `thick_slices`, `thin_structure_thick_slices` | Coarsest spacing > 3 mm; the second for vessels, ribs, nerves, airway wall and thin neck muscles. |
| `mr_no_hu_check` | MR: no HU checks. |
| `multi_source` | More than one model contributed voxels. |
| `part_of:<parent>` | Refinement of a parent structure. |

Scan-level flags in `qc.scan.flags`: `thick_slices`, `metal_present`, `resampled_input`, `mr_no_hu_checks`,
`possibly_unrescaled_ct`, `implausible_scale`, `direction_repaired`. `qc.laterality` holds the left/right check
summary. For DICOM input, `qc.series` holds the chosen series, the reason, and the alternatives.

## Licences and privacy

- `allowlist.py` is an allowlist: every model and every nnU-Net weight folder must be listed, or the run
  fails. It lists TotalSegmentator `total`, `total_mr`, `body`, `body_mr`, `lung_vessels`,
  `vertebrae_pp_refined`, `craniofacial_structures`, `headneck_bones_vessels`, `headneck_muscles`,
  `head_glands_cavities`, `trunk_cavities` and `liver_segments` (Apache-2.0); MOOSE
  `clin_ct_peripheral_bones` and `clin_ct_body_composition` (CC BY 4.0); and CADS `open` (CC BY-SA 4.0,
  validated but not run in S1). A configured TotalSegmentator licence key is itself an error.
  `head_muscles` is Apache-2.0 according to the research notes but is left out until it is re-checked.
- TotalSegmentator usage statistics: `send_usage_stats` is set to `false` in `$TOTALSEG_HOME_DIR/config.json`
  before every run, and any licence key found there is removed. TotalSegmentator still prints its standard
  notice; it checks the config before sending anything. `provenance.json` records the setting.
- Hosting: bake the weights into the image, run with no network access, and run
  `python -m emgmirror.ctseg check-licences --weights-dir ...` in CI. Written confirmation from the MOOSE
  authors for hosted commercial use is still open (critique.md L6).

## Known limits

- Normal adult anatomy only. Tumours, fractures, implants and post-surgical anatomy are mis-segmented, and
  children are not validated (no age check in S1: NIfTI has no age).
- CBCT is not detected.
- The plausibility bands are deliberately wide, and they are not reference ranges.
- Per-structure confidence from model agreement (a CADS second opinion) is not in S1.
- The CPU benchmark uses the 3 mm `fast` models. Numbers for the production setting (1.5 mm, GPU, `standard`)
  are still to be measured.
