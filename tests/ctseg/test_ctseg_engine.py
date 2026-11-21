import json

import numpy as np
import pytest
import SimpleITK as sitk

from emgmirror.ctseg import DISCLAIMER, ontology
from emgmirror.ctseg.allowlist import LicenceError
from emgmirror.ctseg.config import SegConfig
from emgmirror.ctseg.engine import segment
from emgmirror.ctseg.inputs import InputRefused

from ctseg_helpers import FakeRunner, make_phantom

LABEL_KEYS = {"id", "name", "display", "rgb", "snomed", "source_model", "flags"}


def cfg(**kw):
    return SegConfig(**kw)


@pytest.fixture
def run(tmp_path, phantom_file, phantom, ts_home):
    def _run(c=None, runner=None, **kw):
        r = runner or FakeRunner(phantom[1])
        out = tmp_path / "out"
        res = segment(phantom_file, out, c or cfg(totalseg_home=str(ts_home)), runner=r, **kw)
        return res, r, out
    return _run


def test_outputs_follow_the_shared_contract(run, phantom):
    res, runner, out = run()
    assert {p.name for p in out.iterdir()} == {"labels.nii.gz", "labels.json", "provenance.json"}
    lab = sitk.ReadImage(str(out / "labels.nii.gz"))
    img = phantom[0]
    assert lab.GetPixelID() == sitk.sitkUInt16 and lab.GetSize() == img.GetSize()
    assert np.allclose(lab.GetSpacing(), img.GetSpacing()) and np.allclose(lab.GetOrigin(), img.GetOrigin())
    assert np.allclose(lab.GetDirection(), img.GetDirection())
    lj = json.loads((out / "labels.json").read_text())
    assert lj["version"] == 1 and set(lj) == {"version", "labels", "qc"}
    present = set(np.unique(sitk.GetArrayFromImage(lab)).tolist()) - {0}
    assert {e["id"] for e in lj["labels"]} == present
    for e in lj["labels"]:
        assert set(e) == LABEL_KEYS
        assert e["name"] == ontology.by_id()[e["id"]].name and len(e["rgb"]) == 3
        assert e["snomed"] is None or {"category", "type"} <= set(e["snomed"])
        assert isinstance(e["flags"], list) and e["source_model"].startswith("totalsegmentator:")
    assert lj["qc"]["disclaimer"] == DISCLAIMER and lj["qc"]["image"] == "input"


def test_pipeline_order_and_region_selection(run):
    res, runner, out = run()
    # base, body, then thorax + abdomen subtasks; femur in view -> MOOSE peripheral bones; no lumbar spine
    assert runner.calls[:2] == ["totalsegmentator:total", "totalsegmentator:body"]
    assert set(runner.calls[2:]) == {"totalsegmentator:lung_vessels", "totalsegmentator:trunk_cavities",
                                     "totalsegmentator:liver_segments", "moose:clin_ct_peripheral_bones"}
    assert {"thorax", "abdomen", "lower_limb"} <= set(res.labels_json["qc"]["regions"])


def test_fusion_and_qc_flags_end_to_end(run):
    res, _, _ = run()
    by = {e["name"]: e for e in res.labels_json["labels"]}
    # the fake segments cover only part of the liver and were predicted larger than it: they replace liver
    # voxels only, never the fat around it
    assert "liver" in by and "part_of:liver" in by["liver_segment_1"]["flags"]
    st0 = res.labels_json["qc"]["structures"]
    assert 40 <= st0["liver_segment_1"]["mean_hu"] <= 80 and 40 <= st0["liver_segment_2"]["mean_hu"] <= 80
    assert "cut_by_scan_edge" in by["femur_left"]["flags"]
    assert "cut_by_scan_edge" in by["body_trunc"]["flags"] or "body_trunc" in by
    st = res.labels_json["qc"]["structures"]
    assert st["femur_left"]["touches_edge"] == ["z_min"]
    assert 800 < st["femur_left"]["mean_hu"] < 1000


def test_provenance_records_models_licences_and_privacy(run):
    res, _, out = run()
    p = json.loads((out / "provenance.json").read_text())
    assert p["disclaimer"] == DISCLAIMER
    assert p["privacy"]["totalsegmentator_send_usage_stats"] is False
    assert all(m["status"] == "ok" and m["licence"] in ("Apache-2.0", "CC-BY-4.0") for m in p["models"])
    assert p["settings"]["profile"] == "standard" and p["software"]["fake"] == "1"
    assert len(p["input"]["sha256"]) == 64 and "ct.nii.gz" not in json.dumps(p)  # no file names
    assert set(p["outputs"]) == {"labels.nii.gz", "labels.json"}


def test_usage_stats_are_switched_off_in_totalsegmentator_config(run, ts_home):
    (ts_home).mkdir(parents=True, exist_ok=True)
    (ts_home / "config.json").write_text(json.dumps({"totalseg_id": "x", "send_usage_stats": True,
                                                      "license_number": "aca_1"}))
    run()
    c = json.loads((ts_home / "config.json").read_text())
    assert c["send_usage_stats"] is False and "license_number" not in c


def test_failed_optional_model_gives_partial_result(run, phantom):
    res, _, out = run(runner=FakeRunner(phantom[1], fail={"totalsegmentator:liver_segments"}))
    names = {e["name"] for e in res.labels_json["labels"]}
    assert "liver" in names and "liver_segment_1" not in names
    assert res.labels_json["qc"]["failed_models"] == ["totalsegmentator:liver_segments"]
    p = json.loads((out / "provenance.json").read_text())
    assert any(m["status"] == "failed" and "simulated" in m["error"] for m in p["models"])


def test_failed_base_model_fails_the_job(run, phantom):
    with pytest.raises(RuntimeError):
        run(runner=FakeRunner(phantom[1], fail={"totalsegmentator:total"}))


def test_licence_problem_stops_before_any_model_runs(run, phantom, ts_home):
    r = FakeRunner(phantom[1])
    with pytest.raises(LicenceError):
        run(cfg(extra_tasks=["appendicular_bones"], totalseg_home=str(ts_home)), runner=r)
    assert r.calls == []


def test_minimal_profile(run, ts_home):
    res, runner, _ = run(cfg(profile="minimal", totalseg_home=str(ts_home)))
    assert runner.calls == ["totalsegmentator:total", "totalsegmentator:body"]


def test_mr_is_routed_to_total_mr(tmp_path, ts_home):
    img, truth = make_phantom()
    mr = sitk.Cast(img, sitk.sitkFloat32) + 1000.0  # non-negative intensities, no air at -1000
    p = tmp_path / "mr.nii.gz"
    sitk.WriteImage(mr, str(p))
    r = FakeRunner(truth)
    res = segment(p, tmp_path / "o", cfg(totalseg_home=str(ts_home)), runner=r)
    assert r.calls == ["totalsegmentator:total_mr", "totalsegmentator:body_mr"]
    assert res.labels_json["qc"]["scan"]["modality"] == "MR"
    assert all("mr_no_hu_check" in e["flags"] for e in res.labels_json["labels"])


def test_resampled_input_writes_the_matching_image(tmp_path, ts_home):
    img, _ = make_phantom()
    p = tmp_path / "ct.nii"
    sitk.WriteImage(img, str(p))
    small_truth = {}

    class Rescaled(FakeRunner):
        def run(self, source, image_path, image, workdir):
            if not small_truth:  # build truth on the resampled grid from thresholds
                a = sitk.GetArrayFromImage(image)
                small_truth.update(liver=(a > 30) & (a < 100), body_trunc=a > -500, femur_left=a > 500,
                                   lung_upper_lobe_left=np.zeros(a.shape, bool),
                                   lung_upper_lobe_right=np.zeros(a.shape, bool))
                self.truth = small_truth
            return super().run(source, image_path, image, workdir)

    res = segment(p, tmp_path / "o", cfg(max_voxels=30_000, totalseg_home=str(ts_home)), runner=Rescaled({}))
    out = tmp_path / "o"
    assert res.labels_json["qc"]["image"] == "image.nii.gz"
    a, b = sitk.ReadImage(str(out / "labels.nii.gz")), sitk.ReadImage(str(out / "image.nii.gz"))
    assert a.GetSize() == b.GetSize() and np.allclose(a.GetSpacing(), b.GetSpacing())
    assert "resampled_input" in res.labels_json["qc"]["scan"]["flags"]


def test_thick_slices_refused(tmp_path, ts_home):
    img, truth = make_phantom()
    img.SetSpacing((1.0, 1.0, 7.0))
    p = tmp_path / "thick.nii.gz"
    sitk.WriteImage(img, str(p))
    with pytest.raises(InputRefused):
        segment(p, tmp_path / "o", cfg(totalseg_home=str(ts_home)), runner=FakeRunner(truth))


def test_no_intermediate_files_left(run):
    _, _, out = run()
    assert not [p for p in out.iterdir() if p.name.startswith(".ctseg_work")]
