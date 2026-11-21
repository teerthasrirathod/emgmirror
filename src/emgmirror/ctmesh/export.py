"""Print and clinical-tool exports: STL and 3MF per structure (LPS mm), DICOM-SEG for the whole label map.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
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
    Path(path).write_bytes(_deterministic_3mf(trimesh.exchange.threemf.export_3MF(scene)))
    return Path(path)


def _deterministic_3mf(data: bytes) -> bytes:
    """trimesh writes a random UUID per object; replace them with UUIDs derived from the content so the same
    meshes always give the same bytes, and re-zip with fixed member order and timestamps."""
    import io
    import re as _re
    import uuid
    import zipfile
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in sorted(zin.namelist()):
            b = zin.read(name)
            if name.endswith(".model"):
                txt = b.decode("utf-8")
                pat = _re.compile(r'UUID="([0-9a-fA-F-]{36})"')
                seed = hashlib.sha256(pat.sub("", txt).encode()).hexdigest()
                order: dict[str, int] = {}

                def _sub(mt):
                    k = order.setdefault(mt.group(1), len(order))
                    return f'UUID="{uuid.uuid5(uuid.NAMESPACE_OID, f"{seed}/{k}")}"'
                txt = pat.sub(_sub, txt)
                if "<metadata" not in txt:  # 3MF core: metadata elements come first inside <model>
                    txt = _re.sub(r"(<model[^>]*>)", lambda mt: mt.group(1) + '<metadata name="Description">'
                                  + STL_NOTE + "</metadata>", txt, count=1)
                b = txt.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(info, b)
    return out.getvalue()


def _rgb_to_cielab(rgb):
    import highdicom as hd
    try:
        return hd.color.CIELabColor.from_rgb(*[int(c) for c in rgb])
    except Exception:  # pragma: no cover - older highdicom
        return None


def fixed_datetime() -> _dt.datetime:
    """Timestamp written into DICOM: ``SOURCE_DATE_EPOCH`` (reproducible-builds convention) if set, else
    2000-01-01 00:00 UTC, so the same input always gives the same bytes."""
    e = os.environ.get("SOURCE_DATE_EPOCH")
    return _dt.datetime.fromtimestamp(int(e), _dt.timezone.utc) if e else _dt.datetime(2000, 1, 1,
                                                                                         tzinfo=_dt.timezone.utc)


def _uid(seed: str, what: str) -> str:
    from pydicom.uid import generate_uid
    return generate_uid(entropy_srcs=[seed, what])


def _reference_series(lm: LabelMap, seed: str = ""):
    """Minimal CT headers on the label map's grid, used as the SEG's source images when no original DICOM
    series is given. They carry only geometry and fresh UIDs (no pixel data, no patient identity)."""
    from pydicom.dataset import Dataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian
    nz, ny, nx = lm.array.shape
    D = np.asarray(lm.direction, float)
    sx, sy, sz = lm.spacing
    study, series, frame = _uid(seed, "study"), _uid(seed, "series"), _uid(seed, "frame")
    today = fixed_datetime().strftime("%Y%m%d")
    out = []
    for k in range(nz):
        ds = Dataset()
        ds.file_meta = Dataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.SOPClassUID, ds.SOPInstanceUID = CTImageStorage, _uid(seed, f"ct/{k}")
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


def write_dicom_seg(path: str | Path, lm: LabelMap, label_ids: list[int], source_images=None,
                    seed: str = "") -> dict:
    """DICOM-SEG (BINARY, one segment per structure, empty frames omitted) via highdicom. ``source_images``:
    the original CT series as pydicom datasets; without it a geometry-only reference series is synthesised
    and the result says so."""
    import highdicom as hd
    from pydicom.sr.codedict import codes
    synthetic = source_images is None
    if not seed:
        seed = hashlib.sha256(np.ascontiguousarray(lm.array).tobytes()).hexdigest()
    src = _reference_series(lm, seed) if synthetic else list(source_images)
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
        segment_descriptions=descs, series_instance_uid=_uid(seed, "seg-series"), series_number=100,
        sop_instance_uid=_uid(seed, "seg-instance"), instance_number=1, manufacturer="emgmirror",
        manufacturer_model_name="ctmesh", software_versions=__version__, device_serial_number="0",
        content_description=DISCLAIMER[:64], content_label="SEGMENTATION", omit_empty_frames=True)
    t = fixed_datetime()
    for kw in ("ContentDate", "SeriesDate", "InstanceCreationDate", "StudyDate"):
        if kw in seg:
            setattr(seg, kw, t.strftime("%Y%m%d"))
    for kw in ("ContentTime", "SeriesTime", "InstanceCreationTime", "StudyTime"):
        if kw in seg:
            setattr(seg, kw, t.strftime("%H%M%S"))
    for item in seg.get("ContributingEquipmentSequence", []):
        if "ContributionDateTime" in item:
            item.ContributionDateTime = t.strftime("%Y%m%d%H%M%S")
    dim_uid = _uid(seed, "dimension-organization")
    for sq in ("DimensionOrganizationSequence", "DimensionIndexSequence"):
        for item in seg.get(sq, []):
            if "DimensionOrganizationUID" in item:
                item.DimensionOrganizationUID = dim_uid
    seg.save_as(str(path))
    return {"file": Path(path).name, "segments": len(descs), "frames": int(seg.NumberOfFrames),
            "references": "synthetic geometry-only series" if synthetic else "original series"}
