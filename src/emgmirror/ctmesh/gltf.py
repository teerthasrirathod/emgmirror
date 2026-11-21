"""Minimal, dependency-light GLB 2.0 writer (and reader for tests).

Vertices are written in glTF axes and metres (``geometry.LPS_TO_GLTF`` applied to LPS mm), one node + mesh +
material per structure; structure metadata (id, name, display name, colour, SNOMED codes, volume, QC) goes in
``node.extras`` and the frame definition in ``asset.extras``.

``compress=True`` gives the web encoding, a pure-Python equivalent of
``gltf-transform quantize`` + ``gltf-transform meshopt``:

* ``KHR_mesh_quantization``: positions as uint16 with a per-node uniform dequantisation scale and translation
  (uniform, so normals are unaffected), normals as normalised int8;
* ``EXT_meshopt_compression``: every buffer view encoded with the meshoptimizer codecs (vertex codec v0,
  index codec v1, as the extension requires); the uncompressed buffer is a fallback-only buffer with no data.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from . import DISCLAIMER
from .geometry import AXES_NOTE, LPS_TO_GLTF, apply

FLOAT, UBYTE, BYTE, USHORT, UINT = 5126, 5121, 5120, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


@dataclass
class GltfNode:
    name: str
    mesh_lps: trimesh.Trimesh  # LPS mm
    rgb: tuple[int, int, int] = (200, 200, 200)
    extras: dict = field(default_factory=dict)
    opacity: float = 1.0


def srgb_to_linear(c8) -> list[float]:
    c = np.asarray(c8, float) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).tolist()


def _pad4(b: bytes, fill: bytes = b"\0") -> bytes:
    return b + fill * (-len(b) % 4)


def _vertex_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    T = V[F]
    fn = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])  # area weighted
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, F[:, k], fn)
    n = np.linalg.norm(vn, axis=1, keepdims=True)
    return vn / np.where(n > 0, n, 1)


def _optimise(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertex-cache then vertex-fetch order (meshoptimizer), which also helps the compressor."""
    import meshoptimizer as mo
    idx = np.ascontiguousarray(F.ravel(), np.uint32)
    dst = np.zeros_like(idx)
    mo.optimize_vertex_cache(dst, idx, vertex_count=len(V))
    Vo = np.zeros_like(V)
    n = mo.optimize_vertex_fetch(Vo, dst, np.ascontiguousarray(V), vertex_count=len(V),
                                 vertex_size=V.itemsize * V.shape[1])
    return Vo[:n], dst.reshape(-1, 3).astype(np.int64)


class _Builder:
    def __init__(self, compress: bool):
        self.compress = compress
        self.bin = bytearray()  # GLB BIN chunk (compressed data when compress)
        self.fallback_len = 0  # size of the virtual uncompressed buffer
        self.views, self.accessors = [], []

    def view(self, raw: bytes, count: int, stride: int, target: int, mode: str) -> int:
        if not self.compress:
            off = len(self.bin)
            self.bin += _pad4(raw)
            v = {"buffer": 0, "byteOffset": off, "byteLength": len(raw), "target": target}
            if target == ARRAY_BUFFER:
                v["byteStride"] = stride
        else:
            import meshoptimizer as mo
            arr = np.frombuffer(raw, np.uint8)
            if mode == "ATTRIBUTES":
                mo.encode_vertex_version(0)
                enc = mo.encode_vertex_buffer(arr.reshape(count, stride), count, stride)
            else:
                mo.encode_index_version(1)
                ix = np.frombuffer(raw, np.uint16 if stride == 2 else np.uint32).astype(np.uint32)
                enc = mo.encode_index_buffer(ix, len(ix), int(ix.max()) + 1 if len(ix) else 0)
            off = len(self.bin)
            self.bin += _pad4(enc)
            foff = self.fallback_len
            self.fallback_len += len(_pad4(raw))
            v = {"buffer": 1, "byteOffset": foff, "byteLength": len(raw), "target": target,
                 "extensions": {"EXT_meshopt_compression": {
                     "buffer": 0, "byteOffset": off, "byteLength": len(enc), "byteStride": stride,
                     "count": count, "mode": mode}}}
            if target == ARRAY_BUFFER:
                v["byteStride"] = stride
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, view: int, ctype: int, count: int, typ: str, normalized=False, mn=None, mx=None) -> int:
        a = {"bufferView": view, "componentType": ctype, "count": count, "type": typ}
        if normalized:
            a["normalized"] = True
        if mn is not None:
            a["min"], a["max"] = [float(x) if ctype == FLOAT else int(x) for x in mn], \
                                 [float(x) if ctype == FLOAT else int(x) for x in mx]
        self.accessors.append(a)
        return len(self.accessors) - 1


def write_glb(path: str | Path, nodes: list[GltfNode], asset_extras: dict | None = None,
              compress: bool = False, scene_name: str = "structures") -> Path:
    b = _Builder(compress)
    gnodes, meshes, materials = [], [], []
    for nd in nodes:
        m = nd.mesh_lps
        V = apply(LPS_TO_GLTF, np.asarray(m.vertices, float))
        F = np.asarray(m.faces, np.int64)
        if not len(F):
            continue
        Vf = V.astype(np.float32)
        if compress:
            Vf, F = _optimise(Vf, F)
        N = _vertex_normals(Vf.astype(float), F)
        node = {"name": nd.name, "mesh": len(meshes), "extras": nd.extras}
        if compress:
            lo = Vf.min(0).astype(float)
            ext = float((Vf.max(0) - lo).max()) or 1e-6
            s = ext / 65535.0
            q = np.clip(np.rint((Vf - lo) / s), 0, 65535).astype(np.uint16)
            qp = np.zeros((len(q), 4), np.uint16)
            qp[:, :3] = q
            pv = b.view(qp.tobytes(), len(q), 8, ARRAY_BUFFER, "ATTRIBUTES")
            pa = b.accessor(pv, USHORT, len(q), "VEC3", mn=q.min(0), mx=q.max(0))
            nq = np.zeros((len(N), 4), np.int8)
            nq[:, :3] = np.clip(np.rint(N * 127), -127, 127)
            nv = b.view(nq.tobytes(), len(N), 4, ARRAY_BUFFER, "ATTRIBUTES")
            na = b.accessor(nv, BYTE, len(N), "VEC3", normalized=True)
            node["translation"] = lo.tolist()
            node["scale"] = [s, s, s]
        else:
            pv = b.view(Vf.tobytes(), len(Vf), 12, ARRAY_BUFFER, "ATTRIBUTES")
            pa = b.accessor(pv, FLOAT, len(Vf), "VEC3", mn=Vf.min(0), mx=Vf.max(0))
            nv = b.view(N.astype(np.float32).tobytes(), len(N), 12, ARRAY_BUFFER, "ATTRIBUTES")
            na = b.accessor(nv, FLOAT, len(N), "VEC3")
        big = len(Vf) > 65535
        ib = F.astype(np.uint32 if big else np.uint16).tobytes()
        iv = b.view(ib, F.size, 4 if big else 2, ELEMENT_ARRAY_BUFFER, "TRIANGLES")
        ia = b.accessor(iv, UINT if big else USHORT, F.size, "SCALAR")
        mat = {"name": nd.name, "pbrMetallicRoughness": {
            "baseColorFactor": srgb_to_linear(nd.rgb) + [float(nd.opacity)], "metallicFactor": 0.0,
            "roughnessFactor": 0.6}, "doubleSided": False}
        if nd.opacity < 1:
            mat["alphaMode"] = "BLEND"
        materials.append(mat)
        meshes.append({"name": nd.name, "primitives": [{"attributes": {"POSITION": pa, "NORMAL": na},
                                                        "indices": ia, "material": len(materials) - 1}]})
        gnodes.append(node)
    extras = {"disclaimer": DISCLAIMER, "units": "m", "axes": AXES_NOTE, "lps_to_gltf": LPS_TO_GLTF.tolist()}
    extras.update(asset_extras or {})
    doc = {"asset": {"version": "2.0", "generator": "emgmirror.ctmesh", "copyright": DISCLAIMER, "extras": extras},
           "scene": 0, "scenes": [{"name": scene_name, "nodes": list(range(len(gnodes)))}],
           "nodes": gnodes, "meshes": meshes, "materials": materials,
           "accessors": b.accessors, "bufferViews": b.views,
           "buffers": [{"byteLength": len(b.bin)}]}
    if compress:
        doc["buffers"].append({"byteLength": b.fallback_len, "extensions": {"EXT_meshopt_compression": {"fallback": True}}})
        doc["extensionsUsed"] = doc["extensionsRequired"] = ["EXT_meshopt_compression", "KHR_mesh_quantization"]
    js = _pad4(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
    binb = _pad4(bytes(b.bin))
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binb))
    out += struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binb), b"BIN\0") + binb
    path = Path(path)
    path.write_bytes(out)
    return path


def read_glb(path: str | Path) -> tuple[dict, list[dict]]:
    """Parse a GLB written by :func:`write_glb` (both encodings). Returns (json, [{"name", "extras",
    "positions_gltf" (world, metres, node transform applied), "normals", "faces"}])."""
    data = Path(path).read_bytes()
    magic, ver, total = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and ver == 2 and total == len(data)
    jl = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + jl])
    bl = struct.unpack_from("<I", data, 20 + jl)[0]
    binb = data[28 + jl:28 + jl + bl]
    ncomp = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}
    dt = {FLOAT: np.float32, USHORT: np.uint16, UINT: np.uint32, BYTE: np.int8, UBYTE: np.uint8}

    def acc(i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        d = np.dtype(dt[a["componentType"]])
        nc = ncomp[a["type"]]
        ext = v.get("extensions", {}).get("EXT_meshopt_compression")
        if ext:
            import meshoptimizer as mo
            raw = binb[ext["byteOffset"]:ext["byteOffset"] + ext["byteLength"]]
            if ext["mode"] == "ATTRIBUTES":
                # default decode path (the wrapper's dtype= path under-allocates); returns the raw bytes
                buf = np.ascontiguousarray(mo.decode_vertex_buffer(ext["count"], ext["byteStride"], raw)).tobytes()
            else:
                # the stream does not depend on index width; the wrapper always returns uint32, so decode at 4
                arr = mo.decode_index_buffer(ext["count"], 4, raw)
                buf = np.asarray(arr).astype(np.uint16 if ext["byteStride"] == 2 else np.uint32).tobytes()
        else:
            buf = binb[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        stride = v.get("byteStride", d.itemsize * nc)
        n = a["count"]
        rows = np.frombuffer(buf, np.uint8)[:n * stride].reshape(n, stride)[:, :d.itemsize * nc]
        x = np.ascontiguousarray(rows).view(d).reshape(n, nc).astype(float)
        if a.get("normalized"):
            x = np.maximum(x / {BYTE: 127.0, UBYTE: 255.0, USHORT: 65535.0}[a["componentType"]], -1.0)
        return x

    out = []
    for nd in doc["nodes"]:
        pr = doc["meshes"][nd["mesh"]]["primitives"][0]
        P = acc(pr["attributes"]["POSITION"])
        s = np.asarray(nd.get("scale", [1, 1, 1]), float)
        t = np.asarray(nd.get("translation", [0, 0, 0]), float)
        out.append({"name": nd.get("name"), "extras": nd.get("extras", {}), "positions_gltf": P * s + t,
                    "normals": acc(pr["attributes"]["NORMAL"]), "faces": acc(pr["indices"]).astype(np.int64).reshape(-1, 3)})
    return doc, out
