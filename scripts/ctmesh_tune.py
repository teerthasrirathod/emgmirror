"""Reproduce the smoothing measurements behind the ctmesh defaults (docs/ct_platform/results/S2_mesh.md).

Analytic shapes with known surfaces (a rotated ellipsoid, a thin-walled tilted tube) are voxelised on an
isotropic 1.5 mm grid and an anisotropic 0.8 x 0.8 x 2.5 mm grid, then meshed with each candidate setting.

    python scripts/ctmesh_tune.py            # prints a markdown table

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import time

import numpy as np
import SimpleITK as sitk
import trimesh

from emgmirror.ctmesh import fixtures, qc, surface
from emgmirror.ctmesh.contract import Label, LabelMap

R = fixtures.rotation((1, 1, 0), 30.0)
ELL_C, ELL_AX = np.array([-8.0, 0.0, 0.0]), np.array([18.0, 13.0, 10.0])
TUBE_C, TUBE_A = np.array([16.0, 0.0, 0.0]), np.array([0.3, 0.2, 1.0]) / np.linalg.norm([0.3, 0.2, 1.0])
TUBE_R, TUBE_W, TUBE_H = 4.0, 1.6, 20.0


def inside(P):
    lab = np.zeros(len(P), np.uint16)
    Q = (P - ELL_C) @ R
    lab[((Q / ELL_AX) ** 2).sum(1) <= 1] = 1
    d = P - TUBE_C
    t = d @ TUBE_A
    r2 = (d ** 2).sum(1) - t ** 2
    lab[(r2 <= TUBE_R ** 2) & (r2 >= (TUBE_R - TUBE_W) ** 2) & (np.abs(t) <= TUBE_H)] = 2
    return lab


def grid(spacing, size_mm=64.0):
    n = [int(round(size_mm / s)) for s in spacing]
    origin = tuple(-size_mm / 2 + s / 2 for s in spacing)
    arr = fixtures._voxelise((n[2], n[1], n[0]), spacing, origin, np.eye(3), inside, f=4)
    return LabelMap(arr, tuple(spacing), origin, np.eye(3), [Label(1, "ellipsoid"), Label(2, "tube")])


def ellipsoid_errors(m: trimesh.Trimesh):
    def f(P):
        Q = (P - ELL_C) @ R
        g = Q / ELL_AX ** 2
        F = np.sqrt(((Q / ELL_AX) ** 2).sum(1))
        return (F - 1) / np.linalg.norm(g, axis=1) * F, (g / np.linalg.norm(g, axis=1, keepdims=True)) @ R.T
    d, _ = f(m.vertices)
    _, n = f(m.triangles_center)
    ang = np.degrees(np.arccos(np.clip((m.face_normals * n).sum(1), -1, 1)))
    return float(np.sqrt((d ** 2).mean())), float(ang.mean()), float(np.percentile(ang, 95))


def old_pipeline(lm, lid):
    from emgmirror.ct.mesh import MeshParams, mask_to_mesh
    m, _ = mask_to_mesh(lm.array == lid, lm.to_sitk(), MeshParams(max_faces=None))
    return m


CONFIGS = [
    ("SurfaceNets, no smoothing (voxel boundary)", dict(smoothing_iterations=0)),
    ("SurfaceNets built-in smoother, VTK defaults (16 it, automatic constraints)",
     dict(smoothing_iterations=0, sn_smoothing_iterations=16)),
    ("SurfaceNets built-in smoother, 50 it, constraint 0.5 voxel",
     dict(smoothing_iterations=0, sn_smoothing_iterations=50, sn_constraint_distance_mm="half")),
    ("SurfaceNets + windowed sinc pb 0.05 / 30 it, clamp 1.0 voxel",
     dict(passband=0.05, smoothing_iterations=30, constraint_voxels=1.0)),
    ("SurfaceNets + windowed sinc pb 0.1 / 50 it, clamp 1.0 voxel",
     dict(passband=0.1, smoothing_iterations=50, constraint_voxels=1.0)),
    ("SurfaceNets + windowed sinc pb 0.1 / 50 it, clamp 0.45 voxel, all labels smoothed as one shared mesh",
     dict(shared_smoothing=True)),
    ("**default**: SurfaceNets + windowed sinc pb 0.1 / 50 it, clamp 0.45 voxel", dict()),
    ("**pipeline output** (default + fallbacks: flying edges / voxel boundary)", "pipeline"),
    ("old `ct/mesh.py`: marching cubes + Taubin 20", None),
]


def main():
    rows = []
    for spacing in ((1.5, 1.5, 1.5), (0.8, 0.8, 2.5)):
        lm = grid(spacing)
        vmm = qc.voxel_mm(spacing)
        B = {lid: qc.label_boundary(lm.array, lid, lm.index_to_lps) for lid in (1, 2)}
        vox = {lid: np.count_nonzero(lm.array == lid) * lm.voxel_volume_mm3 for lid in (1, 2)}
        for name, kw in CONFIGS:
            t = time.perf_counter()
            if kw is None:
                ms = {lid: old_pipeline(lm, lid) for lid in (1, 2)}
            elif kw == "pipeline":
                from emgmirror.ctmesh.pipeline import PipelineParams, _choose_native
                s = surface.surfacenets_all(lm, [1, 2])
                ms, used = {}, []
                for lid in (1, 2):
                    best, _, _ = _choose_native(lm, lid, s[lid], B[lid], PipelineParams())
                    ms[lid] = best.mesh
                    used.append(best.method)
                name = f"{name} [{' / '.join(used)}]"
            else:
                kw = dict(kw)
                if kw.get("sn_constraint_distance_mm") == "half":
                    kw["sn_constraint_distance_mm"] = 0.5 * min(spacing)
                s = surface.surfacenets_all(lm, [1, 2], surface.SurfaceParams(**kw))
                ms = {lid: s[lid].mesh for lid in (1, 2)}
            dt = time.perf_counter() - t
            rms, nmean, np95 = ellipsoid_errors(ms[1])
            hd = {lid: qc.surface_distance(ms[lid].vertices, ms[lid].faces, *B[lid], vmm)["hd95_mm"] for lid in (1, 2)}
            dv = {lid: (ms[lid].volume - vox[lid]) / vox[lid] * 100 for lid in (1, 2)}
            si = sum(qc.self_intersections(ms[lid]) for lid in (1, 2))
            wt = all(ms[lid].is_watertight for lid in (1, 2))
            rows.append(f"| {spacing[0]}x{spacing[1]}x{spacing[2]} | {name} | {rms:.2f} | {nmean:.1f} / {np95:.0f} "
                        f"| {dv[1]:+.1f} | {hd[1]:.2f} | {dv[2]:+.1f} | {hd[2]:.2f} | {si} | {'yes' if wt else 'no'} "
                        f"| {dt:.2f} |")
    print("| grid (mm) | setting | ellipsoid RMS to true surface (mm) | ellipsoid normal error mean / p95 (deg) "
          "| ellipsoid dV vs voxels (%) | ellipsoid HD95 vs label (mm) | thin tube dV (%) | thin tube HD95 (mm) "
          "| self-intersecting pairs | watertight | time (s) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))


if __name__ == "__main__":
    _ = sitk
    main()
