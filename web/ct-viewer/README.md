# CT 3D Viewer (web)

Web viewer for CT-to-3D results: a PBR 3D model view (three.js / React-Three-Fiber) and CT slices (NiiVue 1.0).
**Research and education use only; not for diagnosis, treatment or surgical planning.**

Milestone S3 of [docs/ct_platform/PLAN.md](../../docs/ct_platform/PLAN.md). Results, screenshots and test output:
[docs/ct_platform/results/S3_viewer.md](../../docs/ct_platform/results/S3_viewer.md).

## Run

```bash
npm install
npm run sample      # downloads one public TotalSegmentator case (~48 MB compressed) and builds public/sample/
npm run dev         # http://localhost:5173/
```

`npm run sample` builds the TotalSegmentator v2.0.1 small-subset subject `s0011` from the Zenodo
reference masks. It is a thorax, abdomen and pelvis scan, not a head-to-toe study. The 117 structures
are dataset reference labels, not emgmirror model predictions. The sample is kept in ignored local
data folders and is not committed. The command needs Python 3 with `numpy scipy nibabel scikit-image
trimesh fast_simplification lxml`, Node dependencies from `npm install`, and network access to
`zenodo.org` if the subject has not already been downloaded.

Open another result folder with `?result=<url-or-path>/` (it must contain `manifest.json`).
Add `&mode=reference` for the read-only reference mode.

The upload app defaults to the research `skeletal` segmentation profile: full-resolution TotalSegmentator plus
the MOOSE peripheral-bone task, scheduled even when baseline limb markers are absent. It is CPU-intensive and
does not guarantee complete anatomy. Set `CTAPP_SEGMENTATION_PROFILE=standard` to use marker-planned optional
limb processing. The skeletal profile rejects `CTAPP_FAST=1` and `CTAPP_ENABLE_MOOSE=0` at startup instead of
silently skipping its required model path. Each result records the profile and model coverage states.

## Labelling (build-time)

```bash
npm run build                                         # "Research use only. Not for clinical use." (default)
VITE_LABEL_STATUS=investigational npm run build       # "Investigational device: for use only in an approved clinical investigation."
```

The label cannot be changed from the URL. Use-related hazards and the reviewer workflow are described in
[docs/ct_platform/device/S3_use_specification.md](../../docs/ct_platform/device/S3_use_specification.md).

## Build and test

```bash
npm run build       # type check + production build into dist/ (not committed)
npm test            # Playwright smoke and accessibility tests against `vite preview`
npm run screenshots # writes docs/ct_platform/results/img/S3_*.jpg
```

## Input: the result folder

`manifest.json` version 1 from the S2 mesher (`src/emgmirror/ctmesh/`), plus `ct.nii.gz` and `labels.nii.gz`.
The viewer also reads these optional keys, proposed for S2:

| key | use | fallback |
|---|---|---|
| `structures[].label` | value of this structure in `labels.nii.gz` | position in the list + 1 |
| `structures[].display.system` | `skeleton`, `organs`, `vessels`, `muscles`, `other` | guessed from the id |
| `structures[].display.material` | `bone`, `artery`, `vein`, `organ`, `muscle` | guessed from the id |
| `volumes.ct_preview` | a ≤256-voxel copy of the CT for the slice inside the 3D view | `ct.nii.gz`, downsampled in a worker |
| `study.{study_date, series_description, study_hash}` | de-identified identifiers shown on every screen | a red "Study details missing" message |

Mesh nodes in `combined_web.glb` are matched to structures by node or mesh name (= `id`).

## Layout

| path | what |
|---|---|
| `src/model/engine.ts` | render pipeline: opaque pass, stencil-capped cuts, GTAO, CT slice on the cut, depth-peeled translucency, tone mapping, GPU picking |
| `src/model/ModelView.tsx` | R3F canvas, camera presets, orbit, hover / click, keyboard |
| `src/model/clip.ts` | cut plane / box geometry in patient terms |
| `src/slices/SliceView.tsx` | NiiVue: axial / coronal / sagittal / all three, volume rendering, window/level, label overlay |
| `src/slices/ObliqueView.tsx` | oblique slice (NiiVue 1.0 has no oblique 2D view) |
| `src/lib/niftiWorker.ts` | NIfTI fetch + gunzip + half-float conversion in a worker |
| `src/ui/copy.ts` | all interface text |
| `src/review/` | review, edit and approval records bound to the result version hash |
| `src/ui/StatusBar.tsx` | label banner, study identifiers, version, approval status, offline state |
| `public/sw.js`, `src/lib/offline.ts` | offline cache for reference mode |
| `tools/` | sample download (HTTP range reads of the Zenodo zip), sample builder, old-viewer comparison |

## Licences

three.js, React, React-Three-Fiber, drei, meshoptimizer, fflate: MIT. NiiVue: BSD-2-Clause.
Sample data: TotalSegmentator dataset small v2.0.1 (Wasserthal et al.), CC BY 4.0,
doi:10.5281/zenodo.10047263. The sample renders reference masks and must not be described as
segmentation performed by the emgmirror backend.
