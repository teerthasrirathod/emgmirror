#!/usr/bin/env python3
"""Render the same sample result with the existing single-file viewer, for comparison.

Uses src/emgmirror/ct/viewer.py unchanged. Output is a large self-contained HTML
file (kept out of git).

    PYTHONPATH=src python3 web/ct-viewer/tools/old_viewer_sample.py web/ct-viewer/public/sample web/ct-viewer/.scratch/old_viewer.html
"""
from __future__ import annotations

import json
import pathlib
import sys

import SimpleITK as sitk
import trimesh

from emgmirror.ct.viewer import write_viewer


def main() -> None:
    src = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    m = json.loads((src / "manifest.json").read_text())
    meshes = {}
    for s in m["structures"]:
        mesh = trimesh.load(src / s["files"]["stl"], force="mesh")
        meshes[s["id"]] = {
            "vertices": mesh.vertices,
            "faces": mesh.faces,
            "label": s["name"],
            "group": s.get("display", {}).get("system", "Structures").capitalize(),
            "color": "#%02x%02x%02x" % tuple(s["rgb"]),
            "opacity": 1.0,
        }
    ct = sitk.ReadImage(str(src / "ct.nii.gz"))
    labels = sitk.ReadImage(str(src / "labels.nii.gz"))
    write_viewer(out, meshes, ct, labels, {"title": "Sample CT (old viewer)"})
    print(out, out.stat().st_size // 1_000_000, "MB")


if __name__ == "__main__":
    main()
