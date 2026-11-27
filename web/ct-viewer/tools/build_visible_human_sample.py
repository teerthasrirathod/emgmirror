#!/usr/bin/env python3
"""Build a separate viewer result from BoneHub's Visible Human CT and labels.

This imports the NRRD voxel annotations and rebuilds surfaces with the existing
sample mesher. It intentionally does not use the repository's bundled STL or
NURBS surfaces and does not claim emgmirror model inference.
"""
from __future__ import annotations

import argparse
import gc
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import nibabel as nib
import numpy as np
import SimpleITK as sitk


DATASET_TITLE = "Visible Human Full-Skeleton 3D Bone Models"
DATASET_DOI = "10.57967/hf/10464"
SOURCE_DOI = "10.1038/s41597-022-01905-2"
LIMITATIONS = (
    "Reference annotations from BoneHub Visible Human; no emgmirror inference. "
    "CC BY 4.0, DOI 10.57967/hf/10464. Known source limitations: elbow region "
    "is incomplete/truncated; phalanges are grouped by digit; cranium and maxilla "
    "are grouped; sternum is undivided; sacrum includes coccyx; hyoid and costal "
    "cartilage are not labeled."
)
INCOMPLETE_ELBOW_BONES = {
    "humerus_left", "humerus_right", "radius_left", "radius_right", "ulna_left", "ulna_right",
}
ELBOW_LIMITATION = (
    "The source CT does not fully capture the elbow; distal humerus and proximal radius/ulna are incomplete. "
    "This is a source-acquisition limitation, not a mesh or label-name defect."
)


def slug(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    if not value:
        raise ValueError("A segmentation contains an empty anatomy name.")
    return value


def read_geometry(path: pathlib.Path) -> tuple[tuple[int, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    reader.ReadImageInformation()
    return (
        tuple(reader.GetSize()),
        tuple(float(x) for x in reader.GetSpacing()),
        tuple(float(x) for x in reader.GetOrigin()),
        tuple(float(x) for x in reader.GetDirection()),
    )


def verify_same_grid(reference: tuple, candidate: tuple, path: pathlib.Path) -> None:
    if reference[0] != candidate[0]:
        raise ValueError(f"{path.name}: size {candidate[0]} differs from CT {reference[0]}; refusing resampling.")
    for key, ref_values, got_values in zip(("spacing", "origin", "direction"), reference[1:], candidate[1:]):
        if not np.allclose(ref_values, got_values, rtol=0, atol=1e-5):
            raise ValueError(f"{path.name}: {key} differs from CT; refusing to hide a geometry mismatch.")


def segment_records(image: sitk.Image, path: pathlib.Path) -> list[tuple[str, int, int]]:
    records: list[tuple[str, int, int]] = []
    for key in image.GetMetaDataKeys():
        match = re.fullmatch(r"Segment(\d+)_Name", key)
        if not match:
            continue
        index = match.group(1)
        value_key = f"Segment{index}_LabelValue"
        layer_key = f"Segment{index}_Layer"
        if not image.HasMetaDataKey(value_key) or not image.HasMetaDataKey(layer_key):
            raise ValueError(f"{path.name}: {key} is missing its label value or layer metadata.")
        records.append((
            slug(image.GetMetaData(key)),
            int(image.GetMetaData(layer_key)),
            int(image.GetMetaData(value_key)),
        ))
    if not records:
        raise ValueError(f"{path.name}: no Segment*_Name metadata found.")
    components = image.GetNumberOfComponentsPerPixel()
    if any(layer < 0 or layer >= components for _, layer, _ in records):
        raise ValueError(f"{path.name}: segment layer metadata exceeds its {components} stored component(s).")
    return sorted(records)


def prepare_input(ct_path: pathlib.Path, source_seg_dir: pathlib.Path, work: pathlib.Path) -> pathlib.Path:
    ct_grid = read_geometry(ct_path)
    if len(ct_grid[0]) != 3:
        raise ValueError(f"Expected a 3D CT, got dimensions {ct_grid[0]}.")
    ct = nib.load(str(ct_path))  # header + affine only; do not materialize the full CT here.
    subject_id = ct_path.name.removesuffix(".nii.gz")
    input_dir = work / subject_id
    seg_out = input_dir / "segmentations"
    seg_out.mkdir(parents=True)

    ct_out = input_dir / "ct.nii.gz"
    try:
        ct_out.hardlink_to(ct_path)
    except OSError:
        shutil.copy2(ct_path, ct_out)

    source_files = sorted(source_seg_dir.glob("*.seg.nrrd"))
    if not source_files:
        raise FileNotFoundError(f"No *.seg.nrrd files found under {source_seg_dir}.")
    seen_names: set[str] = set()
    written = 0
    for path in source_files:
        image = sitk.ReadImage(str(path))
        verify_same_grid(ct_grid, (
            tuple(image.GetSize()),
            tuple(float(x) for x in image.GetSpacing()),
            tuple(float(x) for x in image.GetOrigin()),
            tuple(float(x) for x in image.GetDirection()),
        ), path)
        records = segment_records(image, path)
        array_zyx = sitk.GetArrayViewFromImage(image)
        for anatomy_id, layer, label_value in records:
            if anatomy_id in seen_names:
                raise ValueError(f"Duplicate stable anatomy ID {anatomy_id!r}; refusing to merge labels silently.")
            seen_names.add(anatomy_id)
            label_volume = array_zyx if array_zyx.ndim == 3 else array_zyx[..., layer]
            mask_zyx = np.asarray(label_volume == label_value, dtype=np.uint8)
            voxels = int(np.count_nonzero(mask_zyx))
            if voxels == 0:
                print(f"Skip empty annotation: {anatomy_id}", flush=True)
                continue
            mask_xyz = np.transpose(mask_zyx, (2, 1, 0))
            mask_img = nib.Nifti1Image(mask_xyz, ct.affine, ct.header.copy())
            mask_img.set_data_dtype(np.uint8)
            nib.save(mask_img, str(seg_out / f"{anatomy_id}.nii.gz"))
            written += 1
            print(f"Imported {anatomy_id}: {voxels:,} voxels", flush=True)
            del mask_img, mask_xyz, mask_zyx
        del array_zyx, image
        gc.collect()

    if written == 0:
        raise ValueError("No nonempty segmentations were imported.")
    (input_dir / "SOURCE.txt").write_text(
        f"{DATASET_TITLE}, subject {subject_id}\n"
        f"https://doi.org/{DATASET_DOI}; source CT paper https://doi.org/{SOURCE_DOI}; CC BY 4.0\n"
        f"{LIMITATIONS}\n",
        encoding="utf-8",
    )
    return input_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject_dir", type=pathlib.Path, help="Downloaded subject folder with CT NIfTI and Segmentation/*.seg.nrrd")
    parser.add_argument("output_dir", type=pathlib.Path, help="New result directory; must not already contain files")
    args = parser.parse_args()

    subject = args.subject_dir.resolve()
    output = args.output_dir.resolve()
    ct_path = subject / f"{subject.name}.nii.gz"
    seg_dir = subject / "Segmentation"
    if not ct_path.is_file():
        raise FileNotFoundError(f"Expected CT at {ct_path}.")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite nonempty result folder: {output}")
    output.mkdir(parents=True, exist_ok=True)

    tools_dir = pathlib.Path(__file__).resolve().parent
    viewer_dir = tools_dir.parent
    builder = tools_dir / "build_sample.py"
    with tempfile.TemporaryDirectory(prefix="emgmirror-visible-human-", dir=str(subject.parent)) as temp:
        prepared = prepare_input(ct_path, seg_dir, pathlib.Path(temp))
        subprocess.run([sys.executable, str(builder), str(prepared), str(output)], check=True, cwd=viewer_dir)

    raw_glb = output / "combined_web_raw.glb"
    web_glb = output / "combined_web.glb"
    node = shutil.which("node")
    cli = viewer_dir / "node_modules" / "@gltf-transform" / "cli" / "bin" / "cli.js"
    if not node or not cli.is_file():
        raise RuntimeError("The sample was meshed, but Node or glTF-Transform was not found for web-GLB compression.")
    subprocess.run([node, str(cli), "meshopt", str(raw_glb), str(web_glb)], check=True, cwd=viewer_dir)
    raw_glb.unlink()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["structures"]:
        if item["id"] in INCOMPLETE_ELBOW_BONES:
            item["source_status"] = "partial"
            item["source_status_reason"] = ELBOW_LIMITATION
    (output / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    systems: dict[str, int] = {}
    for item in manifest["structures"]:
        system = item.get("display", {}).get("system", "other")
        systems[system] = systems.get(system, 0) + 1
    print(f"Visible Human reference result: {len(manifest['structures'])} meshes; systems={systems}; output={output}")
    print("Source note:", manifest.get("source", {}).get("segmentation_kind", "missing"))


if __name__ == "__main__":
    main()
