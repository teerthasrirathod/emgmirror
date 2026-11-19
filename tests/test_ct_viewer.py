"""Tests for the single-file offline CT viewer (synthetic phantom only)."""

from __future__ import annotations

import base64
import gzip
import json
import re
import shutil
import subprocess

import numpy as np
import pytest
import SimpleITK as sitk
from skimage.measure import marching_cubes

from emgmirror.ct import DISCLAIMER
from emgmirror.ct import labels as L
from emgmirror.ct.phantom import make_phantom
from emgmirror.ct.viewer import _TEMPLATE, write_viewer


@pytest.fixture(scope="module")
def phantom():
    return make_phantom()


def _mesh(truth: sitk.Image, mask_fn, **extra) -> dict:
    """Marching cubes on a padded boolean mask, vertices in patient LPS mm (identity direction)."""
    mask = np.pad(mask_fn(sitk.GetArrayFromImage(truth)), 1).astype(np.float32)
    sx, sy, sz = truth.GetSpacing()
    v, f, _, _ = marching_cubes(mask, 0.5, spacing=(sz, sy, sx))
    ox, oy, oz = truth.GetOrigin()
    xyz = v[:, ::-1] + np.array([ox - sx, oy - sy, oz - sz])
    return {"vertices": xyz, "faces": f, **extra}


@pytest.fixture(scope="module")
def meshes(phantom):
    t = phantom.truth
    return {
        "radius": _mesh(t, lambda a: (a == L.cortical(0)) | (a == L.medullary(0)), color="#efe9dc", label="Radius (test)",
                        group="Bones", info={"Length": "60 mm"}, explain="A bone."),
        "muscle": _mesh(t, lambda a: a == L.MUSCLE, color="#b5524c", label="Muscle (test)", group="Soft tissue", opacity=0.4),
        "plate": _mesh(t, lambda a: a == L.METAL, color="#4aa3df", label="Plate (test)", group="Hardware"),
    }


@pytest.fixture(scope="module")
def full(tmp_path_factory, phantom, meshes):
    return write_viewer(tmp_path_factory.mktemp("full") / "v.html", meshes, phantom.image, phantom.truth, META)


def _payload(path) -> dict:
    m = re.search(r'<script id="payload" type="application/json">(.*?)</script>', path.read_text(encoding="utf-8"), re.S)
    assert m
    return json.loads(m.group(1))


def _raw(b64: str, dtype: str) -> np.ndarray:
    return np.frombuffer(gzip.decompress(base64.b64decode(b64)), dtype=dtype)


META = {"title": "Phantom forearm", "subtitle": "Synthetic", "summary": ["A made-up arm."], "notes": ["Not real."],
        "voxel_mm": [0.9, 0.9, 1.5]}


def test_file_text(full, meshes):
    out = full
    assert out.exists() and [p.name for p in out.parent.iterdir()] == ["v.html"]
    text = out.read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text
    assert DISCLAIMER in text
    for m in meshes.values():
        assert m["label"] in text
    print(f"\nphantom viewer size: {out.stat().st_size / 1024:.0f} KiB")
    assert out.stat().st_size < 4_000_000


def test_payload_roundtrip(full, phantom, meshes):
    p = _payload(full)
    assert [e["name"] for e in p["meshes"]] == list(meshes)
    for e in p["meshes"]:
        src = meshes[e["name"]]
        q = _raw(e["verts"], "<u2").reshape(-1, 3)
        lo, hi = np.array(e["bbox"][:3]), np.array(e["bbox"][3:])
        v = lo + q / 65535.0 * (hi - lo)
        step = (hi - lo) / 65535.0
        assert v.shape == src["vertices"].shape and e["nv"] == len(v)
        assert np.all(np.abs(v - src["vertices"]) <= step / 2 + 1e-9)
        assert np.abs(v - src["vertices"]).max() < 0.01  # mm, for a ~100 mm part
        assert np.array_equal(_raw(e["faces"], "<u4").reshape(-1, 3), src["faces"])
        assert e["color"] == src["color"] and e["group"] == src["group"]
    assert p["meshes"][1]["opacity"] == pytest.approx(0.4) and p["meshes"][0]["opacity"] == 1.0
    assert p["meshes"][0]["info"] == {"Length": "60 mm"} and p["meshes"][0]["explain"] == "A bone."
    nx, ny, nz = phantom.image.GetSize()
    vol = _raw(p["volume"]["data"], "<i2").reshape(nz, ny, nx)
    assert np.array_equal(vol, sitk.GetArrayFromImage(phantom.image))
    lab = _raw(p["labels"], "u1").reshape(nz, ny, nx)
    assert np.array_equal(lab, sitk.GetArrayFromImage(phantom.truth))
    assert p["meta"]["voxel_mm"] == [0.9, 0.9, 1.5]
    assert p["meta"]["label_names"]["2"] == "muscle" and p["meta"]["label_colors"]["2"] == L.COLORS["muscle"]


def test_geometry_with_oblique_direction(tmp_path, phantom):
    img = sitk.Image(phantom.image)
    img.SetDirection((0, 1, 0, -1, 0, 0, 0, 0, 1))
    img.SetOrigin((5.5, -7.25, 3.0))
    img.SetSpacing((0.7, 0.9, 1.5))
    out = write_viewer(tmp_path / "v.html", {}, img, None, None)
    g = _payload(out)["volume"]
    assert g["size"] == list(img.GetSize()) and g["spacing"] == list(img.GetSpacing())
    assert g["origin"] == list(img.GetOrigin()) and g["direction"] == list(img.GetDirection())
    assert np.array_equal(_raw(g["data"], "<i2").reshape(img.GetSize()[::-1]), sitk.GetArrayFromImage(img))


def test_volume_none_and_empty_meshes(tmp_path, phantom, meshes):
    a = write_viewer(tmp_path / "a.html", meshes, None, None, None)
    pa = _payload(a)
    assert pa["volume"] is None and pa["labels"] is None and len(pa["meshes"]) == 3
    b = write_viewer(tmp_path / "b.html", {}, phantom.image, None, {"title": "Slices only"})
    pb = _payload(b)
    assert pb["meshes"] == [] and pb["volume"] is not None and pb["labels"] is None
    for f in (a, b):
        t = f.read_text(encoding="utf-8")
        assert DISCLAIMER in t and "http://" not in t and "https://" not in t


def test_rejects_labels_without_volume_and_bad_faces(tmp_path, phantom):
    with pytest.raises(ValueError):
        write_viewer(tmp_path / "x.html", {}, None, phantom.truth)
    bad = {"s": {"vertices": np.zeros((3, 3)), "faces": np.array([[0, 1, 5]])}}
    with pytest.raises(ValueError):
        write_viewer(tmp_path / "y.html", bad)


def test_markup_in_text_is_escaped(tmp_path):
    out = write_viewer(tmp_path / "e.html", {}, None, None, {"title": "A </script><b>x</b>", "summary": ["</script>"]})
    text = out.read_text(encoding="utf-8")
    assert text.count("</script>") == 2  # payload + code only
    assert _payload(out)["meta"]["summary"] == ["</script>"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_script_syntax(tmp_path):
    html = _TEMPLATE.read_text(encoding="utf-8")
    code = re.findall(r"<script>(.*?)</script>", html, re.S)[0]
    js = tmp_path / "viewer.js"
    js.write_text(code, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
