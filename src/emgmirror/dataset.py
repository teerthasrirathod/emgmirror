"""Dataset sessions: every EMG sample traceable to its setup and to the movement that produced it.

A session is one sitting with an unchanged setup::

    data/dataset/<subject>/<session_id>/
        setup.yaml        device identity (from the HELLO), pin map, electrode placement, pads, skin
                          prep, power state, room floor; confirmed by the person before recording
        floor.json        room noise floor from the floor check (rest), per channel
        trials.csv        one row per channel x trial: labels + floor-relative metrics + quality flags
        session.json      pointers to the raw recordings (data/raw/<id>) this session produced

    data/dataset/index.csv   all trials of all sessions (rebuilt from the session folders)

Raw data stays in the append-only raw layer (data/raw); a session only points to it.

Metrics are computed at the ADC pin, where the voltage is actually measured (the electrode-referred
value depends on a gain that has not been measured):
  clean     band-pass 75-450 Hz (4th-order Butterworth, zero-phase) + mains removal by regression
            (50 Hz and harmonics fitted per 0.5 s block), as established in docs/EXPERIMENTS.md
  floor     RMS of `clean` over the floor-check rest, per channel
  net_rms   sqrt(rms^2 - floor^2)        snr_db  20*log10(rms / floor)
  active    share of 250 ms windows above `active_threshold_x_floor` x floor (default 2x = 6 dB)
  quality   clipped share (ADC code 0 or full scale), missing samples, 50 Hz amplitude
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml
from scipy.signal import butter, sosfiltfilt, welch

from .processing.filters import remove_mains_regression
from .recording import RawRecording

FIELDS = ["session_id", "subject", "arm", "recording_id", "ch", "gpio", "header", "muscle_group", "site",
          "trial", "task", "target_effort", "rep", "start_index", "end_index", "duration_s",
          "rms_pin_mv", "floor_pin_mv", "net_rms_pin_mv", "snr_db", "active_fraction", "peak_250ms_x_floor",
          "median_hz", "clipped_fraction", "missing_samples", "mains50_pin_mv", "quality"]


# ---------------------------------------------------------------- signal
def clean_pin(pin_mv: np.ndarray, fs: float, mains_hz: float = 50.0, band=(75.0, 450.0)) -> np.ndarray:
    """(n, ch) pin mV -> cleaned (n, ch). NaNs (invalid samples) are bridged before filtering."""
    x = np.array(pin_mv, dtype=float)
    out = np.empty_like(x)
    sos = butter(4, list(band), "bandpass", fs=fs, output="sos")
    for c in range(x.shape[1]):
        col = x[:, c]
        bad = ~np.isfinite(col)
        if bad.any():
            good = np.flatnonzero(~bad)
            col = col.copy()
            col[bad] = np.interp(np.flatnonzero(bad), good, col[good]) if len(good) else 0.0
        y = sosfiltfilt(sos, col - np.median(col))
        out[:, c] = remove_mains_regression(y, fs, mains_hz, max_hz=band[1])
    return out


def _windows_rms(y: np.ndarray, fs: float, win_s: float = 0.25) -> np.ndarray:
    w = max(1, int(fs * win_s))
    n = len(y) // w
    return np.sqrt(np.mean(y[: n * w].reshape(n, w) ** 2, axis=1)) if n else np.array([])


def _mains50(raw_pin: np.ndarray, fs: float, mains_hz: float, block_s: float = 0.25) -> float:
    """RMS of the mains fundamental, fitted in short blocks.

    Real mains is not exactly `mains_hz` (49.9 Hz measured here on 2026-11-17). One fit over many
    seconds lets the phase drift through whole cycles and cancels itself out (a 0.9 V component
    read as 23 mV). Within 0.25 s a 0.1 Hz offset drifts only 9 degrees.
    """
    v = raw_pin[np.isfinite(raw_pin)]
    w = int(fs * block_s)
    if len(v) < w:
        return float("nan")
    t = np.arange(w) / fs
    basis = np.column_stack([np.sin(2 * np.pi * mains_hz * t), np.cos(2 * np.pi * mains_hz * t)])
    amps = []
    for i in range(0, len(v) - w + 1, w):
        seg = v[i:i + w]
        coef, *_ = np.linalg.lstsq(basis, seg - seg.mean(), rcond=None)
        amps.append(np.hypot(*coef) / np.sqrt(2))
    return float(np.median(amps))  # rms of the fitted mains fundamental


def trials_from_markers(rec: RawRecording) -> list[dict[str, Any]]:
    """Trial boundaries at device sample indices, exactly as the recorder marked them.

    `run` is the device run the trial was marked in (None for recordings made before markers carried
    it). The index restarts with every run, so start/end only identify samples together with the run;
    a trial whose start and end fall in different runs (device restarted mid-trial) is dropped.
    """
    out, open_ = [], {}
    for e in rec.events:
        if e.get("event") != "marker" or e.get("index") is None:
            continue
        if e["label"] == "trial_start":
            open_[e.get("task")] = e
        elif e["label"] == "trial_end" and e.get("task") in open_:
            s = open_.pop(e["task"])
            r0, r1 = s.get("run"), e.get("run")
            if r0 is not None and r1 is not None and int(r0) != int(r1):
                continue
            run = r0 if r0 is not None else r1
            out.append({"task": e["task"], "level": float(s.get("level", 0.0)), "start": int(s["index"]),
                        "end": int(e["index"]), "run": None if run is None else int(run)})
    return out


def record_positions(rec: RawRecording, fill_gaps: bool = True) -> np.ndarray:
    """Timeline position (in samples) of every record.

    fill_gaps=True: the signal viewer's timeline (viewer.analysis.TailReader): index - i0 within a run, and
    after a device restart (index not above the previous one) the new run continues right after the last
    position. fill_gaps=False: the record's place in the file, for signals built by stacking the records
    (to_pin_mv), which have no room for gaps.
    """
    idx = rec.records["index"].astype(np.int64)
    n = len(idx)
    if not fill_gaps or n == 0:
        return np.arange(n, dtype=np.int64)
    starts = np.flatnonzero(idx[1:] <= idx[:-1]) + 1
    pos = idx - idx[0]
    for a in starts:  # restarts are rare
        pos[a:] = idx[a:] - idx[a] + pos[a - 1] + 1
    return pos


def marker_position(rec: RawRecording, index: int, run: int | None, pos: np.ndarray) -> int:
    """Timeline position of a device sample index from a marker, using the marker's run.

    The device index restarts with every run, so an index only identifies a sample together with its
    run. Markers from recordings made before they carried the run (run None), or recordings without
    frames.csv, keep the old meaning: index minus the first index.
    """
    idx = rec.records["index"].astype(np.int64)
    if len(idx) == 0:
        return int(index)
    runs = rec.record_runs() if run is not None else None
    if runs is None:
        return int(index - idx[0])
    sel = np.flatnonzero(runs == int(run))
    if len(sel) == 0:
        return int(index - idx[0])
    ii = idx[sel]
    k = int(np.clip(np.searchsorted(ii, index), 0, len(ii) - 1))
    return int(pos[sel[k]] + (index - ii[k]))


def floor_from_recording(rec: RawRecording, mains_hz: float = 50.0, trim_s: float = 0.5) -> dict[str, Any]:
    """Room noise floor per channel from a rest recording (whole recording minus the edges)."""
    fs = rec.fs
    y = clean_pin(rec.to_pin_mv(), fs, mains_hz)
    k = int(trim_s * fs)
    seg = y[k: len(y) - k] if len(y) > 2 * k + fs else y
    chans = []
    for c in range(seg.shape[1]):
        w = _windows_rms(seg[:, c], fs)
        chans.append({"ch": c + 1, "floor_pin_mv": round(float(np.sqrt(np.mean(seg[:, c] ** 2))), 3),
                      "floor_250ms_p95_mv": round(float(np.percentile(w, 95)), 3) if len(w) else None})
    return {"recording_id": rec.manifest["recording_id"], "seconds": round(len(seg) / fs, 2), "channels": chans}


def analyse_recording(rec: RawRecording, floor: list[float], active_x: float = 2.0,
                      mains_hz: float = 50.0) -> list[dict[str, Any]]:
    """Per channel x trial metrics. `floor` = pin mV rms per channel from the floor check."""
    fs = rec.fs
    idx = rec.records["index"].astype(np.int64)
    pin = rec.to_pin_mv()
    raw = rec.records["raw"]
    os_ = float(rec.manifest["source"].get("device", {}).get("os", 1) or 1)
    y = clean_pin(pin, fs, mains_hz)
    runs = rec.record_runs()
    rows = []
    counts: dict[str, int] = {}
    for k, t in enumerate(trials_from_markers(rec)):
        counts[t["task"]] = counts.get(t["task"], 0) + 1
        m = (idx >= t["start"]) & (idx < t["end"])
        if runs is not None and t.get("run") is not None:
            m &= runs == t["run"]  # the same indices exist again in every other run
        pos = np.flatnonzero(m)
        if len(pos) < fs * 0.5:
            continue
        # skip 250 ms at each edge: reaction time and the cue itself
        e = int(0.25 * fs)
        core = pos[e: len(pos) - e] if len(pos) > 2 * e + fs * 0.25 else pos
        span = t["end"] - t["start"]
        missing = int(span - len(pos))
        for c in range(pin.shape[1]):
            seg = y[core, c]
            rms = float(np.sqrt(np.mean(seg ** 2)))
            fl = float(floor[c]) if c < len(floor) and floor[c] else float("nan")
            w = _windows_rms(seg, fs)
            f, pxx = welch(seg, fs=fs, nperseg=min(len(seg), int(fs // 4)))
            band = (f >= 20) & (f <= 450)
            cum = np.cumsum(pxx[band])
            r = raw[core, c].astype(float)
            clipped = float(np.mean((r <= 0) | ((r >= 4095 * os_) & (r != 0xFFFF)))) if raw.dtype.kind == "u" else 0.0
            quality = []
            if clipped > 0.05:
                quality.append("clipping")
            if missing:
                quality.append("missing")
            if not np.isfinite(fl):
                quality.append("no_floor")
            rows.append({"recording_id": rec.manifest["recording_id"], "ch": c + 1, "trial": k + 1, "task": t["task"],
                         "target_effort": t["level"], "rep": counts[t["task"]], "start_index": t["start"],
                         "end_index": t["end"], "duration_s": round(span / fs, 3),
                         "rms_pin_mv": round(rms, 3), "floor_pin_mv": round(fl, 3),
                         "net_rms_pin_mv": round(float(np.sqrt(max(rms ** 2 - fl ** 2, 0.0))), 3) if np.isfinite(fl) else None,
                         "snr_db": round(20 * np.log10(rms / fl), 2) if np.isfinite(fl) and fl > 0 else None,
                         "active_fraction": round(float(np.mean(w > active_x * fl)), 3) if len(w) and np.isfinite(fl) else None,
                         "peak_250ms_x_floor": round(float(w.max() / fl), 2) if len(w) and np.isfinite(fl) else None,
                         "median_hz": round(float(f[band][np.searchsorted(cum, cum[-1] / 2)]), 1) if cum.size and cum[-1] > 0 else None,
                         "clipped_fraction": round(clipped, 4), "missing_samples": missing,
                         "mains50_pin_mv": round(_mains50(pin[core, c], fs, mains_hz), 1),
                         "quality": ";".join(quality) or "ok"})
    return rows


# ---------------------------------------------------------------- setup
def default_setup(cfg: dict, subject: str, arm: str) -> dict[str, Any]:
    pl = cfg.get("placement", {})
    chans = []
    for k, (ch, hw) in enumerate(zip(cfg["channels"], cfg.get("hardware", {}).get("channels", []))):
        p = pl.get(f"ch{k + 1}", {})
        chans.append({"ch": k + 1, "name": ch["name"], "gpio": hw.get("gpio"), "header": hw.get("header"),
                      "muscle_group": p.get("muscle_group", ch.get("muscle")), "site": p.get("site", "?"),
                      "ref": p.get("ref", "?")})
    return {"subject": subject, "arm": arm, "channels": chans,
            "electrodes": {"pads": pl.get("pads", "?"), "skin_prep": pl.get("skin_prep", "?")},
            "photos": [], "notes": ""}


def confirm_setup(setup: dict, ask: Callable[[str], str]) -> dict:
    """Show the placement and let the person correct it. Empty answer = keep."""
    print("\nSETUP (Enter keeps a value; type to change it)")
    for c in setup["channels"]:
        print(f"  CH{c['ch']} GPIO{c['gpio']} ({c['header']}) -> {c['muscle_group']}")
        for key in ("site", "ref"):
            ans = ask(f"    {key} [{c[key]}]: ").strip()
            if ans:
                c[key] = ans
    for key in ("pads", "skin_prep"):
        ans = ask(f"  {key} [{setup['electrodes'][key]}]: ").strip()
        if ans:
            setup["electrodes"][key] = ans
    ans = ask("  photo file path (optional): ").strip().strip('"')
    if ans:
        setup["photos"].append(ans)
    ans = ask("  notes (optional): ").strip()
    if ans:
        setup["notes"] = ans
    return setup


def _write_rows(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def enrich(rows: list[dict], session: dict, setup: dict) -> list[dict]:
    by_ch = {c["ch"]: c for c in setup["channels"]}
    for r in rows:
        c = by_ch.get(r["ch"], {})
        r.update(session_id=session["session_id"], subject=setup["subject"], arm=setup["arm"], gpio=c.get("gpio"),
                 header=c.get("header"), muscle_group=c.get("muscle_group"), site=c.get("site"))
    return rows


def rebuild_index(root: Path) -> Path:
    rows = []
    for p in sorted(root.glob("*/*/trials.csv")):
        with open(p, newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    out = root / "index.csv"
    _write_rows(out, rows)
    return out


def analyse_session(folder: Path, raw_root: Path, active_x: float = 2.0, mains_hz: float = 50.0) -> list[dict]:
    session = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    setup = yaml.safe_load((folder / "setup.yaml").read_text(encoding="utf-8"))
    floor = json.loads((folder / "floor.json").read_text(encoding="utf-8"))
    fl = [c["floor_pin_mv"] for c in floor["channels"]]
    rows = []
    for rid in session["recordings"]:
        rows += analyse_recording(RawRecording.open(raw_root / rid), fl, active_x, mains_hz)
    rows = enrich(rows, session, setup)
    _write_rows(folder / "trials.csv", rows)
    rebuild_index(folder.parent.parent)
    return rows


def summary_table(rows: list[dict]) -> str:
    """Median per channel x task over repetitions."""
    from collections import defaultdict

    g: dict = defaultdict(list)
    for r in rows:
        g[(int(r["ch"]), r["task"], float(r["target_effort"]))].append(r)
    lines = [f"{'ch':<3}{'task':<14}{'effort':>7}{'reps':>5}{'SNR dB':>8}{'net mV':>8}{'active':>8}{'peak x':>8}"
             f"{'med Hz':>8}{'clip':>7}  quality"]
    for (ch, task, eff), rs in sorted(g.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][1])):
        med = lambda k: np.nanmedian([float(r[k]) if r[k] not in (None, "") else np.nan for r in rs])  # noqa: E731
        q = sorted({r["quality"] for r in rs})
        lines.append(f"{ch:<3}{task:<14}{eff:>7.2f}{len(rs):>5}{med('snr_db'):>8.1f}{med('net_rms_pin_mv'):>8.1f}"
                     f"{med('active_fraction'):>8.2f}{med('peak_250ms_x_floor'):>8.1f}{med('median_hz'):>8.0f}"
                     f"{med('clipped_fraction'):>7.3f}  {','.join(q)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- run
def run_session(cfg: dict, source_spec: str, subject: str, arm: str, dataset_root: Path, raw_root: Path,
                data_dir: Path | None = None, ask: Callable[[str], str] = input, confirm: bool = True,
                countdown: int = 3, floor_s: float = 10.0) -> Path:
    from .acquisition import safety
    from .acquisition.pipeline import Acquisition
    from .acquisition.registry import open_source
    from .record import RecordingAborted, record_block
    from .recording import RawRecorder
    from .storage import Channel

    session_id = time.strftime("%Y%m%d-%H%M%S") + "_grips"
    folder = dataset_root / subject / session_id
    folder.mkdir(parents=True, exist_ok=False)
    setup = default_setup(cfg, subject, arm)
    if confirm:
        setup = confirm_setup(setup, ask)

    src = open_source(source_spec, cfg, data_dir)
    src.info.channels[:] = [Channel(c.name, arm, c.muscle) for c in src.info.channels]
    acq = Acquisition(src, cfg)
    if src.info.is_real_hardware:
        problems = safety.check_before_person_recording()
        if problems:
            raise SystemExit("BLOCKED: " + " ".join(problems))
    acq.start()
    snap = {"acquisition": cfg["acquisition"], "hardware": cfg.get("hardware"), "placement": setup}
    session = {"session_id": session_id, "subject": subject, "arm": arm, "source": source_spec, "recordings": [],
               "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    setup.update(session_id=session_id, device={k: src.info.device.get(k) for k in
                                                ("fw", "chip", "rev", "mac", "pins", "fs", "os", "cal", "cal_a", "cal_b", "port")},
                 is_real_hardware=src.info.is_real_hardware,
                 power_on_battery=(safety.ac_power_connected() is False) if src.info.is_real_hardware else None)
    try:
        if src.info.is_real_hardware:
            safety.start_power_watch(acq)  # mains appearing mid-session aborts it (floor check and grips)
        # 1. floor check: rest, no cues
        print(f"\nFLOOR CHECK: relax the forearm completely and keep still for {floor_s:.0f} s ...")
        rec = RawRecorder(raw_root, src.info, label=f"{session_id}_floor",
                          metadata={"label": "floor", "session_id": session_id, "arm": arm}, config_snapshot=snap)
        acq.start_recording(rec)
        t0 = time.time()
        while time.time() - t0 < floor_s:
            if getattr(acq, "abort_reason", None):
                raise RecordingAborted(acq.abort_reason)
            acq.read()
            time.sleep(0.05)
        acq.stop_recording("floor check done")
        session["recordings"].append(rec.recording_id)
        floor = floor_from_recording(RawRecording.open(rec.dir), cfg["acquisition"].get("mains_hz", 50))
        ref = cfg.get("room_noise_floor", {}).get("pin_mv_rms", {})
        for c in floor["channels"]:
            r = ref.get(f"ch{c['ch']}")
            c["reference_pin_mv"] = r
            c["x_reference"] = None if not r else round(c["floor_pin_mv"] / r, 2)
        (folder / "floor.json").write_text(json.dumps(floor, indent=2), encoding="utf-8")
        setup["room_floor_pin_mv"] = {f"ch{c['ch']}": c["floor_pin_mv"] for c in floor["channels"]}
        print("  floor (pin mV rms): " + "  ".join(
            f"CH{c['ch']} {c['floor_pin_mv']:.1f}" + (f" ({c['x_reference']}x reference)" if c["x_reference"] else "")
            for c in floor["channels"]))
        bad = [c for c in floor["channels"] if c["x_reference"] and c["x_reference"] > 2.0]
        if bad and confirm:
            ans = ask("  Floor is more than 2x the room reference on " + ", ".join(f"CH{c['ch']}" for c in bad)
                      + " (check pad contact). Continue anyway? [y/N]: ")
            if ans.strip().lower() != "y":
                raise SystemExit("stopped after the floor check")

        # 2. graded grips with cues
        rec = RawRecorder(raw_root, src.info, label=f"{session_id}_grips",
                          metadata={"label": "grips", "session_id": session_id, "arm": arm}, config_snapshot=snap)
        acq.start_recording(rec)
        if src.info.is_real_hardware:
            acq.display_gain = float(cfg["acquisition"].get("amp_gain", 1.0))
        record_block(acq, cfg, countdown=countdown)
        session["recordings"].append(rec.recording_id)
    except BaseException as exc:
        # An open raw recording ends as interrupted, never as complete (safety abort, error, Ctrl+C).
        reason = getattr(acq, "abort_reason", None) or f"{type(exc).__name__}: {exc}".rstrip(": ")
        acq.stop_recording(reason, status="interrupted")
        if isinstance(exc, RecordingAborted):
            raise SystemExit(f"STOPPED: {reason}. Disconnect the electrodes. Session folder: {folder}") from exc
        raise
    finally:
        acq.stop()
        (folder / "setup.yaml").write_text(yaml.safe_dump(setup, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (folder / "session.json").write_text(json.dumps(session, indent=2), encoding="utf-8")
    rows = analyse_session(folder, raw_root, float(cfg.get("room_noise_floor", {}).get("active_threshold_x_floor", 2.0)),
                           cfg["acquisition"].get("mains_hz", 50))
    print("\n" + summary_table([r for r in rows if r["task"] != "rest"] + [r for r in rows if r["task"] == "rest"]))
    print(f"\nsession: {folder}")
    return folder
