"""Print and clinical-tool exports: STL and 3MF per structure (LPS mm), DICOM-SEG for the whole label map.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import numpy as np
import trimesh

from . import DISCLAIMER, __version__
from .contract import LabelMap, snomed_triplet

STL_NOTE = f"{DISCLAIMER} Units mm, patient LPS."  # 80-byte binary STL header


def write_stl(path: str | Path, mesh: trimesh.Trimesh) -> Path:
    """Binary STL in LPS mm with the disclaimer in the 80-byte header (must not start with 'solid')."""
    data = bytearray(trimesh.exchange.stl.export_stl(mesh))
    data[:80] = STL_NOTE.encode("ascii")[:80].ljust(80, b" ")
    Path(path).write_bytes(bytes(data))
    return Path(path)


def write_3mf(path: str | Path, meshes: dict[str, tuple[trimesh.Trimesh, tuple[int, int, int]]]) -> Path:
    """3MF (unit millimetre, LPS coordinates) with one object per structure, named and coloured."""
    scene = trimesh.Scene()
    for name, (m, rgb) in meshes.items():
        g = trimesh.Trimesh(np.asarray(m.vertices), np.asarray(m.faces), process=False)
        g.visual.face_colors = np.tile(list(rgb) + [255], (len(g.faces), 1)).astype(np.uint8)
        scene.add_geometry(g, node_name=name, geom_name=name)
    scene.metadata["disclaimer"] = DISCLAIMER
    Path(path).write_bytes(trimesh.exchange.threemf.export_3MF(scene))
    return Path(path)


def _rgb_to_cielab(rgb):
    import highdicom as hd
    try:
        return hd.color.CIELabColor.from_rgb(*[int(c) for c in rgb])
    except Exception:  # pragma: no cover - older highdicom
        return None


def _reference_series(lm: LabelMap):
    """Minimal CT headers on the label map's grid, used as the SEG's source images when no original DICOM
    series is given. They carry only geometry and fresh UIDs (no pixel data, no patient identity)."""
    from pydicom.dataset import Dataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
    nz, ny, nx = lm.array.shape
    D = np.asarray(lm.direction, float)
    sx, sy, sz = lm.spacing
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    today = _dt.date.today().strftime("%Y%m%d")
    out = []
    for k in range(nz):
        ds = Dataset()
        ds.file_meta = Dataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.SOPClassUID, ds.SOPInstanceUID = CTImageStorage, generate_uid()
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.FrameOfReferenceUID = study, series, frame
        ds.Modality, ds.PatientID, ds.PatientName = "CT", "RESEARCH", "Research^Phantom"
        ds.PatientBirthDate, ds.PatientSex, ds.StudyID, ds.AccessionNumber = "", "O", "1", ""
        ds.StudyDate, ds.StudyTime, ds.ReferringPhysicianName = today, "000000", ""
        ds.SeriesNumber, ds.InstanceNumber = 1, k + 1
        ds.Rows, ds.Columns = ny, nx
        ds.PixelSpacing = [float(sy), float(sx)]
        ds.SliceThickness = float(sz)
        ds.ImageOrientationPatient = [float(v) for v in np.r_[D[:, 0], D[:, 1]]]
        ds.ImagePositionPatient = [float(v) for v in np.asarray(lm.origin, float) + D[:, 2] * sz * k]
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
        out.append(ds)
    return out


def write_dicom_seg(path: str | Path, lm: LabelMap, label_ids: list[int], source_images=None) -> dict:
    """DICOM-SEG (BINARY, one segment per structure, empty frames omitted) via highdicom. ``source_images``:
    the original CT series as pydicom datasets; without it a geometry-only reference series is synthesised
    and the result says so."""
    import highdicom as hd
    from pydicom.sr.codedict import codes
    from pydicom.uid import generate_uid
    synthetic = source_images is None
    src = _reference_series(lm) if synthetic else list(source_images)
    ids = [i for i in label_ids if (lm.array == i).any()]
    seg_arr = np.zeros(lm.array.shape, np.uint16)
    descs = []
    for n, lid in enumerate(ids, start=1):
        seg_arr[lm.array == lid] = n
        lab = lm.label(lid)
        sn = snomed_triplet(lab.snomed)
        if sn:
            cat = hd.sr.CodedConcept(sn["category"]["value"], sn["category"]["scheme"], sn["category"]["meaning"])
            typ = hd.sr.CodedConcept(sn["type"]["value"], sn["type"]["scheme"], sn["type"]["meaning"])
            mods = [hd.sr.CodedConcept(sn["modifier"]["value"], sn["modifier"]["scheme"], sn["modifier"]["meaning"])] \
                if sn["modifier"] else None
        else:
            cat = hd.sr.CodedConcept("85756007", "SCT", "Tissue")
            typ, mods = hd.sr.CodedConcept("85756007", "SCT", "Tissue"), None
        algo = hd.AlgorithmIdentificationSequence(name=(lab.source_model or "unknown")[:64], version="1",
                                                  family=codes.cid7162.ArtificialIntelligence)
        kw = {}
        col = _rgb_to_cielab(lab.rgb)
        if col is not None:
            kw["display_color"] = col
        descs.append(hd.seg.SegmentDescription(
            segment_number=n, segment_label=lab.display[:64] or lab.name[:64],
            segmented_property_category=cat,
            segmented_property_type=hd.seg.content.SegmentedPropertyType(typ, mods) if mods else typ,
            algorithm_type=hd.seg.SegmentAlgorithmTypeValues.AUTOMATIC, algorithm_identification=algo, **kw))
    seg = hd.seg.Segmentation(
        source_images=src, pixel_array=seg_arr, segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
        segment_descriptions=descs, series_instance_uid=generate_uid(), series_number=100,
        sop_instance_uid=generate_uid(), instance_number=1, manufacturer="emgmirror",
        manufacturer_model_name="ctmesh", software_versions=__version__, device_serial_number="0",
        content_description=DISCLAIMER[:64], content_label="SEGMENTATION", omit_empty_frames=True)
    seg.save_as(str(path))
    return {"file": Path(path).name, "segments": len(descs), "frames": int(seg.NumberOfFrames),
            "references": "synthetic geometry-only series" if synthetic else "original series"}
