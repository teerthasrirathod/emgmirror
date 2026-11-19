"""HTML report of the synthetic experiments (reads the JSON files in a folder)."""

from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ARM = {"right": "#3b6fb6", "left": "#d0582f"}
SEQ = ["#3b6fb6", "#d0582f", "#2f9e5b", "#8e5bb5", "#c9a227", "#5b8e9e"]


def _load(folder: Path, name: str):
    p = folder / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _html(fig: go.Figure, first: bool) -> str:
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=55, r=15, t=45, b=45))
    return fig.to_html(full_html=False, include_plotlyjs="cdn" if first else False)


def effort_section(r: dict, first: bool) -> str:
    moves = list(r["summary"])
    fig = make_subplots(rows=2, cols=3, subplot_titles=moves, vertical_spacing=0.15)
    for i, m in enumerate(moves):
        row, col = i // 3 + 1, i % 3 + 1
        for arm in ("right", "left"):
            rr = [x for x in r["rows"] if x["movement"] == m and x["arm"] == arm]
            fig.add_trace(go.Scatter(x=[x["effort"] * 100 for x in rr], y=[x["net_rms_mv"] for x in rr],
                                     name=arm, mode="lines+markers", line=dict(color=ARM[arm]),
                                     showlegend=(i == 0)), row=row, col=col)
        fig.update_xaxes(title_text="effort %", row=row, col=col)
    fig.update_yaxes(title_text="net RMS (mV)", col=1)
    fig.update_layout(height=560, title="Signal vs effort on the movement's main muscle")

    mu = go.Figure()
    for arm, dash in (("right", "solid"), ("left", "dot")):
        rr = [x for x in r["rows"] if x["movement"] == "power_grip" and x["arm"] == arm]
        mu.add_trace(go.Scatter(x=[x["effort"] * 100 for x in rr], y=[x["active_mu"] for x in rr],
                                name=f"{arm}: active units", line=dict(color=ARM[arm], dash=dash)))
        mu.add_trace(go.Scatter(x=[x["effort"] * 100 for x in rr], y=[x["mean_rate_hz"] for x in rr],
                                name=f"{arm}: mean firing rate (Hz)", yaxis="y2",
                                line=dict(color=ARM[arm], dash="dash", width=1)))
    mu.update_layout(height=360, title="Power grip, flexors: motor units recruited and their firing rate (ground truth)",
                     xaxis_title="effort %", yaxis_title="active motor units",
                     yaxis2=dict(overlaying="y", side="right", title="Hz"))

    rows = []
    for m, s in r["summary"].items():
        lr = s["left_pct_of_right_by_effort"]
        rows.append(f"<tr><td>{m}</td><td>{s['right']['min_effort_snr6db'] or '-'}</td>"
                    f"<td>{s['right']['mvc_net_rms_mv'] * 1000:.0f} µV</td><td>{s['right']['mvc_adc_peak_counts']:.0f}</td>"
                    f"<td>{lr.get('0.2', 0) or 0:.0f}%</td><td>{lr.get('1.0', 0) or 0:.0f}%</td></tr>")
    fe = r["frontend"]
    table = ("<table><tr><th>Movement</th><th>Lowest effort with SNR ≥ 6 dB</th><th>Right MVC net RMS</th>"
             "<th>Right MVC peak (ADC counts from mid-scale)</th><th>Left/right at 20%</th><th>Left/right at 100%</th></tr>"
             + "".join(rows) + "</table>"
             f"<p class='note'>Front-end: {fe['adc_bits']}-bit ADC, gain {fe['gain']:.0f}, 1 LSB = {fe['lsb_uv']:.1f} µV, "
             f"range ±{fe['range_mv']:.2f} mV ({2 ** (fe['adc_bits'] - 1)} counts).</p>")
    return "<h2>1. Effort: what signal to expect</h2>" + table + _html(fig, first) + _html(mu, False)


def movements_section(r: dict) -> str:
    names, groups = list(r["activation_net_mv"]), r["groups"]
    z = np.array([[r["activation_net_mv"][m][g] for g in groups] for m in names])
    zn = z / np.maximum(z.max(axis=0, keepdims=True), 1e-9)
    t = np.array([[r["truth_active_pct"][m][g] for g in groups] for m in names])
    fig = make_subplots(rows=1, cols=2, subplot_titles=("What each electrode sees (net RMS, / column max)",
                                                        "Ground truth: % of each pool firing"), horizontal_spacing=0.18)
    fig.add_trace(go.Heatmap(z=zn, x=groups, y=names, colorscale="Blues", zmin=0, zmax=1, showscale=False,
                             text=(z * 1000).round(0), texttemplate="%{text}"), row=1, col=1)
    fig.add_trace(go.Heatmap(z=t, x=groups, y=names, colorscale="Oranges", zmin=0, zmax=100, showscale=False,
                             text=t.round(0), texttemplate="%{text}"), row=1, col=2)
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=620, title=f"Movement map at {r['effort'] * 100:.0f}% effort (numbers in µV / %)")

    best = r["best_by_count"]
    acc = go.Figure(go.Bar(x=[f"{k} electrode(s)<br>{', '.join(v['channels'])}" for k, v in best.items()],
                           y=[100 * v["accuracy"] for v in best.values()], marker_color="#3b6fb6"))
    acc.add_hline(y=100 * r["default_flexor_extensor_accuracy"], line_dash="dot",
                  annotation_text="flexors + extensors (current 2-channel plan)")
    acc.add_hline(y=100 * r["chance"], line_dash="dash", annotation_text="chance")
    acc.update_layout(height=380, yaxis_title="movements told apart (%)",
                      title="Best electrode positions for telling the 17 movements apart (LDA, held-out trial)")
    return "<h2>2. Movements and exercises: which muscles fire</h2>" + _html(fig, False) + _html(acc, False)


def rotation_section(r: dict) -> str:
    groups = list(next(iter(r["relative_pct"].values())))
    fig = go.Figure()
    for i, g in enumerate(groups):
        fig.add_trace(go.Scatter(x=r["angles"], y=[r["relative_pct"][str(a)][g] for a in r["angles"]],
                                 name=g, line=dict(color=SEQ[i])))
    fig.add_hline(y=100, line_dash="dot")
    fig.update_layout(height=360, xaxis_title="forearm rotation (°): -90 palm down … +90 palm up",
                      yaxis_title="% of signal at +90°", title="Same 50% grip, electrodes placed palm-up")
    tr = r["trace"]
    dyn = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.3, 0.7])
    dyn.add_trace(go.Scatter(x=tr["t"], y=tr["angle"], name="angle (°)", line=dict(color="#555")), row=1, col=1)
    for i, g in enumerate(groups):
        dyn.add_trace(go.Scatter(x=tr["t"], y=tr["envelope"][g], name=g, line=dict(color=SEQ[i])), row=2, col=1)
    dyn.update_layout(height=420, title="Screwdriver rotation (palm down ↔ up every 2 s): envelopes", xaxis2_title="s")
    return "<h2>3. Hand rotation</h2>" + _html(fig, False) + _html(dyn, False)


def noise_section(r: dict) -> str:
    conds, pipes = list(r["table"]), r["pipelines"]
    z = np.array([[r["table"][c][p]["mean_abs_error_pct"] for p in pipes] for c in conds])
    heat = go.Figure(go.Heatmap(z=np.minimum(z, 60), x=pipes, y=conds, colorscale="RdYlGn_r", zmin=0, zmax=60,
                                text=z.round(0), texttemplate="%{text}%", colorbar=dict(title="error %")))
    heat.update_layout(height=400, title="Mean |error| of recovered EMG amplitude vs the true clean signal")
    line = go.Figure()
    for i, p in enumerate(pipes):
        e = r["table"]["typical_home"][p]["abs_error_pct_by_effort"]
        line.add_trace(go.Scatter(x=[float(k) * 100 for k in e], y=list(e.values()), name=p, line=dict(color=SEQ[i])))
    line.update_layout(height=340, xaxis_type="log", xaxis_title="effort % (log)", yaxis_title="|error| %",
                       title="Typical home setup: error vs effort")
    rows = "".join(f"<tr><td>{c}</td><td>{r['table'][c]['best_pipeline']}</td>"
                   f"<td>{r['table'][c][r['table'][c]['best_pipeline']]['rest_rms_uv']:.1f} µV</td></tr>" for c in conds)
    return ("<h2>4. Noise: which cleaning pipeline to use</h2><table><tr><th>Condition</th><th>Best pipeline</th>"
            f"<th>Rest floor after it</th></tr>{rows}</table>" + _html(heat, False) + _html(line, False))


def duration_section(r: dict) -> str:
    d = r["durations_s"]
    fig = go.Figure()
    for key, name, c in (("activation_pct", "Activation (MVC), L/R %", "#3b6fb6"),
                         ("mu_firing_pct", "Firing units (light hold), L/R %", "#d0582f")):
        m = [r[key][str(x)]["mean"] for x in d]
        s = [r[key][str(x)]["sd"] or 0 for x in d]
        fig.add_trace(go.Scatter(x=d, y=m, error_y=dict(array=s), name=name, line=dict(color=c)))
    fig.update_layout(height=380, xaxis_title="hold duration (s)", yaxis_title="left as % of right (mean ± SD)",
                      title=f"How long to hold: spread over {r['repeats']} repeated synthetic sessions")
    return "<h2>5. Recording duration</h2>" + _html(fig, False)


def calibration_section(r: dict) -> str:
    hold = r["holdout"]
    rows = "".join(f"<tr><td>{k}</td><td>{v['r2']:+.2f}</td><td>{v['mae']:.3f}</td><td>{v['coverage_80']:.0%}</td></tr>"
                   for k, v in hold.items())
    pr = r["paired"]
    t = [x["true_ratio_pct"] for x in pr["rows"]]
    fig = go.Figure()
    for k, name, c in (("engine_ratio_pct", "calibration engine", "#2f9e5b"),
                       ("cumulant_ratio_pct", "cumulant estimator", "#d0582f"),
                       ("amplitude_ratio_pct", "MVC amplitude", "#8c8c8c")):
        fig.add_trace(go.Scatter(x=t, y=[x[k] for x in pr["rows"]], mode="markers", name=name,
                                 marker=dict(color=c, size=9)))
    fig.add_trace(go.Scatter(x=[0, 110], y=[0, 110], mode="lines", line=dict(dash="dot", color="#555"), name="perfect"))
    fig.update_layout(height=420, xaxis_title="true left/right motor units (%)", yaxis_title="estimated (%)",
                      title="Left/right motor-unit ratio on held-out synthetic pairs")
    sc = "".join(f"<tr><td>{k}</td><td>{v['mae_pct_points']:.1f}</td><td>{v['corr']:.2f}</td></tr>"
                 for k, v in pr["scores"].items())
    return ("<h2>6. Calibration engine (fitting the simulator to a recording)</h2>"
            f"<p class='note'>Trained on {r['n_train']} simulated recordings; scored on recordings it never saw.</p>"
            "<table><tr><th>Hidden setting</th><th>R²</th><th>MAE</th><th>80% interval coverage</th></tr>"
            f"{rows}</table><table><tr><th>Method</th><th>MAE (pts)</th><th>r</th></tr>{sc}</table>" + _html(fig, False))


def paired_section(r: dict) -> str:
    h = r["holdout"]
    rows = "".join(f"<tr><td>{k}</td><td>{h[k]['r2']:+.2f}</td><td>{h[k]['mae']:.3f}</td><td>{h[k]['coverage_80']:.0%}</td></tr>"
                   for k in ("mu_fraction", "enlargement", "drive_max"))
    cmp_ = "".join(f"<tr><td>{name}</td><td>{100 * h[k]['mae']:.1f}</td><td>{h[k]['corr']:.2f}</td></tr>"
                   for k, name in (("paired_engine_mu_ratio", "paired engine"), ("cumulant_baseline_mu_ratio", "cumulant estimator")))
    return ("<h3>Paired engine (right arm as reference)</h3>"
            f"<p class='note'>Trained on {r['n_train']} healthy-right / injured-left pairs with independent electrode coupling.</p>"
            "<table><tr><th>Left-arm setting</th><th>R²</th><th>MAE</th><th>80% coverage</th></tr>" + rows + "</table>"
            "<table><tr><th>Left/right motor units</th><th>MAE (pts)</th><th>r</th></tr>" + cmp_ + "</table>")


def write_experiments_report(folder: str | Path, calibration_json: str | Path = "models/calibrator.json",
                             paired_json: str | Path = "models/paired_calibrator.json") -> Path:
    folder = Path(folder)
    parts, first = [], True
    for name, fn in (("effort", effort_section), ("movements", movements_section), ("rotation", rotation_section),
                     ("noise", noise_section), ("duration", duration_section)):
        r = _load(folder, name)
        if r is None:
            continue
        parts.append(fn(r, first) if name == "effort" else fn(r))
        first = False
    cal = Path(calibration_json)
    if cal.exists():
        parts.append(calibration_section(json.loads(cal.read_text(encoding="utf-8"))))
    if Path(paired_json).exists():
        parts.append(paired_section(json.loads(Path(paired_json).read_text(encoding="utf-8"))))
    preset = next((_load(folder, n) or {} for n in ("effort", "noise") if (folder / f"{n}.json").exists()), {}).get("preset", "?")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>emgmirror experiments</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1150px;margin:24px auto;padding:0 16px;color:#1d232b;background:#fafafa}}
table{{border-collapse:collapse;margin:10px 0}}td,th{{border:1px solid #ccd;padding:5px 9px;text-align:left;font-size:14px}}
.warn{{background:#fff3cd;border:1px solid #e0c35c;padding:8px 12px}}.note{{color:#555;font-size:14px}}h2{{margin-top:36px}}</style>
</head><body><h1>Synthetic experiments</h1>
<p class="warn">All results come from the motor-unit simulator (preset: {html.escape(str(preset))}). They show what to
expect and which methods work. They are not measurements of a person. Re-run after calibrating to real recordings.</p>
{''.join(parts)}</body></html>"""
    out = folder / "report.html"
    out.write_text(doc, encoding="utf-8")
    return out
