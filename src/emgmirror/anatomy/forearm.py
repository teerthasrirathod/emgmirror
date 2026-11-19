"""Parametric 3D forearm: radius, ulna and the main flexor/extensor muscles.

This is a *template* anatomy scaled to tape-measure values (length and two
girths per arm). Muscle bulk on each side scales with the soft-tissue part of
the proximal girth, so atrophy on the left shows up as thinner muscles. It is a
visual model, not imaging: muscle shapes become patient-specific only when real
thickness measurements (for example from ultrasound) are added via
`muscle_overrides`.

Coordinates: x along the forearm (0 = elbow crease, L = wrist crease),
y lateral (thumb side, +), z anterior (palm side, +). Palm up (supinated).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import PchipInterpolator


@dataclass(frozen=True)
class MuscleSpec:
    name: str
    long_name: str
    theta_deg: float  # around the axis, 0 = lateral, 90 = anterior
    rho: float  # centre distance from axis as a fraction of local radius
    s_start: float
    s_peak: float
    s_end: float
    thickness: float  # radial thickness at the belly, fraction of local radius
    width_deg: float  # angular width at the belly


MUSCLES = [
    MuscleSpec("BR", "Brachioradialis", 20, 0.70, 0.00, 0.20, 0.80, 0.30, 40),
    MuscleSpec("ECRL", "Extensor carpi radialis longus", -12, 0.70, 0.02, 0.25, 0.65, 0.25, 28),
    MuscleSpec("ECRB", "Extensor carpi radialis brevis", -38, 0.70, 0.05, 0.30, 0.70, 0.25, 28),
    MuscleSpec("EDC", "Extensor digitorum", -82, 0.70, 0.05, 0.32, 0.80, 0.28, 45),
    MuscleSpec("ECU", "Extensor carpi ulnaris", -130, 0.70, 0.05, 0.30, 0.85, 0.22, 34),
    MuscleSpec("FCU", "Flexor carpi ulnaris", -172, 0.70, 0.02, 0.30, 0.85, 0.26, 40),
    MuscleSpec("FDP", "Flexor digitorum profundus", 150, 0.40, 0.05, 0.35, 0.85, 0.30, 60),
    MuscleSpec("FDS", "Flexor digitorum superficialis", 112, 0.62, 0.10, 0.40, 0.85, 0.28, 50),
    MuscleSpec("PL", "Palmaris longus", 90, 0.80, 0.05, 0.25, 0.55, 0.12, 14),
    MuscleSpec("FCR", "Flexor carpi radialis", 60, 0.72, 0.03, 0.30, 0.70, 0.22, 30),
    MuscleSpec("PT", "Pronator teres", 35, 0.50, 0.00, 0.12, 0.45, 0.22, 30),
    MuscleSpec("SUP", "Supinator", -20, 0.40, 0.00, 0.12, 0.35, 0.18, 40),
]
BY_NAME = {m.name: m for m in MUSCLES}

REFERENCE_LENGTH_CM = 26.0
BULK, SPREAD = 1.5, 1.3  # global muscle thickness / width factors of the template
N_LONG, N_AROUND = 40, 20


def outer_radius(s: np.ndarray, circ_prox: float, circ_dist: float) -> np.ndarray:
    rp, rd = circ_prox / (2 * np.pi), circ_dist / (2 * np.pi)
    knots = [0.0, 0.25, 0.55, 0.80, 1.0]
    vals = [0.92 * rp, rp, 0.55 * rp + 0.45 * rd, 1.04 * rd, rd]
    return PchipInterpolator(knots, vals)(s)


def soft_tissue_scale(arm: dict, ref: dict) -> float:
    """Muscle bulk relative to the reference arm, from the proximal girth."""
    core = 0.45 * ref["circ_proximal_cm"] / (2 * np.pi)
    r_arm = arm["circ_proximal_cm"] / (2 * np.pi) - core
    r_ref = ref["circ_proximal_cm"] / (2 * np.pi) - core
    return float(np.clip(r_arm / r_ref, 0.3, 1.4))


def _belly(s: np.ndarray, spec: MuscleSpec) -> np.ndarray:
    """0 before origin, rises to 1 at the belly, tapers to a thin tendon."""
    y = np.zeros_like(s)
    up = (s >= spec.s_start) & (s <= spec.s_peak)
    y[up] = np.sin(0.5 * np.pi * (s[up] - spec.s_start) / max(spec.s_peak - spec.s_start, 1e-6))
    down = s > spec.s_peak
    tail = np.clip((s[down] - spec.s_peak) / max(spec.s_end - spec.s_peak, 1e-6), 0, 1)
    y[down] = 0.15 + 0.85 * np.cos(0.5 * np.pi * tail) ** 2
    y[s > spec.s_end] = 0.12
    return np.maximum(y, 0.05)


def _tube(part: dict) -> dict:
    """Mesh of a tube with elliptical cross-sections. Returns Mesh3d arrays."""
    xs, ring = part["xs"], section_rings(part)
    X = np.repeat(xs, N_AROUND)
    Y, Z = ring[0].ravel(), ring[1].ravel()
    I, J, K = [], [], []
    for k in range(len(xs) - 1):
        for j in range(N_AROUND):
            a = k * N_AROUND + j
            b = k * N_AROUND + (j + 1) % N_AROUND
            c, d = a + N_AROUND, b + N_AROUND
            I += [a, b]
            J += [b, d]
            K += [c, c]
    return {"x": X, "y": Y, "z": Z, "i": I, "j": J, "k": K}


def section_rings(part: dict) -> np.ndarray:
    """Outline points (2, n_long, N_AROUND) of every cross-section of a part."""
    phi = np.linspace(0, 2 * np.pi, N_AROUND, endpoint=False)
    ang = part["angle"]
    ty, tz = -np.sin(ang), np.cos(ang)  # tangential unit vector
    ry, rz = np.cos(ang), np.sin(ang)  # radial unit vector
    a = part["semi_t"][:, None] * np.cos(phi)[None, :]
    b = part["semi_r"][:, None] * np.sin(phi)[None, :]
    y = part["cy"][:, None] + a * ty + b * ry
    z = part["cz"][:, None] + a * tz + b * rz
    return np.stack([y, z])


def arm_geometry(measure: dict, muscle_scale: float, mirror: bool,
                 muscle_overrides: dict | None = None) -> dict:
    """Centre line and cross-section size of skin, bones and muscles along the arm."""
    L = measure["length_cm"]
    s = np.linspace(0, 1, N_LONG)
    xs = s * L
    R = outer_radius(s, measure["circ_proximal_cm"], measure["circ_distal_cm"])
    ax, az = 1.10, 0.90  # forearm cross-section is wider than deep
    side = -1.0 if mirror else 1.0
    bone_k = L / REFERENCE_LENGTH_CM
    zero = np.zeros_like(xs)

    def part(cy, cz, semi_t, semi_r, angle=0.0):
        return {"xs": xs, "cy": np.broadcast_to(cy, xs.shape) * 1.0, "cz": np.broadcast_to(cz, xs.shape) * 1.0,
                "semi_t": semi_t, "semi_r": semi_r, "angle": angle}

    parts = {"skin": part(zero, zero, R * az, R * ax)}
    # Bones: radius thin at the elbow, broad at the wrist; ulna the opposite.
    rad_r = bone_k * (0.45 + 0.55 * s)
    uln_r = bone_k * (0.95 - 0.55 * s)
    parts["radius"] = part(side * 0.42 * R * ax, -0.05 * R * az, rad_r, rad_r)
    parts["ulna"] = part(-side * 0.42 * R * ax, -0.22 * R * az, uln_r, uln_r)

    overrides = muscle_overrides or {}
    for spec in MUSCLES:
        th = np.deg2rad(spec.theta_deg)
        if mirror:
            th = np.pi - th
        scale = overrides.get(spec.name, muscle_scale)
        belly = _belly(s, spec)
        rho = spec.rho * R
        semi_r = 0.5 * BULK * spec.thickness * R * belly * scale
        semi_t = 0.5 * SPREAD * np.deg2rad(spec.width_deg) * rho * np.maximum(belly, 0.3) * (0.6 + 0.4 * scale)
        parts[spec.name] = part(rho * np.cos(th) * ax, rho * np.sin(th) * az, semi_t, semi_r, th)
    return parts


def build_arm(measure: dict, muscle_scale: float, mirror: bool,
              muscle_overrides: dict | None = None) -> dict:
    """Mesh3d-ready dicts for every part of one arm."""
    return {k: _tube(v) for k, v in arm_geometry(measure, muscle_scale, mirror, muscle_overrides).items()}


def cross_section(geom: dict, s: float) -> dict:
    """2D outlines (y, z) of every part at fraction `s` of the forearm length."""
    k = int(round(s * (N_LONG - 1)))
    out = {}
    for name, part in geom.items():
        ring = section_rings(part)[:, k, :]
        out[name] = (np.append(ring[0], ring[0][0]), np.append(ring[1], ring[1][0]))
    return out
