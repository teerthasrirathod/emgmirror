"""Plotly figures: right arm (reference) beside the left arm coloured by ability."""

from __future__ import annotations

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .forearm import BY_NAME, arm_geometry, build_arm, cross_section, section_rings, soft_tissue_scale

BONE = "#e8e2d0"
BONE_EDGE = "#a89f86"
SKIN = "#c98d68"
REFERENCE_MUSCLE = "#b9473f"
NEUTRAL = "#8c8c8c"
SECTION_AT = 0.30  # fraction of length from the elbow: around the muscle bellies

# Percent of the reference arm -> colour. Shared with the dashboard JS.
STOPS = [(0, "#7a1020"), (40, "#d7301f"), (70, "#f0a830"), (90, "#9ccb5b"), (110, "#2f9e5b")]


def pct_color(pct: float | None) -> str:
    if pct is None:
        return NEUTRAL
    p = max(STOPS[0][0], min(STOPS[-1][0], pct))
    for (p0, c0), (p1, c1) in zip(STOPS, STOPS[1:]):
        if p <= p1:
            f = (p - p0) / (p1 - p0)
            rgb = [round(int(c0[i:i + 2], 16) * (1 - f) + int(c1[i:i + 2], 16) * f) for i in (1, 3, 5)]
            return "#%02x%02x%02x" % tuple(rgb)
    return STOPS[-1][1]


def muscle_group_map(cfg: dict) -> dict[str, str]:
    """Anatomical muscle -> channel group (only groups that are recorded)."""
    recorded = {c["muscle"] for c in cfg["channels"]}
    out = {}
    for group, names in cfg["anatomy"]["groups"].items():
        if group in recorded:
            for n in names:
                out[n] = group
    return out


def _arms(cfg: dict, muscle_overrides: dict | None):
    subj = cfg["subject"]
    ref, test = subj["right"], subj["left"]
    scale_left = soft_tissue_scale(test, ref)
    for arm, measure, scale in (("right", ref, 1.0), ("left", test, scale_left)):
        yield arm, measure, scale, (muscle_overrides or {}).get(arm)


def _style(cfg: dict, summary: dict | None, metric: str, arm: str, part: str, scale: float):
    """Colour and hover text of one muscle."""
    groups = muscle_group_map(cfg)
    group = groups.get(part)
    spec = BY_NAME[part]
    if arm == "right":
        color = REFERENCE_MUSCLE if group else NEUTRAL
        return color, f"{spec.long_name}<br>group: {group or 'not recorded'}<br>reference = 100%", group
    val = (summary or {}).get(group, {}).get(metric) if group else None
    txt = f"{val:.0f}% of right" if val is not None else "no data"
    hover = (f"{spec.long_name}<br>group: {group or 'not recorded'}<br>{metric}: {txt}"
             f"<br>bulk vs right: {100 * scale:.0f}% (from girth)")
    return pct_color(val), hover, group


def forearm_figure(cfg: dict, summary: dict | None = None, metric: str = "activation_pct",
                   muscle_overrides: dict | None = None) -> tuple[go.Figure, list[dict]]:
    """3D view. Returns the figure and per-trace metadata (arm, part, group) for live recolouring."""
    fig = make_subplots(rows=2, cols=1, specs=[[{"type": "scene"}], [{"type": "scene"}]],
                        subplot_titles=("Right forearm: reference (100%)", "Left forearm: % of right"),
                        vertical_spacing=0.04)
    meta: list[dict] = []
    for row, (arm, measure, scale, over) in enumerate(_arms(cfg, muscle_overrides), start=1):
        scene = "scene" if row == 1 else "scene2"
        geom = arm_geometry(measure, scale, mirror=(arm == "left"), muscle_overrides=over)
        meshes = build_arm(measure, scale, mirror=(arm == "left"), muscle_overrides=over)

        # Skin as girth rings (a transparent surface renders poorly in WebGL).
        rings = section_rings(geom["skin"])
        xs = geom["skin"]["xs"]
        for k in range(0, len(xs), 6):
            fig.add_trace(go.Scatter3d(x=[xs[k]] * (rings.shape[2] + 1), y=list(rings[0, k]) + [rings[0, k, 0]],
                                       z=list(rings[1, k]) + [rings[1, k, 0]], mode="lines",
                                       line=dict(color=SKIN, width=2), hoverinfo="skip", scene=scene))
            meta.append({"arm": arm, "part": "skin", "group": None})

        for part, mesh in meshes.items():
            if part == "skin":
                continue
            if part in ("radius", "ulna"):
                color, hover, group = BONE, f"{part.title()} ({arm})", None
            else:
                color, hover, group = _style(cfg, summary, metric, arm, part, scale)
            fig.add_trace(go.Mesh3d(**mesh, color=color, opacity=1.0, name=part, hovertext=hover,
                                    hoverinfo="text", flatshading=False, scene=scene,
                                    lighting=dict(ambient=0.5, diffuse=0.8, specular=0.2, roughness=0.7)))
            meta.append({"arm": arm, "part": part, "group": group})

    camera = dict(eye=dict(x=0.2, y=-1.15, z=1.05), up=dict(x=0, y=0, z=1), center=dict(x=0, y=0, z=-0.1))
    axis = dict(visible=False)
    for sc in ("scene", "scene2"):
        fig.layout[sc].update(xaxis=axis, yaxis=axis, zaxis=axis, aspectmode="data", camera=camera)
    fig.update_layout(margin=dict(l=0, r=0, t=30, b=0), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", height=620)
    return fig, meta


def cross_section_figure(cfg: dict, summary: dict | None = None, metric: str = "activation_pct",
                         s: float = SECTION_AT, muscle_overrides: dict | None = None) -> go.Figure:
    """2D slice through both forearms at the muscle bellies, viewed from the hand."""
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Right (reference)", "Left (% of right)"),
                        horizontal_spacing=0.06)
    for col, (arm, measure, scale, over) in enumerate(_arms(cfg, muscle_overrides), start=1):
        sec = cross_section(arm_geometry(measure, scale, mirror=(arm == "left"), muscle_overrides=over), s)
        y, z = sec.pop("skin")
        fig.add_trace(go.Scatter(x=y, y=z, fill="toself", fillcolor="rgba(201,141,104,0.12)",
                                 line=dict(color=SKIN, width=2), hoverinfo="skip"), row=1, col=col)
        for part, (y, z) in sec.items():
            if part in ("radius", "ulna"):
                color, hover = BONE, f"{part.title()}"
                line = BONE_EDGE
            else:
                color, hover, _ = _style(cfg, summary, metric, arm, part, scale)
                line = "rgba(0,0,0,0.35)"
            fig.add_trace(go.Scatter(x=y, y=z, fill="toself", fillcolor=color, line=dict(color=line, width=1),
                                     hovertext=hover, hoverinfo="text", mode="lines"), row=1, col=col)
        fig.update_xaxes(visible=False, scaleanchor="y" if col == 1 else "y2", row=1, col=col)
        fig.update_yaxes(visible=False, row=1, col=col)
    fig.update_layout(showlegend=False, height=330, margin=dict(l=0, r=0, t=30, b=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig
