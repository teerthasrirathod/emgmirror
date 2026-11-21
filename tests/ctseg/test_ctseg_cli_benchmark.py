import json
import subprocess
import sys

import numpy as np
import pytest
import SimpleITK as sitk

from emgmirror.ctseg import ontology
from emgmirror.ctseg.benchmark import pick_subjects, read_meta, score_subject
from emgmirror.ctseg.cli import main


def test_check_licences_cli(tmp_path, capsys):
    assert main(["check-licences"]) == 0
    assert main(["check-licences", "--models", "totalsegmentator:tissue_types"]) == 2
    bad = tmp_path / "cfg.json"
    bad.write_text(json.dumps({"extra_tasks": ["face"]}))
    assert main(["check-licences", "--config", str(bad)]) == 2
    w = tmp_path / "w" / "Dataset303_face_1559subj"
    w.mkdir(parents=True)
    assert main(["check-licences", "--weights-dir", str(tmp_path / "w")]) == 2
    assert "LICENCE CHECK FAILED" in capsys.readouterr().err


def test_module_entry_point_runs():
    p = subprocess.run([sys.executable, "-m", "emgmirror.ctseg", "labels"], capture_output=True, text=True, check=True)
    assert json.loads(p.stdout)[0]["name"] == "spleen"


def test_run_refuses_unreadable_input(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TOTALSEG_HOME_DIR", str(tmp_path / "h"))
    f = tmp_path / "x.nii.gz"
    f.write_bytes(b"junk")
    assert main(["run", "--input", str(f), "--out", str(tmp_path / "o")]) == 2
    assert "refused" in capsys.readouterr().err


def test_subject_selection_is_test_split_and_spread():
    meta = read_meta("﻿image_id;study_type;split\ns1;ct head;test\ns2;ct head;test\ns3;ct abdomen;test\n"
                     "s4;ct abdomen;train\ns5;;test\n")
    picked = [m["image_id"] for m in pick_subjects(meta, 3)]
    assert picked == ["s1", "s3", "s2"]
    assert all(m["split"] == "test" for m in pick_subjects(meta, 10))


def test_score_subject(tmp_path):
    a = np.zeros((10, 10, 10), np.uint16)
    a[2:6, 2:6, 2:6] = ontology.label("liver_segment_1").id
    a[6:8, 2:6, 2:6] = ontology.label("spleen").id
    img = sitk.GetImageFromArray(a)
    sitk.WriteImage(img, str(tmp_path / "labels.nii.gz"))
    seg = tmp_path / "seg"
    seg.mkdir()
    ref = np.zeros_like(a, np.uint8)
    ref[2:6, 2:6, 2:6] = 1
    sitk.WriteImage(sitk.GetImageFromArray(ref), str(seg / "liver.nii.gz"))
    sitk.WriteImage(sitk.GetImageFromArray(np.zeros_like(ref)), str(seg / "spleen.nii.gz"))
    lj = {"labels": [{"id": ontology.label("liver_segment_1").id, "flags": ["part_of:liver"]}]}
    rows = {r["structure"]: r for r in score_subject(tmp_path / "labels.nii.gz", lj, seg)}
    assert rows["liver"]["dice"] == pytest.approx(1.0) and rows["liver"]["msd_mm"] == pytest.approx(0.0, abs=1e-6)
    assert rows["spleen"]["dice"] == 0.0  # false positive
