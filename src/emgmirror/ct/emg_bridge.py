"""Put the EMG electrodes on the CT forearm and replay a session on it.

What this does, and what it does not:

- Builds a forearm frame from the CT bones: elbow = proximal ends of radius + ulna, wrist = their
  distal ends, axis d = principal axis of radius + ulna (pointing to the hand).
- Palm (volar) side: for a LEFT forearm in supination, volar = d x (ulna -> radius). This follows
  from the anatomical frame (lateral, anterior, superior) and does not depend on how the whole arm
  was rotated in the scanner. It assumes the forearm was supinated (radius and ulna side by side);
  pronation rotates it. Marked "to confirm" until the person confirms it in the viewer.
- Electrode sites from the session's setup: a fraction along the forearm from the elbow, palm side
  for the flexor channel, back for the extensor channel, projected onto the CT skin surface.
- Depths under each site, measured on the CT along the inward normal: skin -> muscle surface (fat
  layer) and skin -> first bone or metal.
- Two muscle regions (palm-side half and back half of the forearm muscle in the electrode band)
  coloured by each channel's measured activity. Two surface channels give two numbers per moment:
  this is a two-region map, NOT individual muscles (the CT does not separate them either).
- If the EMG arm differs from the CT arm, everything is mirrored (x -> -x) and labelled so.

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from . import DISCLAIMER
from .mesh import _decimate

MESHES = ["skin", "muscle", "radius", "ulna", "humerus_distal", "carpals_hand", "metal"]


def load_meshes(folder: Path) -> dict[str, trimesh.Trimesh]:
    out = {}
    for n in MESHES:
        p = folder / f"{n}.ply"
        if p.exists():
            out[n] = trimesh.load(p, process=False)
    missing = {"skin", "muscle", "radius", "ulna"} - set(out)
    if missing:
        raise FileNotFoundError(f"CT meshes missing in {folder}: {sorted(missing)}")
    return out


def forearm_frame(m: dict[str, trimesh.Trimesh], ct_arm: str = "left", end_mm: float = 8.0) -> dict[str, Any]:
    rad, uln = np.asarray(m["radius"].vertices), np.asarray(m["ulna"].vertices)
    both = np.vstack([rad[::7], uln[::7]])
    c = both.mean(0)
    _, _, vt = np.linalg.svd(both - c, full_matrices=False)
    d = vt[0]
    hand = m.get("carpals_hand")
    hum = m.get("humerus_distal")
    if hand is not None and np.dot(np.asarray(hand.vertices).mean(0) - c, d) < 0:
        d = -d
    elif hand is None and hum is not None and np.dot(np.asarray(hum.vertices).mean(0) - c, d) > 0:
        d = -d
    t_all = (both - c) @ d
    t0, t1 = t_all.min(), t_all.max()
    elbow = both[t_all < t0 + end_mm].mean(0)
    wrist = both[t_all > t1 - end_mm].mean(0)
    length = float(np.dot(wrist - elbow, d))
    # radio-ulnar direction over the middle of the shafts
    def mid(v):
        t = (v - elbow) @ d
        sel = v[(t > 0.35 * length) & (t < 0.65 * length)]
        return (sel if len(sel) >= 50 else v).mean(0)  # sparse meshes: whole bone
    ru = mid(rad) - mid(uln)
    ru -= np.dot(ru, d) * d
    ru /= np.linalg.norm(ru)
    volar = np.cross(d, ru) if ct_arm == "left" else np.cross(ru, d)
    volar /= np.linalg.norm(volar)
    return {"axis": d, "elbow": elbow, "wrist": wrist, "length_mm": length, "ulna_to_radius": ru, "volar": volar,
            "volar_basis": f"{ct_arm} forearm assumed supinated: volar = axis x (ulna->radius)"}


def ray_hits(mesh: trimesh.Trimesh, origin: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Distances along the ray to every triangle it crosses (Moller-Trumbore, vectorised; no rtree)."""
    tri = np.asarray(mesh.triangles, dtype=np.float64)
    o, dvec = np.asarray(origin, float), np.asarray(direction, float)
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    p = np.cross(dvec, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    s = o - tri[:, 0]
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ dvec) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
    # a ray through an edge or vertex shared by several triangles is one crossing, not several
    return np.unique(np.round(t[hit], 6))


def _first_hit(mesh: trimesh.Trimesh, origin: np.ndarray, direction: np.ndarray, max_mm: float = 200.0):
    d = ray_hits(mesh, origin, direction)
    d = d[d < max_mm]
    return float(d[0]) if len(d) else None


def place_sites(m: dict[str, trimesh.Trimesh], frame: dict, channels: list[dict]) -> list[dict]:
    """channels: [{ch, name, muscle_group, fraction_from_elbow}] -> skin points + CT depths."""
    out = []
    bones = [m[k] for k in ("radius", "ulna", "metal") if k in m]
    for ch in channels:
        side = frame["volar"] if ch["muscle_group"] == "flexors" else -frame["volar"]
        p_axis = frame["elbow"] + ch["fraction_from_elbow"] * frame["length_mm"] * frame["axis"]
        dist = ray_hits(m["skin"], p_axis, side)
        if len(dist) == 0:
            out.append({**ch, "error": "ray did not reach the skin"})
            continue
        skin_pt = p_axis + dist.max() * side  # outermost skin crossing
        inward = -side
        o = skin_pt + inward * 0.05
        fat = _first_hit(m["muscle"], o, inward)
        bone_hits = [h for h in (_first_hit(b, o, inward) for b in bones) if h is not None]
        bone = min(bone_hits) if bone_hits else None
        out.append({**ch, "skin_point_mm": skin_pt.round(2).tolist(), "axis_point_mm": p_axis.round(2).tolist(),
                    "direction": side.round(4).tolist(),
                    "fat_mm": None if fat is None else round(fat, 1),
                    "skin_to_bone_mm": None if bone is None else round(bone, 1),
                    "muscle_mm": None if fat is None or bone is None else round(bone - fat, 1)})
    return out


def compartments(muscle: trimesh.Trimesh, frame: dict, band=(0.05, 0.65)) -> dict[str, trimesh.Trimesh]:
    """Palm-side and back halves of the forearm muscle within the band along the forearm."""
    v = np.asarray(muscle.vertices)
    t = (v - frame["elbow"]) @ frame["axis"]
    axis_pts = frame["elbow"] + np.outer(t, frame["axis"])
    s = np.einsum("ij,j->i", v - axis_pts, frame["volar"])
    L = frame["length_mm"]
    inband = (t > band[0] * L) & (t < band[1] * L)
    out = {}
    for name, keep in (("volar", inband & (s > 0)), ("dorsal", inband & (s <= 0))):
        fmask = keep[muscle.faces].all(axis=1)
        faces_idx = np.flatnonzero(fmask)
        out[name] = (muscle.submesh([faces_idx], append=True) if len(faces_idx)
                     else trimesh.Trimesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=int), process=False))
    return out


def mirror(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    v = np.asarray(mesh.vertices).copy()
    v[:, 0] = -v[:, 0]
    return trimesh.Trimesh(v, np.asarray(mesh.faces)[:, ::-1], process=False)


def activity_series(session_dir: Path, raw_root: Path, window_s: float = 0.5) -> dict[str, Any]:
    """Per-window RMS / floor for every channel of the session's cued recording, with cue spans."""
    from ..dataset import clean_pin, marker_position, record_positions, trials_from_markers
    from ..recording import RawRecording

    sess = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    floor = json.loads((session_dir / "floor.json").read_text(encoding="utf-8"))
    rec = RawRecording.open(raw_root / sess["recordings"][-1])
    fs = rec.fs
    y = clean_pin(rec.to_pin_mv(), fs)
    w = int(fs * window_s)
    n = len(y) // w
    rpos = record_positions(rec, fill_gaps=False)  # y stacks the records, so time = record number / fs
    ratios = []
    for c, f in enumerate(floor["channels"]):
        r = np.sqrt(np.mean(y[: n * w, c].reshape(n, w) ** 2, axis=1)) / f["floor_pin_mv"]
        ratios.append(r.round(2).tolist())
    cues = [{"task": t["task"], "level": t["level"],
             "start_s": round(marker_position(rec, t["start"], t.get("run"), rpos) / fs, 2),
             "end_s": round(marker_position(rec, t["end"], t.get("run"), rpos) / fs, 2)}
            for t in trials_from_markers(rec)]
    return {"window_s": window_s, "t_s": (np.arange(n) * window_s + window_s / 2).round(2).tolist(),
            "x_floor": ratios, "cues": cues, "recording_id": rec.manifest["recording_id"],
            "floor_pin_mv": [c["floor_pin_mv"] for c in floor["channels"]]}


def _color(ratio: float, top: float = 10.0) -> str:
    """1x floor -> grey, >= top x floor -> red, on a log scale."""
    k = float(np.clip(np.log10(max(ratio, 1.0)) / np.log10(top), 0, 1))
    g = np.array([150, 150, 150]) * (1 - k) + np.array([214, 39, 40]) * k
    return f"rgb({int(g[0])},{int(g[1])},{int(g[2])})"


def build(ct_meshes: Path, session_dir: Path, raw_root: Path, out_dir: Path, ct_arm: str = "left",
          emg_arm: str = "right", fraction_from_elbow: float = 1 / 3, faces: int = 6000) -> dict[str, Any]:
    import yaml

    out_dir.mkdir(parents=True, exist_ok=True)
    m = load_meshes(ct_meshes)
    fr = forearm_frame(m, ct_arm)
    setup = yaml.safe_load((session_dir / "setup.yaml").read_text(encoding="utf-8"))
    chans = [{"ch": c["ch"], "name": c["name"], "muscle_group": c["muscle_group"], "site_text": c["site"],
              "fraction_from_elbow": fraction_from_elbow} for c in setup["channels"]]
    sites = place_sites(m, fr, chans)
    comp = compartments(m["muscle"], fr)
    act = activity_series(session_dir, raw_root)
    mirrored = ct_arm != emg_arm
    preview = "preview" in Path(ct_meshes).parts
    summary = {
        "disclaimer": DISCLAIMER,
        "ct_meshes": str(ct_meshes), "session": str(session_dir), "ct_arm": ct_arm, "emg_arm": emg_arm,
        "mirrored": mirrored,
        "frame": {"elbow_mm": fr["elbow"].round(2).tolist(), "wrist_mm": fr["wrist"].round(2).tolist(),
                  "forearm_bone_length_mm": round(fr["length_mm"], 1), "axis": fr["axis"].round(4).tolist(),
                  "volar": fr["volar"].round(4).tolist(), "volar_basis": fr["volar_basis"],
                  "volar_confirmed": False},
        "sites": sites, "activity": {k: act[k] for k in ("recording_id", "window_s", "floor_pin_mv")},
        "limits": ["two channels -> two regions (palm-side and back halves of the forearm muscle), not single muscles",
                   "electrode positions from the setup text (fraction from the elbow), not measured on the skin",
                   ("CT meshes are the preview build (old label map, no accuracy checks)" if preview else
                    "CT meshes from the full build; their accuracy checks are in the build's report.md"),
                   "mirrored" if mirrored else "same arm"],
    }
    (out_dir / "emg_on_ct.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    sc = scene(m, comp, sites, mirrored, faces)
    sc["summary"] = summary
    (out_dir / "scene.json").write_text(json.dumps(sc), encoding="utf-8")
    return summary


def scene(m, comp, sites, mirrored: bool, faces: int = 6000) -> dict[str, Any]:
    """CT forearm for the signal viewer: decimated (and mirrored) meshes, regions, electrode sites."""

    def prep(mesh, n):
        mesh = _decimate(mesh, n) if len(mesh.faces) > n else mesh
        return mirror(mesh) if mirrored else mesh

    def pack(mesh):
        v, f = np.asarray(mesh.vertices, float), np.asarray(mesh.faces, int)
        return {"x": v[:, 0].round(2).tolist(), "y": v[:, 1].round(2).tolist(), "z": v[:, 2].round(2).tolist(),
                "i": f[:, 0].tolist(), "j": f[:, 1].tolist(), "k": f[:, 2].tolist()}

    static = [("radius", "#d9cfb8", 1.0, faces // 2), ("ulna", "#d9cfb8", 1.0, faces // 2),
              ("humerus_distal", "#d9cfb8", 1.0, faces // 3), ("carpals_hand", "#d9cfb8", 1.0, faces // 2),
              ("metal", "#5b8fd6", 1.0, faces // 4), ("skin", "#e8b9a0", 0.10, faces)]
    meshes = [{"name": n, "color": c, "opacity": op, **pack(prep(m[n], k))} for n, c, op, k in static if n in m]
    region = {"flexors": "volar", "extensors": "dorsal"}
    regions = []
    for s in sites:
        key = region.get(s["muscle_group"])
        if key and len(comp[key].faces) and key not in {r["region"] for r in regions}:
            regions.append({"region": key, "ch": s["ch"], "muscle_group": s["muscle_group"],
                            **pack(prep(comp[key], faces))})
    marks = []
    for s in sites:
        if "skin_point_mm" not in s:
            continue
        p = list(s["skin_point_mm"])
        if mirrored:
            p[0] = -p[0]
        marks.append({"ch": s["ch"], "muscle_group": s["muscle_group"], "point": p, "fat_mm": s.get("fat_mm"),
                      "muscle_mm": s.get("muscle_mm"), "skin_to_bone_mm": s.get("skin_to_bone_mm")})
    return {"meshes": meshes, "regions": regions, "sites": marks, "mirrored": mirrored}
