# SOUP and licence register: S2 mesh pipeline

Research use, not for diagnosis or surgical planning.

Status: draft of 2026-11-22 for `src/emgmirror/ctmesh` 0.1.0 (IEC 62304 section 8 / FDA OTS guidance
fields). Versions are the ones the tests and the benchmark ran with; each run also writes them to
`manifest.json` → `provenance.software`. Pin exact versions with hashes (`pip-compile --generate-hashes`) before
any release build; this file is then updated in the same change. No GPL, AGPL or non-commercial component is
used. MeshLib, PyMeshLab and pymeshfix are deliberately not used.

## Shipped in the mesh service (server side)

| Item | Version tested | Licence | Purpose in S2 | Requirements we rely on | Known anomalies found / handling | Verified by |
|---|---|---|---|---|---|---|
| VTK (Kitware) | 9.7.2 (needs >= 9.5) | BSD-3-Clause | `vtkSurfaceNets3D`, `vtkDiscreteFlyingEdges3D`, `vtkWindowedSincPolyDataFilter`, `vtkImplicitPolyDataDistance` | Exact voxel-boundary quads at 0 iterations; consistent quad order between runs; non-shrinking smoothing; exact point-to-surface distance | SurfaceNets emits coincident duplicate points: merged on the voxel-corner lattice. Built-in SurfaceNets smoother shrinks thin structures 10-50 %: not used by default. `SetOutputStyleToDefault` deprecated in 9.7: not called. `vtkImplicitPolyDataDistance` has no locator setter in the Python wrapping. | `tests/ctmesh/test_surface_qc.py`, `scripts/ctmesh_tune.py` |
| trimesh | 5.2.1 | MIT | Mesh container, volume/area, STL and 3MF export | Correct volume sign, watertight test, binary STL | 3MF export writes random object UUIDs: rewritten deterministically (`export._deterministic_3mf`). `proximity.closest_point` needs `rtree` (not used). | `test_exports_pipeline.py`, HAZ06 |
| manifold3d | 3.5.5 | Apache-2.0 | 2-manifold acceptance check | Rejects non-manifold or open meshes | None found | `test_exports_pipeline.py::test_decimation_keeps_the_mesh_closed_and_close` |
| meshoptimizer (Python bindings of zeux/meshoptimizer) | 0.2.30a0 (alpha) | MIT | `simplify` (LODs), vertex-cache/fetch optimisation, `EXT_meshopt_compression` codecs | Edge-collapse respecting an absolute error cap; vertex codec v0 and index codec v1 output as the glTF extension requires | Alpha wrapper, installed from its source distribution (needs a C/C++ compiler). `decode_vertex_buffer(dtype=...)` under-allocates (segfault): not used. `decode_index_buffer` always returns uint32: decode at width 4. Simplify can leave coincident opposite triangle pairs: removed in repair. Independent check: Khronos glTF-Validator and glTF-Transform decode our files with 0 errors. Candidate to replace with a vetted build before release. | `test_exports_pipeline.py::test_glb_round_trip_both_encodings`, BENCH |
| highdicom | 0.29.0 | MIT | DICOM-SEG writer | Valid BINARY SEG with segment descriptions and plane positions | Writes random DimensionOrganizationUID and current ContributionDateTime: overwritten with deterministic values. | `test_exports_pipeline.py::test_dicom_seg_round_trips_labels_in_patient_space`, HAZ06 |
| pydicom | 3.0.2 | MIT | DICOM datasets and UIDs (`generate_uid` with fixed entropy) | Deterministic UID from entropy sources | None found | HAZ06 |
| SimpleITK | 2.5.8 | Apache-2.0 | NIfTI read/write with RAS→LPS handling | Origin/spacing/direction in LPS | None found | `test_geometry_contract.py::test_index_to_lps_matches_simpleitk` |
| NumPy | 2.5.3 | BSD-3-Clause (bundled: 0BSD, MIT, Zlib, CC0-1.0) | Arrays | — | — | all tests |
| SciPy | 1.18.1 | BSD-3-Clause | Distance transform (thin-label test), sparse graphs in `mesh_qc` | — | — | `test_surface_qc.py::test_label_depth` |

## Data shipped with the code

| Item | Version | Licence | Purpose | Notes |
|---|---|---|---|---|
| `totalsegmentator_snomed_mapping.csv` from lassoan/SlicerTotalSegmentator | main branch, fetched 2026-11-21 | Apache-2.0 | Names, SNOMED codes and colours for the TotalSegmentator fixtures | Used only by `fixtures.totalseg_to_contract`; S1 supplies codes in production. Attribution required (NOTICE page, S6). |

## Development and benchmark only (not shipped)

| Item | Version | Licence | Purpose |
|---|---|---|---|
| TotalSegmentator dataset ground truth (Zenodo 6802613) | v3.0.0 | CC BY 4.0 | Public benchmark subjects |
| three.js | 0.170.0 | MIT | Before/after renders (`scripts/ctmesh_render`) |
| Khronos glTF-Validator | 2.0.0-dev.3.10 | Apache-2.0 | GLB validation in the benchmark |
| glTF-Transform core/extensions | 4.5.1 | MIT | Independent GLB decode check |
| meshoptimizer (npm) | 1.3.0 | MIT | Meshopt decoder for the checks |
| Playwright + Chromium | preinstalled | Apache-2.0 / BSD | Headless rendering |
| Pillow | 12.3.0 | MIT-CMU | Side-by-side images |
| pytest | 9.1.1 | MIT | Tests |
