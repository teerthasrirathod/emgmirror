"""Provenance and determinism helpers: dependency versions, input / parameter / output hashes.

Same input + same software versions + same parameters must give byte-identical ``manifest.json`` and output
files; run-specific data (timings, worker count) goes to ``run_log.json`` only.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from importlib import metadata
from pathlib import Path

import numpy as np
import trimesh

from . import __version__

DEPENDENCIES = ("numpy", "scipy", "vtk", "trimesh", "manifold3d", "meshoptimizer", "highdicom", "pydicom",
                "SimpleITK")


def software_versions() -> dict:
    out = {"emgmirror.ctmesh": __version__, "python": platform.python_version(),
           "platform": f"{sys.platform}-{platform.machine()}"}
    for d in DEPENDENCIES:
        try:
            out[d] = metadata.version(d)
        except metadata.PackageNotFoundError:  # pragma: no cover
            out[d] = None
    return out


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mesh_sha256(m: trimesh.Trimesh) -> str:
    """Hash of the geometry itself (float64 LPS vertices + int64 faces, little endian), independent of file
    format."""
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(m.vertices, "<f8").tobytes())
    h.update(np.ascontiguousarray(m.faces, "<i8").tobytes())
    return h.hexdigest()


def json_sha256(obj) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode())
