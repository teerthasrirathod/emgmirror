# Web viewer and mesh stack research for the CT-to-3D service (checked 2026-11-21)

**How this was checked.** `gh` is not installed on this machine, so I took repo metadata from the public GitHub REST API (`api.github.com/repos/OWNER/REPO`). After 27 calls it hit the unauthenticated rate limit. Package versions come from registry.npmjs.org and pypi.org. Nothing was installed and no project or patient file was touched. "UNVERIFIED" means I could not confirm it from a source in this session.

## 1. Repo health (licence / stars / last push / archived)
| Repo | Licence | Stars | Last push | Archived |
|---|---|---|---|---|
| OHIF/Viewers | MIT | 4416 | 2026-11-20 | no |
| cornerstonejs/cornerstone3D (npm @cornerstonejs/core 5.19.4, 2026-11-17) | MIT | 1171 | 2026-11-21 | no |
| niivue/niivue (0.x, **retiring**) | BSD-2-Clause | 506 | 2026-11-16 | to be archived 2027-01-15; development moved to niivue/mono, 1.0.0-rc.235 on npm `next` |
| Kitware/vtk-js (npm 38.1.0, 2026-11-18) | BSD-3-Clause | 1551 | 2026-11-18 | no |
| Kitware/VolView | Apache-2.0 | 306 | 2026-11-20 | no |
| InsightSoftwareConsortium/itk-wasm | Apache-2.0 | 235 | 2026-11-09 | no |
| Slicer/Slicer | NOASSERTION (custom BSD-style Slicer License) | 2741 | 2026-11-20 | no |
| Kitware/trame | NOASSERTION (GitHub could not classify it; actual licence UNVERIFIED) | 712 | 2026-11-13 | no |
| KitwareMedical/trame-slicer | NOASSERTION | 70 | 2026-11-16 | no |
| rii-mango/Papaya | NOASSERTION (BSD-style text "Copyright (c) 2012-2015, RII-UTHSCSA") | 583 | **2024-06-10 (stale)** | no |
| mrdoob/three.js (0.187.0, 2026-10-30) | MIT | 119056 | 2026-11-21 | no |
| pmndrs/react-three-fiber | MIT | 33851 | 2026-10-21 | no |
| BabylonJS/Babylon.js | Apache-2.0 | 26415 | 2026-11-21 | no |
| Kitware/VTK (PyPI vtk 9.7.2) | NOASSERTION (BSD-3 style Copyright.txt) | 3238 | 2026-11-21 | no |
| pyvista/pyvista (0.49.2) | MIT | 3885 | 2026-11-20 | no |
| mikedh/trimesh | MIT | 3729 | 2026-11-19 | no |
| google/draco | Apache-2.0 | 7563 | 2026-11-16 | no |
| zeux/meshoptimizer (1.3.0) | MIT | 8888 | 2026-10-14 | no |
| donmccurdy/glTF-Transform (4.5.1) | MIT | 2092 | 2026-10-20 | no |
| cnr-isti-vclab/PyMeshLab | **GPL-3.0** | 992 | 2026-02-03 | no |
| pyvista/pymeshfix | **GPL-3.0** | 411 | 2026-11-01 | no |
| MeshInspector/MeshLib | **Non-commercial & education licence** (LICENSE: "NON-COMMERCIAL & education LICENSE AGREEMENT ... AMV Consulting") | 848 | 2026-11-21 | no |
| wasserth/TotalSegmentator | Apache-2.0 code; **some weights restricted** (see 6) | 3172 | 2026-11-20 | no |
| lassoan/SlicerTotalSegmentator | Apache-2.0 | 293 | 2026-10-28 | no |
| QIICR/dcmqi | BSD-3-Clause | 282 | 2026-11-16 | no |
| ImagingDataCommons/highdicom | MIT | 243 | 2026-11-21 | no |
| dcmjs-org/dcmjs | MIT | 359 | 2026-11-17 | no |
| pmndrs/postprocessing, N8python/n8ao, Slicer/SlicerWeb, MONAILabel | UNVERIFIED (rate-limited) | | | |

Source for every row: `https://api.github.com/repos/<repo>`. Licence texts: https://raw.githubusercontent.com/MeshInspector/MeshLib/master/LICENSE and https://raw.githubusercontent.com/rii-mango/Papaya/master/LICENSE. NiiVue move: https://github.com/niivue/niivue (README).

## 2. Viewer comparison

**OHIF v3 + Cornerstone3D**
- **Formats.** OHIF loads DICOM through DICOMweb or local files.
  - DICOM-SEG and RTSTRUCT are supported. v3.11 added DICOM Label Map Segmentation (https://ohif.org/release-notes/3p11/), and v3.13 added labelmap SEG in any transfer syntax, including compressed (https://ohif.org/release-notes/3p13).
  - NIfTI: Cornerstone3D has a nifti volume loader (UNVERIFIED in this session).
- **Views and segmentation.**
  - MPR and GPU volume rendering use Cornerstone3D, which is built on vtk.js (https://www.cornerstonejs.org/docs/getting-started/overview).
  - Labelmaps can be shown as surfaces in the 3D viewport since 3.9 (https://ohif.org/release-notes/3p9). v3.12 lists surface representations in the segmentation table and fixes labelmap-to-surface (https://ohif.org/release-notes/3p12).
  - Surface/volume intersection is drawn on slices (https://v1.cornerstonejs.org/live-examples/surfaceRendering).
- **Tools.** Full measurement toolset (length, angle, ROI stats); segment statistics and undo/redo arrived in v3.10, plus WebGPU 3D GrowCut and local AI-assisted segmentation (https://ohif.org/release-notes/3p10/). Freehand segmentation came in v3.12.
- **Mobile is weak.**
  - iOS kills pages that use too much WebGL memory (https://community.ohif.org/t/ios-memory-limit-problem/1086, https://bugs.webkit.org/show_bug.cgi?id=300782).
  - Volume viewports can lose the GPU context (https://github.com/OHIF/Viewers/issues/3207).
  - Volume data is kept in CPU memory as well as on the GPU (https://community.ohif.org/t/optimize-resource-usage-for-large-ct-mri-stacks-in-volume-viewport/1633).
  - It needs cross-origin isolation (SharedArrayBuffer) for MPR and volume rendering (https://v3-docs.ohif.org/faq/technical).
- **Embedding.** It is a heavy app; you either extend it through modes/extensions or use Cornerstone3D directly in your own React app.
- **Verdict.** The most clinical-grade option, with the strongest DICOM-SEG round-trip.

**NiiVue**
- Formats: WebGL2-based. Over 30 volume and mesh formats, including NIfTI, plus mesh formats OBJ, STL, PLY, GIfTI, VTK, X3D (https://github.com/niivue/niivue README). DICOM comes through a plugin/extension.
- Features: drawing/segmentation UI, clip planes, "shiny" volume rendering and mesh layers (https://niivue.github.io/niivue).
- The 1.0 rewrite in niivue/mono adds WebGPU with WebGL2 fallback, smaller bundles, React bindings and a web component (same README).
- Prior art: ct2print.org and brain2print.org already do CT/voxels-to-printable-mesh in the browser (same README). Study these.
- DICOM-SEG import: UNVERIFIED.
- Verdict: the lightest and easiest to embed; probably the best on mobile (UNVERIFIED, no benchmark found); weaker on clinical DICOM.

**vtk.js**
- The low-level engine under Cornerstone3D and VolView.
- GPU ray casting with gradient opacity (product of scalar and gradient transfer functions), multi-component volumes, per-component properties and labelmap outline thickness (https://kitware.github.io/vtk-js/api/Rendering_Core_VolumeProperty.html, https://kitware.github.io/vtk-js/api/Rendering_Core_Volume.html).
- Local ambient occlusion, volumetric shadows and cinematic rendering (https://www.kitware.com/cinematic-volume-rendering/, https://kitware.github.io/vtk-js/examples/VolumeMapperLightAndShadow.html).
- Labelmap edge projection example: https://kitware.github.io/vtk-js/examples/LabelmapEdgeProjection.html.
- No UI of its own; a lot of work to build on directly.

**Kitware VolView** (Apache-2.0, a Vue app on vtk.js and itk-wasm)
- Rendering: cinematic volume rendering since 4.0 (https://www.kitware.com/kitware-brings-3d-cinematic-rendering-to-your-web-browser-with-volview-4-0/). Image layers / fusion since 4.1 (https://kitware.com/volview-4-1).
- Segmentation:
  - 4.2 made label editing segment-oriented (https://www.kitware.com/volview-4-2-release-notes/).
  - 4.3 reads DICOM-SEG labels and colours, puts overlapping segments into separate groups, and adds per-segment opacity/outline (https://kitware.com/volview-4-3-release).
  - 4.5 round-trips `.seg.nrrd` with Slicer and adds Fill Holes (https://www.kitware.com/volview-4-5/).
- DICOMweb, S3/GCS and Orthanc integrations (4.1). Fewer measurement tools than OHIF (UNVERIFIED detail).
- Verdict: best-looking volume rendering out of the box. Good candidate for the "3D" tab, or as a reference to copy shaders from.

**itk-wasm**
- A library, not a viewer.
- Browser and WASI packages: `@itk-wasm/dicom` (7.6.4, uses GDCM/DCMTK), image-io, mesh-io, `@itk-wasm/cuberille` (image-to-mesh), morphological-contour-interpolation (https://docs.itk.org/projects/wasm/en/latest/introduction/packages.html, https://kitware.com/reading-dicom-images-and-non-image-sop-classes-in-javascript-and-python).
- Useful for parsing on the client before upload, e.g. anonymising and checking that a series is complete.

**3D Slicer / trame-slicer / SlicerTrame**
- Slicer is the desktop gold standard. trame-slicer exposes Slicer's MRML scene and core logic as a Python/trame/Vue web app with server-side rendering.
- It is bare-bones and work in progress, with limited file loading (https://pypi.org/project/trame-slicer/). Announced 2025-11-20 (https://www.kitware.com/trame-slicer-announcement/). Can be launched from inside Slicer 5.10 (https://discourse.slicer.org/t/new-extension-slicer-trame-bringing-3d-slicer-to-the-web/45298).
- Remote rendering needs a GPU server per session, so it is costly for a public multi-user service.
- Verdict: use headless Slicer/VTK on the backend for processing, not as the public viewer.

**Papaya**
- Last push 2024-06-10 and no WebGL2 volume rendering (UNVERIFIED detail). **Reject.**

**three.js / React-Three-Fiber**
- A general 3D engine, not a medical one: no DICOM, no MPR.
- Best ecosystem for "premium" surface-mesh display:
  - PBR materials and GLTFLoader with Draco/meshopt decoders.
  - `Material.clippingPlanes` and post-processing SSAO/GTAO (https://threejs.org/docs/ ; GTAO/N8AO detail UNVERIFIED).
- Mobile performance is good for decimated GLB meshes. Huge community (119k / 34k stars).

**Babylon.js**
- Apache-2.0. Comparable PBR, SSAO2 pipeline and clip planes (UNVERIFIED in this session).
- Smaller medical/R3F ecosystem than three.js.
- No reason to choose it over three.js unless you want its editor or inspector.

## 3. Mesh generation

**Surface extraction**
- **Marching cubes** (skimage, `vtkMarchingCubes`, trimesh): one label at a time. Produces staircase artefacts on binary masks, so it needs smoothing.
- **Flying edges** (`vtkFlyingEdges3D`, `vtkDiscreteFlyingEdges3D`):
  - Same output as marching cubes but much faster and multithreaded.
  - Faster than SurfaceNets when there are only a few labels.
  - Per-label surfaces do not share boundaries, so neighbouring organs can overlap or leave gaps.
  - Source: https://discourse.vtk.org/t/new-isocontouring-filter-for-discrete-labled-volumetric-data-vtksurfacenets3d/10614
- **SurfaceNets** (`vtkSurfaceNets3D`):
  - Built for label maps. Organs that touch share one boundary, and sharp boundaries are preserved.
  - Constrained smoothing is built in (constraint distance keeps vertices within about 1 voxel). Output can be one surface per label or boundaries between label pairs.
  - Based on Frisken, JCGT 2022 (https://pmc.ncbi.nlm.nih.gov/articles/PMC9623606/).
  - Class reference: https://vtk.org/doc/nightly/html/classvtkSurfaceNets3D.html. Needs VTK ≥ 9.3; 9.5 added automatic smoothing constraints (https://vtk.org/doc/nightly/release/9.5/html/classvtkSurfaceNets3D.html).
  - PyVista wrapper: `ImageData.contour_labeled` (defaults: 50 iterations, relaxation 0.5, constraint distance 1) (https://docs.pyvista.org/api/core/_autosummary/pyvista.imagedatafilters.contour_labeled).
  - Nightly VTK also has `vtkGeneralizedSurfaceNets3D` (https://vtk.org/doc/nightly/html/vtkGeneralizedSurfaceNets3D_8h_source.html).
  - VTK devs say the smoothing defaults are not well tuned, so expect to tune them.
- **Cuberille** (itk-wasm): an alternative that can run in the browser.

**Smoothing that preserves volume**
- `vtkWindowedSincPolyDataFilter` (Taubin-style low-pass filter) does not shrink the surface the way Laplacian smoothing does.
- PassBand: 0 to 2, default 0.1; lower values smooth more. 10–20 iterations is usually enough. Turn on `NormalizeCoordinates` for scale stability.
- Sources: https://www.vtk.org/doc/nightly/html/classvtkWindowedSincPolyDataFilter.html, https://docs.pyvista.org/api/core/_autosummary/pyvista.PolyDataFilters.smooth_taubin.html
- Forum tests suggest the Blackman window with normalisation gives correct scale and no oscillation (anecdotal; https://discourse.vtk.org/t/slight-offset-in-vtkwindowedsincpolydatafilter-if-normalization-is-enabled/7676/16).
- Slicer's default is roughly passband 0.01–0.1 plus quadric decimation (UNVERIFIED exact values).
- Always measure the volume change, for example with `vtkMassProperties` or trimesh `.volume`, and report it.

**Decimation**
- `vtkQuadricDecimation`, or `meshoptimizer` `simplify` (MIT, attribute-aware, also available in glTF-Transform's `simplify()`).
- Run the decimation on the server, then build LODs: about 50–200k triangles per large organ for print, about 10–50k for web/mobile (heuristic, UNVERIFIED).

**Watertight / manifold repair**
- trimesh (MIT): `fill_holes`, `fix_normals`, `is_watertight`.
- `manifold3d` (Apache-2.0, UNVERIFIED in this session) for guaranteed-manifold booleans.
- pymeshfix and PyMeshLab are **GPL-3.0**. Running them inside a hosted service is not distribution, so it is OK under GPL (not AGPL), but you cannot ship them in a closed client.
- **MeshLib is non-commercial only, so avoid it.**
- SurfaceNets per-label output with closed surfaces is usually watertight. Verify every mesh before STL/3MF export.

**Export formats**
- **STL** (printing): trimesh.
- **3MF**: trimesh has `export_3MF` (https://raw.githubusercontent.com/mikedh/trimesh/main/trimesh/exchange/threemf.py; exporter registry in export.py). 3MF carries colours and multiple objects, so it is better than STL for multi-organ prints.
- **OBJ**: trimesh.
- **GLB for the web**: trimesh → glTF-Transform. Use `EXT_meshopt_compression` + `KHR_mesh_quantization`: meshopt decodes "considerably faster" than Draco, handles all attributes, and should be gzip/brotli-compressed on top. Draco can shrink geometry-heavy files by about 95% but decodes slower, and the two cannot be combined on the same mesh. Sources: https://gltf-transform.dev/modules/extensions/classes/EXTMeshoptCompression, https://gltf-transform.dev/modules/extensions/classes/KHRDracoMeshCompression
- **DICOM-SEG**: highdicom (MIT) or dcmqi `itkimage2segimage` (BSD-3). OHIF 3.11+ supports the DICOM Label Map SEG standard.
- **NIfTI / `.seg.nrrd`**: for Slicer and VolView round-trips.

**Coordinates and accuracy**
- Mesh in patient millimetres (LPS):
  - Extract on the label map with spacing/origin/direction applied: a `vtkImageData` with a direction matrix (VTK ≥ 9.2), or transform vertices by the image's index-to-LPS affine afterwards.
  - Never export in voxel indices. Slicer itself writes STL/OBJ in LPS by default since 4.11 (UNVERIFIED version).
  - glTF is right-handed, Y-up, in metres. Keep vertex data in mm/LPS inside the GLB and add one root node with the LPS→glTF axis swap and 0.001 scale. Store the original affine, units and coordinate system in `extras`, so measurements in the viewer can be converted back to mm.
- Report surface error in mm:
  - Compare each final mesh with the un-smoothed, un-decimated boundary: symmetric Hausdorff, 95th-percentile and mean surface distance (`vtkDistancePolyDataFilter`, `vtkHausdorffDistancePointSetFilter`, or `trimesh.proximity.signed_distance`).
  - Also report the volume difference against the voxel count × voxel volume. Save all of this in a per-label QC JSON next to the model.

## 4. Colour, naming, "premium" look

**Naming and codes**
- Use SNOMED-CT category/type/modifier triplets, which DICOM-SEG requires.
- Slicer's Terminologies module ships the DICOM master list and the "3D Slicer General Anatomy" context. That context matches the GenericAnatomyColors table, and each entry includes a recommended display RGB (https://slicer.readthedocs.io/en/5.10/user_guide/modules/terminologies.html).
- The JSON schema comes from dcmqi (`segment-context-schema.json`).
- Ready-made per-structure SNOMED codes and colours for TotalSegmentator labels:
  - https://raw.githubusercontent.com/lassoan/SlicerTotalSegmentator/main/TotalSegmentator/Resources/SegmentationCategoryTypeModifier-TotalSegmentator.term.json
  - https://raw.githubusercontent.com/lassoan/SlicerTotalSegmentator/main/TotalSegmentator/Resources/totalsegmentator_snomed_mapping.csv
  - Both are verified to exist (HTTP 200). Licence: the repo is Apache-2.0.
- SNOMED terms that are included in DICOM may be used without a fee (https://slicer.org/wiki/Documentation/4.4/Extensions/Reporting).
- Example GenericAnatomyColors value: tissue rgb(128,174,128) (https://www.slicer.org/wiki/Slicer3:2010_GenericAnatomyColors).

**Premium rendering** (design recommendations, not sourced facts)
- PBR materials with low metalness:
  - Bone: off-white, roughness about 0.6.
  - Vessels: arteries red, veins blue, with a slight clearcoat.
  - Organs: muted, slightly translucent; use per-material opacity with depth-peeling or order-independent transparency.
- SSAO/GTAO for contact shadows, plus an HDRI environment light and a soft ground shadow.
- Interaction:
  - Clipping planes / box clip with capped cross-sections, using a stencil cap.
  - **Show the CT slice as a textured plane** that moves with the clip plane: MPR inside the 3D view, the strongest "medical" cue. Cornerstone3D draws surface/slice intersection lines in MPR.
  - Click to isolate an organ, a label hover tooltip with SNOMED name and volume in mL, and smooth camera presets (anterior/lateral/superior in LPS terms).
- For the raw volume:
  - LAO and shadows in vtk.js/VolView cinematic mode.
  - Gradient opacity to suppress soft tissue.
  - CT transfer-function presets: bone, angio, soft tissue.

## 5. Recommendation

**Viewer stack: Cornerstone3D (inside OHIF v3 or a custom React shell) for CT, MPR, measurements and DICOM-SEG, plus a three.js/React-Three-Fiber "Model" view for the GLB meshes.**

Why this split:
- Cornerstone3D/OHIF is the only option that is MIT-licensed, actively maintained (pushed today), and covers DICOM, DICOM-SEG/RTSTRUCT, labelmap-SEG, MPR, GPU volume rendering, surface segmentation and clinical measurement tools in one place.
- three.js gives mobile-friendly, premium-looking mesh rendering that Cornerstone's vtk.js surfaces do not match (PBR, SSAO, capped clipping, CT-slice plane).
- Keep VolView's cinematic vtk.js rendering as the optional desktop "volume" mode, either borrowed or embedded (Apache-2.0).
- Fallback if OHIF proves too heavy: NiiVue 1.0 (niivue/mono, WebGPU, BSD-2). Track its 1.0 GA, since the 0.x repo is archived 2027-01-15.

**Mesh pipeline (Python backend, all permissive licences):**
1. Load the label map (NIfTI/DICOM-SEG) with its LPS geometry (SimpleITK/highdicom).
2. Run `vtkSurfaceNets3D` (VTK ≥ 9.5) on the whole multi-label map, one closed surface per label, with constrained smoothing. Use `vtkDiscreteFlyingEdges3D` + `vtkWindowedSincPolyDataFilter` (passband about 0.05, normalise on, 15–20 iterations) as the fallback for 1–3 labels or very thin structures.
3. `vtkQuadricDecimation` or meshoptimizer simplify to target triangle budgets, with web and print LODs.
4. trimesh repair: fill holes, fix normals, check watertight. Add manifold3d if booleans are needed. Avoid MeshLib; avoid GPL tools in anything distributed to clients.
5. QC per label: Hausdorff / HD95 / mean surface distance in mm against the raw voxel boundary, and percentage volume change, written to a JSON report.
6. Export: GLB (glTF-Transform: meshopt + quantization + brotli; root-node LPS→glTF transform; SNOMED code, name and colour in `extras`), STL and 3MF per label and combined (trimesh), DICOM-SEG (highdicom/dcmqi) and NIfTI for clinical tools. Colours and codes come from the SlicerTotalSegmentator `.term.json` / Slicer General Anatomy.

## 6. Licence traps for a hosted service
- **TotalSegmentator:** code is Apache-2.0, but the subtasks `heartchambers_highres`, `appendicular_bones`, `tissue_types`, `face` and others need a licence. It is free for non-commercial use; commercial use needs a paid licence (https://discourse.slicer.org/t/total-segmentator-1-heartchambers-highres/32505, https://github.com/wasserth/TotalSegmentator). Check the weights licence for every model separately from its code licence.
- **MeshLib:** non-commercial only.
- **PyMeshLab and pymeshfix:** GPL-3.0.
- **Papaya:** stale.
- **trame and Slicer:** the custom licence texts were not read in full, so treat them as UNVERIFIED.

## 7. Repos to study
- OHIF/Viewers: `extensions/cornerstone-dicom-seg`, segmentation service, 3D four-up mode.
- cornerstonejs/cornerstone3D: `packages/tools` segmentation, PolySeg labelmap↔surface conversion.
- Kitware/VolView: cinematic rendering presets, DICOM-SEG import, segment groups.
- Kitware/vtk-js: VolumeMapperLightAndShadow and LabelmapEdgeProjection examples.
- niivue/mono, plus the ct2print.org and brain2print.org apps (browser voxel→printable mesh).
- InsightSoftwareConsortium/itk-wasm: dicom, cuberille, mesh-io.
- Kitware/VTK: `vtkSurfaceNets3D`, `vtkWindowedSincPolyDataFilter`, `vtkQuadricDecimation`. pyvista/pyvista: `contour_labeled`.
- mikedh/trimesh: `exchange/threemf.py`, repair, proximity.
- zeux/meshoptimizer and donmccurdy/glTF-Transform: simplify, meshopt, quantize.
- ImagingDataCommons/highdicom and QIICR/dcmqi: DICOM-SEG writing, segment-context schema.
- lassoan/SlicerTotalSegmentator: SNOMED terminology and colour JSON/CSV.
- Slicer/Slicer: Terminologies module, Segmentations closed-surface conversion parameters.
- KitwareMedical/trame-slicer: only if you ever want server-rendered Slicer.
- mrdoob/three.js and pmndrs/react-three-fiber: GLTFLoader with MeshoptDecoder, clipping planes, GTAO.

## Search sources not linked above
- https://arxiv.org/pdf/2512.15921: TotalSegmentator SNOMED mapping and dcmqi DICOM-SEG pipeline.
- https://zenodo.org/records/7924637: VolView 4.1 record.
- https://ohif.org/newsletters/2025-04-09-OHIF%20Viewer%20v3.10%20with%20Local%20AI%20Enhanced%20Segmentation%20and%20More--RELEASE-NOTE3p10.html