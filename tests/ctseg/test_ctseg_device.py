"""Device build: model allowlist, pinned weights, intended-use output (docs/ct_platform/REGULATORY_PATH.md)."""

import json

import numpy as np
import pytest
import SimpleITK as sitk

from emgmirror.ctseg import allowlist as A
from emgmirror.ctseg import device as D
from emgmirror.ctseg import ontology
from emgmirror.ctseg.config import SegConfig
from emgmirror.ctseg.engine import segment

from ctseg_helpers import FakeRunner


class DeviceFake(FakeRunner):
    def versions(self):
        return {"TotalSegmentator": "2.18.0"}


def make_weights(root, ids=(297,), content=b"weights-v1"):
    for i in ids:
        f = root / f"Dataset{i}_TotalSegmentator_x" / "nnUNetTrainer__nnUNetPlans__3d_fullres" / "fold_0"
        f.mkdir(parents=True, exist_ok=True)
        (f / "checkpoint_final.pth").write_bytes(content)
        (f.parent / "plans.json").write_text("{}")
    return root


@pytest.fixture
def device_env(tmp_path, ts_home):
    w = make_weights(tmp_path / "weights")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(D.build_manifest(w, [297], "2.18.0")))

    def cfg(**kw):
        return SegConfig(build="device", fast=True, weights_dir=str(w), weights_manifest=str(manifest),
                         totalseg_home=str(ts_home), **kw)
    return w, manifest, cfg


# ---------------------------------------------------------------- allowlist

def test_device_build_allows_total_only():
    assert A.allowed("totalsegmentator:total", "device").licence == "Apache-2.0"
    for key in ["totalsegmentator:body", "totalsegmentator:lung_vessels", "moose:clin_ct_peripheral_bones",
                "moose:clin_ct_body_composition", "cads:open", "nvidia:nv-segment-ct", "totalsegmentator:total_mr"]:
        with pytest.raises(A.LicenceError, match="device build"):
            A.allowed(key, "device")
        if key != "totalsegmentator:total_mr":
            A.allowed(key, "research")  # still fine in the research build


def test_device_config_defaults_and_explicit_research_models_fail():
    cfg = SegConfig(build="device")
    assert cfg.profile == "minimal" and cfg.moose is False
    A.check_config(cfg.licence_view())
    for bad in (dict(moose=True), dict(profile="standard"), dict(extra_tasks=["lung_vessels"]),
                dict(cads={"license": "open"})):
        with pytest.raises(A.LicenceError):
            A.check_config(SegConfig(build="device", **bad).licence_view())


def test_device_weights_folder_may_hold_only_total(tmp_path):
    make_weights(tmp_path, ids=(291, 297))
    assert len(A.check_weights_dir(tmp_path, "device")) == 2
    (tmp_path / "Dataset300_body_6mm_1559subj").mkdir()
    A.check_weights_dir(tmp_path, "research")
    with pytest.raises(A.LicenceError, match="device build"):
        A.check_weights_dir(tmp_path, "device")


def test_shipped_manifest_pins_every_total_dataset():
    m = D.load_manifest()
    assert m["totalsegmentator_version"] == "2.18.0"
    assert set(m["datasets"]) == {"291", "292", "293", "294", "295", "297"}
    for d in m["datasets"].values():
        assert any(f.endswith("checkpoint_final.pth") for f in d["files"])
        assert all(len(h) == 64 for h in d["files"].values())
        assert d["tree_sha256"] == D.tree_hash(d["files"])


# ---------------------------------------------------------------- pinned weights

def test_verify_passes_and_fingerprint_is_stable(tmp_path):
    w = make_weights(tmp_path)
    m = D.build_manifest(w, [297], "2.18.0")
    fp = D.verify(w, [297], "2.18.0", m)
    assert fp["sha256"] == D.fingerprint(w, [297], "2.18.0")["sha256"]


@pytest.mark.parametrize("tamper", ["changed", "extra", "missing", "version", "unpinned"])
def test_verify_detects_model_changes(tmp_path, tamper):
    w = make_weights(tmp_path)
    m = D.build_manifest(w, [297], "2.18.0")
    ckpt = next(w.rglob("checkpoint_final.pth"))
    version, ids = "2.18.0", [297]
    if tamper == "changed":
        ckpt.write_bytes(b"weights-v2")
    elif tamper == "extra":
        (ckpt.parent / "checkpoint_best.pth").write_bytes(b"x")
    elif tamper == "missing":
        ckpt.unlink()
    elif tamper == "version":
        version = "2.19.0"
    else:
        ids = [291]
    with pytest.raises(D.ModelIntegrityError):
        D.verify(w, ids, version, m)


def test_intended_use_table_maps_to_total_labels():
    total = set(ontology.classes("totalsegmentator:total").values())
    names = D.device_structures()
    assert len(names) == len(set(names)) == 36  # 3 pelvis + 25 spine + 4 shoulder + 2 femur + 2 humerus
    assert set(names) <= total
    assert {"hip_left", "sacrum", "vertebrae_S1", "scapula_right", "clavicula_left", "femur_right",
            "humerus_left"} <= set(names)


# ---------------------------------------------------------------- engine

def test_device_run_outputs_only_intended_use_structures(phantom_file, phantom, tmp_path, device_env):
    _, _, cfg = device_env
    r = DeviceFake(phantom[1])
    res = segment(phantom_file, tmp_path / "out", cfg(), runner=r)
    assert r.calls == ["totalsegmentator:total"]  # no body, no subtasks, no MOOSE
    names = {e["name"] for e in res.labels_json["labels"]}
    assert names == {"femur_left"}
    lab = sitk.GetArrayFromImage(sitk.ReadImage(str(res.labels_path)))
    assert set(np.unique(lab).tolist()) == {0, ontology.label("femur_left").id}
    dev = res.labels_json["qc"]["device"]
    assert dev["approval"]["status"] == "not_approved"
    assert set(dev["removed_non_intended_use"]) == {"liver", "lung_upper_lobe_left", "lung_upper_lobe_right"}
    assert dev["structures"]["proximal_femur"] == ["femur_left"]
    prov = res.provenance
    assert prov["build"] == "device" and prov["model_fingerprint"]["verified_against_manifest"] is True
    assert prov["privacy"]["totalsegmentator_send_usage_stats"] is False
    assert [m["source"] for m in prov["models"]] == ["totalsegmentator:total"]


def test_device_run_refuses_unpinned_weights_before_running(phantom_file, phantom, tmp_path, device_env):
    w, _, cfg = device_env
    next(w.rglob("checkpoint_final.pth")).write_bytes(b"silently swapped")
    r = DeviceFake(phantom[1])
    with pytest.raises(D.ModelIntegrityError):
        segment(phantom_file, tmp_path / "out", cfg(), runner=r)
    assert r.calls == []
    assert not (tmp_path / "out" / "labels.nii.gz").exists()


def test_device_run_refuses_mr(tmp_path, phantom, device_env):
    _, _, cfg = device_env
    mr = sitk.Cast(phantom[0], sitk.sitkFloat32) + 1000.0
    p = tmp_path / "mr.nii.gz"
    sitk.WriteImage(mr, str(p))
    from emgmirror.ctseg.inputs import InputRefused
    with pytest.raises(InputRefused, match="CT only"):
        segment(p, tmp_path / "o", cfg(), runner=DeviceFake(phantom[1]))


def test_cli_device_licence_check(tmp_path):
    from emgmirror.ctseg.cli import main
    assert main(["check-licences", "--build", "device"]) == 0
    assert main(["check-licences", "--build", "device", "--models", "moose:clin_ct_peripheral_bones"]) == 2
