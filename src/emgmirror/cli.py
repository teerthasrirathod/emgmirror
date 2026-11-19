"""Command line entry point: `emgmirror <command>`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DATA_DIR, load_config


def cmd_simulate(a, cfg):
    from .simulate.synthetic import synthetic_session

    if a.seed is not None:
        cfg["simulation"]["seed"] = a.seed
    s = synthetic_session(cfg, mode=a.mode)
    folder = s.save(a.data)
    print(f"saved {folder}")


def cmd_analyze(a, cfg):
    from .analysis.bilateral import analyze
    from .report import write_report
    from .storage import Session

    folder = Path(a.data) / a.session if not Path(a.session).exists() else Path(a.session)
    s = Session.load(folder)
    res = analyze(s, cfg)
    (folder / "analysis.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    out = write_report(s, cfg, res, folder / "report.html")
    for m, v in res.get("summary", {}).items():
        act = "-" if v["activation_pct"] is None else f"{v['activation_pct']:.0f}%"
        mu = "-" if v["mu_firing_pct"] is None else f"{v['mu_firing_pct']:.0f}%"
        print(f"{m:<12} activation {act:>5} | firing motor units (experimental) {mu:>5}")
    print(f"report: {out}")


def cmd_validate(a, cfg):
    from .analysis import validate

    res = validate.run(cfg, seeds=a.seeds, progress=lambda s: print(s, flush=True))
    print(validate.format_table(res))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=2), encoding="utf-8")


def cmd_experiment(a, cfg):
    import time

    from .experiments import suite
    from .experiments.report import write_experiments_report

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    names = list(suite.EXPERIMENTS) if a.name == "all" else [a.name]
    for name in names:
        t0 = time.time()
        res = suite.EXPERIMENTS[name](cfg, progress=lambda s: print(s, flush=True), **suite.PRESETS[a.preset][name])
        res["preset"] = a.preset
        (out / f"{name}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"== {name}: {time.time() - t0:.0f} s", flush=True)
    print(f"report: {write_experiments_report(out)}")


def _base_frontend(cfg) -> dict:
    from .simulate.frontend import FrontEnd

    acq, sim = cfg["acquisition"], cfg["simulation"]
    return FrontEnd(fs_out=acq["fs"], amp_noise_uv=sim["noise_uv_rms"], mains_uv=sim["mains_uv"],
                    mains_hz=acq["mains_hz"], adc_bits=acq["adc_bits"], adc_vref=acq["adc_vref"],
                    gain=acq["amp_gain"]).__dict__


def _model_path(p: str, must_exist: bool = False) -> Path:
    """Relative model paths are relative to the repository (like data/raw), not the current folder.
    An existing file relative to the current folder is still accepted when reading."""
    from .config import REPO_ROOT

    path = Path(p)
    if path.is_absolute() or (must_exist and path.exists()):
        return path
    return REPO_ROOT / path


def cmd_calibrate(a, cfg):
    from .calibration import engine

    a.model = str(_model_path(a.model, must_exist=a.action == "predict"))
    a.paired_model = str(_model_path(a.paired_model, must_exist=a.action == "predict"))
    if a.action == "predict":
        if not a.session:
            raise SystemExit("give a session id or folder: emgmirror calibrate predict <session>")
        if not Path(a.model).exists():
            raise SystemExit(f"no calibration model at {a.model}: run `emgmirror calibrate train` first "
                             "(or pass --model)")

    if a.action == "train":
        p = lambda s: print(s, flush=True)  # noqa: E731
        fe = _base_frontend(cfg)
        cal, bank = engine.train(fe, n=a.n, n_holdout=max(a.n // 5, 20), jobs=a.jobs, progress=p)
        cal.save(a.model)
        paired = engine.paired_test(cal, fe, n_pairs=a.pairs, progress=p)
        engine.save_json({"holdout": cal.report, "paired": paired, "n_train": a.n, "frontend": fe},
                         Path(a.model).with_suffix(".json"))
        for k, v in cal.report.items():
            print(f"{k:<16} R2={v['r2']:+.2f}  MAE={v['mae']:.3f}  80%-interval coverage={v['coverage_80']:.0%}")
        for k, v in paired["scores"].items():
            print(f"L/R unit ratio via {k:<20} MAE={v['mae_pct_points']:.1f} pts  r={v['corr']:.2f}")
        return

    if a.action == "train-paired":
        p = lambda s: print(s, flush=True)  # noqa: E731
        fe = _base_frontend(cfg)
        cal, _ = engine.train_paired(fe, n=a.n, n_holdout=max(a.n // 5, 20), jobs=a.jobs, progress=p)
        cal.save(a.paired_model)
        engine.save_json({"holdout": cal.report, "n_train": a.n, "frontend": fe}, Path(a.paired_model).with_suffix(".json"))
        for k, v in cal.report.items():
            print(k, {kk: round(vv, 3) for kk, vv in v.items()})
        return

    from .storage import Session

    cal = engine.Calibrator.load(a.model)
    folder = Path(a.data) / a.session if not Path(a.session).exists() else Path(a.session)
    s = Session.load(folder)
    feats = engine.session_features(s, cfg)
    if not feats:
        print("session lacks the calibration tasks: " + ", ".join(engine.TASKS))
        return
    preds = dict(zip(feats, cal.predict(list(feats.values()))))
    result = {"predictions": preds, "model_holdout": cal.report}
    for block in s.blocks:
        rest = next((t for t in block.trials if t.task == "rest"), None)
        if rest:
            a0, b0 = int(rest.start_s * s.fs), int(rest.end_s * s.fs)
            result.setdefault("frontend_from_rest", {})
            for c, ch in enumerate(block.channels):
                result["frontend_from_rest"][f"{ch.arm}/{ch.muscle}"] = engine.frontend_from_rest(
                    block.data[a0:b0, c], s.fs, cfg["acquisition"]["mains_hz"])
    for key, p in preds.items():
        m = p["mu_fraction"]
        print(f"{key:<20} surviving units {100 * m['estimate']:.0f}% ({100 * m['low']:.0f}-{100 * m['high']:.0f}%)  "
              f"unit size x{p['enlargement']['estimate']:.2f}  drive {100 * p['drive_max']['estimate']:.0f}%")
    paired = engine.PairedCalibrator.load(a.paired_model) if Path(a.paired_model).exists() else None
    for muscle in sorted({k.split("/")[1] for k in preds}):
        fl, fr = feats.get(f"left/{muscle}"), feats.get(f"right/{muscle}")
        if fl and fr and paired:
            m = paired.predict_pairs([(fl, fr)])[0]["mu_fraction"]
            result.setdefault("paired", {})[muscle] = m
            print(f"{muscle}: left has ~{100 * m['estimate']:.0f}% ({100 * m['low']:.0f}-{100 * m['high']:.0f}%) "
                  "of the right arm's motor units (paired engine, experimental)")
    engine.save_json(result, folder / "calibration.json")


def cmd_serve(a, cfg):
    import uvicorn

    from .server.app import create_app

    print(f"dashboard: http://{a.host}:{a.port}")
    uvicorn.run(create_app(a.config, a.data, raw_dir=_raw_dir(a, cfg)), host=a.host, port=a.port,
                log_level="warning")


def cmd_view(a, cfg):
    import webbrowser

    from .config import REPO_ROOT as ROOT
    from .viewer.server import serve

    scene = Path(a.scene) if a.scene else ROOT / "results" / "ct" / "emg" / "scene.json"
    dataset_root = Path(a.dataset) if a.dataset else ROOT / "data" / "dataset"
    httpd = serve(_raw_dir(a, cfg), cfg, dataset_root, scene if scene.exists() else None, port=a.port)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"signal viewer: {url}  (this computer only; personal health data stays local)")
    if not scene.exists():
        print("no CT scene yet: build it with `emgmirror ct emg-overlay --session ...` (signals still work)")
    if a.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def cmd_ports(a, cfg):
    from .acquisition.sources import list_serial_ports

    ports = list_serial_ports()
    print("\n".join(ports) if ports else "no serial ports found")


def _raw_dir(a, cfg) -> Path:
    from .config import REPO_ROOT

    p = Path(a.raw) if getattr(a, "raw", None) else Path(cfg.get("raw_dir", "data/raw"))
    return p if p.is_absolute() else REPO_ROOT / p


def _open_acquisition(a, cfg):
    from .acquisition.pipeline import Acquisition
    from .acquisition.registry import open_source

    spec = a.source or a.port
    if not spec:
        raise SystemExit("give --source (e.g. esp32:COM9, emulator, virtual) or --port")
    return Acquisition(open_source(spec, cfg, a.data), cfg)


def cmd_record(a, cfg):
    from .record import channels_for, record_block, save_block
    from .recording import RawRecorder

    from .acquisition import safety

    acq = _open_acquisition(a, cfg)
    if acq.info.is_real_hardware:
        problems = safety.check_before_person_recording()
        if problems:
            for p in problems:
                print(f"BLOCKED: {p}")
            raise SystemExit(3)
    if a.arm:
        from .storage import Channel

        acq.src.info.channels[:] = [Channel(c.name, a.arm, c.muscle) for c in channels_for(cfg, None)][
            : len(acq.channels)]
    print(f"Recording {len(acq.channels)} channel(s): {', '.join(c.name for c in acq.channels)} from {acq.info.label}")
    if acq.info.is_real_hardware:
        print("SAFETY: electrodes only within the conditions in docs/ELECTRICAL_SAFETY.md (battery only, nothing "
              "else plugged in, cleared arm). Power status checked: on battery.")
    acq.start()
    rec = RawRecorder(_raw_dir(a, cfg), acq.info, label=a.session or "record",
                      metadata={"label": a.session or "", "arm": a.arm, "mode": "guided protocol"},
                      config_snapshot={"acquisition": cfg["acquisition"], "hardware": cfg.get("hardware")})
    acq.start_recording(rec)
    if acq.info.is_real_hardware:
        acq.display_gain = float(cfg["acquisition"].get("amp_gain", 1.0))  # live bars in mV at the ADC pin
    try:
        if acq.info.is_real_hardware:
            safety.start_power_watch(acq)  # mains appearing mid-recording aborts it
        block = record_block(acq, cfg)
    except Exception as exc:
        reason = getattr(acq, "abort_reason", None) or f"{type(exc).__name__}: {exc}"
        acq.stop_recording(reason, status="interrupted")
        acq.stop()
        print(f"\nSTOPPED: {reason}. Disconnect the electrodes. Raw recording kept (interrupted): {rec.dir}")
        raise SystemExit(4)
    finally:
        acq.stop()
    if len(block.data) == 0:
        st = acq.src.status()
        why = st.get("error") or (f"{st['malformed_lines']} malformed lines" if st.get("malformed_lines") else
                                  "the device sent no samples")
        raise SystemExit(f"no samples arrived ({why}); nothing saved to the session. Raw recording: {rec.dir}")
    prov = {"raw_recording": rec.recording_id, "source": acq.info.to_dict(), "fills": acq.fills,
            "raw_summary": rec.summary()}
    s = save_block(block, cfg, Path(a.data), a.session, acq.info.kind, acq.fs, provenance=prov)
    print(f"raw recording: {rec.dir}")
    print(f"saved block {len(s.blocks) - 1} to session {s.session_id}")
    missing = sum(f["missing"] for f in acq.fills)
    if missing:
        print(f"warning: {missing} samples were missing and filled with 0 mV (listed in the session provenance)")


def _print_report(r):
    src = r["source"]
    print(f"source: {src['label']}  (kind {src['kind']}, real hardware: {src['is_real_hardware']})")
    for name, st in r["stages"].items():
        print(f"  {name:<12} {st['state']:<13} {st['detail']}")
    rt = r["rates"]
    print(f"  rate: nominal {rt['nominal_hz']} Hz | device clock {rt['device_clock_hz']} Hz | host clock "
          f"{rt['host_window_hz']} Hz")
    c = r["counts"]
    print(f"  samples {c['samples']} | missing {c['missing']} {c['missing_by_cause'] or ''} | invalid {c['invalid']}")
    for ch in r["channels"]:
        stats = "" if "mean" not in ch else (f"mean {ch['mean']:.1f} min {ch['min']:.1f} max {ch['max']:.1f} "
                                             f"p2p {ch['p2p']:.1f} {ch['units']}")
        print(f"  CH{ch['ch']} gpio{ch.get('gpio')} {ch['name']:<6} {ch['state']:<8} {stats}")
        print(f"      {ch['detail']}")


def cmd_scan(a, cfg):
    from .acquisition import pinscan

    spec = a.source or a.port or ""
    kind, _, port = spec.partition(":")
    pins = [int(p) for p in a.pins.split(",")]
    factory = None
    if kind == "emulator":
        from .acquisition.emulator import EmulatorConfig, Esp32Emulator

        modes = dict(zip([32, 33, 34, 35, 36, 39], (port or "floating,floating,dc150,floating,dc2150,dc750").split(",")))
        factory = lambda: Esp32Emulator(EmulatorConfig(pins=tuple(pins), modes=tuple(modes.get(g, "floating") for g in pins),  # noqa: E731
                                                       pin_modes=modes))
        port = "emulator"
    elif kind != "esp32":
        raise SystemExit("scan needs an EMGM/2 device: --source esp32:COM9 (or emulator[:modes])")
    print(f"scanning GPIO {pins} on {port}: {a.seconds:g} s per order, {a.rate or 1000} Hz")
    res = pinscan.scan(port, cfg["acquisition"], pins, seconds=a.seconds, rate=a.rate or 1000, factory=factory)
    print(pinscan.format_table(res))
    if a.out:
        import datetime

        res["date"] = datetime.datetime.now().isoformat(timespec="seconds")
        res["source_spec"] = spec
        res["label"] = a.label
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
        print(f"report: {a.out}")


def cmd_acq(a, cfg):
    import time

    from .recording import RawRecorder

    from .acquisition.esp32 import HandshakeError

    if a.action == "scan":
        return cmd_scan(a, cfg)

    acq = _open_acquisition(a, cfg)
    try:
        acq.start()
    except Exception as exc:
        opened = isinstance(exc, HandshakeError)
        report = {"source": {"label": acq.info.label, "kind": acq.info.kind,
                             "is_real_hardware": acq.info.is_real_hardware},
                  "stages": {"connection": {"state": "ok" if opened else "fail",
                                            "detail": "port opened" if opened else str(exc)},
                             "device": {"state": "fail" if opened else "waiting", "detail": str(exc) if opened else
                                        "not reached"}},
                  "error": f"{type(exc).__name__}: {exc}"}
        for name, st in report["stages"].items():
            print(f"  {name:<12} {st['state']:<8} {st['detail']}")
        if a.out:
            import datetime

            report["probe"] = {"source_spec": a.source or a.port,
                               "date": datetime.datetime.now().isoformat(timespec="seconds")}
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            print(f"report: {a.out}")
        raise SystemExit(2)
    rec = None
    try:
        if a.rate:
            acq.src.send_command(f"RATE {a.rate}")
            time.sleep(0.5)
        if a.action == "record":
            rec = RawRecorder(_raw_dir(a, cfg), acq.info, label=a.label or "free-run",
                              metadata={"label": a.label or "", "arm": a.arm, "mode": "free run"},
                              config_snapshot={"acquisition": cfg["acquisition"], "hardware": cfg.get("hardware")})
            acq.start_recording(rec)
            print(f"recording to {rec.dir}  (Ctrl+C to stop)")
        t0 = time.time()
        last = 0.0
        try:
            while a.seconds <= 0 or time.time() - t0 < a.seconds:
                time.sleep(0.05)
                acq.read()
                if a.action == "record" and time.time() - last > 2:
                    last = time.time()
                    r = acq.report()
                    print(f"\r{r['counts']['samples']} samples, missing {r['counts']['missing']}, "
                          f"{r['stages']['acquisition']['state']}", end="", flush=True)
        except KeyboardInterrupt:
            pass
        report = acq.report()
    finally:
        acq.stop()
    print()
    _print_report(report)
    if rec is not None:
        print(f"raw recording: {rec.dir} ({rec.summary()['samples']} samples)")
    if a.out:
        import datetime

        report["probe"] = {"seconds": a.seconds, "source_spec": a.source or a.port,
                           "date": datetime.datetime.now().isoformat(timespec="seconds"),
                           "device": acq.info.device, "calibration": acq.info.calibration}
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"report: {a.out}")


def cmd_raw(a, cfg):
    from .config import REPO_ROOT
    from .recording import RawRecording, list_recordings
    from .recording.derive import to_session, validate

    root = _raw_dir(a, cfg)
    if a.action == "list":
        for r in list_recordings(root):
            print(f"{r['recording_id']:<40} {r['status']:<12} {r['source']:<15} {r['samples']} samples  {r['label']}")
        return
    if not a.recording:
        raise SystemExit("give a recording id")
    folder = Path(a.recording) if Path(a.recording).exists() else root / a.recording
    rec = RawRecording.open(folder)
    if a.action == "inspect":
        integ = validate(rec, REPO_ROOT / "data" / "derived", cfg.get("diagnostics", {}))
        show = {k: v for k, v in integ.items() if k not in ("markers", "conversion")}
        print(json.dumps(show, indent=2, default=str))
        print(f"derived: {REPO_ROOT / 'data' / 'derived' / rec.manifest['recording_id']}")
    elif a.action == "to-session":
        sid = a.session or f"{rec.manifest['recording_id']}_session"
        s = to_session(rec, sid, arm=a.arm, allow_gaps=a.allow_gaps)
        s.subject = cfg.get("subject", {})
        out = s.save(a.data)
        print(f"session {sid}: {len(s.blocks)} block(s), {sum(len(b.trials) for b in s.blocks)} trial(s) -> {out}")


def cmd_dataset(a, cfg):
    from . import dataset
    from .config import REPO_ROOT

    root = Path(a.root) if a.root else REPO_ROOT / "data" / "dataset"
    raw_root = _raw_dir(a, cfg)
    if a.action == "run":
        if not a.source:
            raise SystemExit("give --source, e.g. esp32:COM7")
        dataset.run_session(cfg, a.source, a.subject, a.arm, root, raw_root, Path(a.data), confirm=not a.yes)
    elif a.action == "analyze":
        if not a.session:
            raise SystemExit("give the session folder")
        rows = dataset.analyse_session(Path(a.session), raw_root,
                                       float(cfg.get("room_noise_floor", {}).get("active_threshold_x_floor", 2.0)),
                                       cfg["acquisition"].get("mains_hz", 50))
        print(dataset.summary_table(rows))
    elif a.action == "index":
        print(dataset.rebuild_index(root))


def cmd_ct(a, cfg):
    from .config import REPO_ROOT as ROOT

    if a.action == "emg-overlay":
        from .ct import emg_bridge

        if not a.session:
            raise SystemExit("give --session data/dataset/<subject>/<session_id>")
        meshes = Path(a.meshes) if a.meshes else ROOT / "results" / "ct" / "meshes"
        out = Path(a.out) if a.out else ROOT / "results" / "ct" / "emg"
        s = emg_bridge.build(meshes, Path(a.session), _raw_dir(a, cfg), out, ct_arm=a.ct_arm, emg_arm=a.emg_arm)
        for site in s["sites"]:
            print(f"CH{site['ch']} {site['muscle_group']:<10} fat {site.get('fat_mm')} mm, muscle {site.get('muscle_mm')} mm, "
                  f"skin to bone {site.get('skin_to_bone_mm')} mm")
        print(("MIRRORED: " if s["mirrored"] else "") + f"{a.emg_arm}-arm EMG on {a.ct_arm}-arm CT")
        print(f"CT scene: {out / 'scene.json'} (local; personal health data, do not publish)")
        print("open it with: emgmirror view   (signals + CT, this computer only)")
        return
    if a.action == "serve":
        import uvicorn

        from .ct.webapp import create_ct_app

        print(f"open http://127.0.0.1:{a.port}/ct  (this computer only; nothing is sent to the internet)")
        uvicorn.run(create_ct_app(ROOT / "results" / "ct_jobs"), host="127.0.0.1", port=a.port, log_level="warning")
        return
    from .ct import pipeline

    src = Path(a.input) if a.input else ROOT / "01_clinical_CT"
    out = Path(a.out) if a.out else ROOT / "results" / "ct"
    if src.is_file() and src.name.upper() == "DICOMDIR":  # the images sit beside (or below) it
        src = src.parent
    if not src.is_dir():
        raise SystemExit(f"CT folder not found: {src}")
    res = pipeline.build(src, out, coarse_checks=not a.fast, sweep=not a.fast)
    print(pipeline.headline(res))
    print(f"results: {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="emgmirror", description=__doc__)
    p.add_argument("--config", default=None, help="YAML file merged over configs/default.yaml")
    p.add_argument("--data", default=str(DATA_DIR), help="sessions folder")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("simulate", help="create a synthetic session")
    s.add_argument("--mode", choices=["mirror", "sequential"], default="mirror")
    s.add_argument("--seed", type=int, default=None)
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("analyze", help="analyse a session and write report.html")
    s.add_argument("session", help="session id or folder")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("validate", help="check estimators against simulator ground truth")
    s.add_argument("--seeds", type=int, nargs="+", default=[7])
    s.add_argument("--out", default=None, help="write full results as JSON")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("experiment", help="run synthetic experiments (effort, movements, rotation, noise, duration)")
    s.add_argument("name", choices=["all", "effort", "movements", "rotation", "noise", "duration"])
    s.add_argument("--preset", choices=["quick", "full"], default="quick")
    s.add_argument("--out", default="results/experiments")
    s.set_defaults(fn=cmd_experiment)

    s = sub.add_parser("calibrate", help="fit the simulator to recordings (train on synthetic, predict on a session)")
    s.add_argument("action", choices=["train", "train-paired", "predict"])
    s.add_argument("session", nargs="?", help="session id or folder (predict)")
    s.add_argument("--n", type=int, default=300, help="training recordings to simulate")
    s.add_argument("--jobs", type=int, default=1, help="parallel processes (use your CPU core count)")
    s.add_argument("--pairs", type=int, default=12, help="left/right pairs for the paired test")
    s.add_argument("--model", default="models/calibrator.joblib")
    s.add_argument("--paired-model", dest="paired_model", default="models/paired_calibrator.joblib")
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("serve", help="start the dashboard backend")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--raw", default=None, help="raw recordings folder (default: config raw_dir)")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("view", help="signal viewer: recordings (complete or live) on a zoomable timeline + CT forearm")
    s.add_argument("--port", type=int, default=8030)
    s.add_argument("--raw", default=None, help="raw recordings folder (default: config raw_dir)")
    s.add_argument("--dataset", default=None, help="dataset root (default: data/dataset)")
    s.add_argument("--scene", default=None, help="CT scene JSON (default: results/ct/emg/scene.json)")
    s.add_argument("--open", action="store_true", help="open the browser")
    s.set_defaults(fn=cmd_view)

    s = sub.add_parser("ports", help="list serial ports")
    s.set_defaults(fn=cmd_ports)

    s = sub.add_parser("record", help="guided protocol recording (raw recording + session block)")
    s.add_argument("--source", default=None, help="esp32:COM9 | text:COM9 | emulator | virtual | replay:<id>")
    s.add_argument("--port", default=None, help="bare serial port; protocol from config acquisition.protocol")
    s.add_argument("--arm", choices=["left", "right"], default=None,
                   help="sequential mode: which arm the electrodes are on (omit for mirror mode)")
    s.add_argument("--session", default=None, help="append to this session (record right, then left)")
    s.add_argument("--raw", default=None, help="raw recordings folder (default: config raw_dir)")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("acq", help="acquisition: probe a device or make a free-running raw recording")
    s.add_argument("action", choices=["probe", "record", "scan"],
                   help="probe: stages and stream check; record: free-running raw recording; scan: which "
                        "ADC pins are driven vs floating")
    s.add_argument("--pins", default="32,33,34,35,36,39", help="scan: ADC1 GPIOs to test")
    s.add_argument("--source", default=None, help="esp32:COM9 | text:COM9 | emulator[:modes] | virtual")
    s.add_argument("--port", default=None, help="bare serial port; protocol from config acquisition.protocol")
    s.add_argument("--seconds", type=float, default=5.0, help="duration (0 = until Ctrl+C)")
    s.add_argument("--rate", type=int, default=None, help="ask an EMGM/2 device for this output rate first")
    s.add_argument("--label", default=None)
    s.add_argument("--arm", choices=["left", "right"], default=None)
    s.add_argument("--out", default=None, help="write the final diagnostic report as JSON")
    s.add_argument("--raw", default=None, help="raw recordings folder (default: config raw_dir)")
    s.set_defaults(fn=cmd_acq)

    s = sub.add_parser("raw", help="raw recordings: list, inspect (integrity + validated layer), to-session")
    s.add_argument("action", choices=["list", "inspect", "to-session"])
    s.add_argument("recording", nargs="?", help="recording id or folder")
    s.add_argument("--session", default=None, help="session id for to-session")
    s.add_argument("--arm", choices=["left", "right"], default=None)
    s.add_argument("--allow-gaps", dest="allow_gaps", action="store_true",
                   help="fill missing samples with 0 mV (each fill is listed in the session provenance)")
    s.add_argument("--raw", default=None, help="raw recordings folder (default: config raw_dir)")
    s.set_defaults(fn=cmd_raw)

    s = sub.add_parser("dataset", help="dataset sessions: setup + floor check + cued protocol + per-trial analysis")
    s.add_argument("action", choices=["run", "analyze", "index"])
    s.add_argument("session", nargs="?", help="session folder (analyze)")
    s.add_argument("--source", default=None, help="esp32:COM7 | emulator | ...")
    s.add_argument("--subject", default="self")
    s.add_argument("--arm", choices=["left", "right"], default="right")
    s.add_argument("--root", default=None, help="dataset folder (default data/dataset)")
    s.add_argument("--raw", default=None, help="raw recordings folder (default: config raw_dir)")
    s.add_argument("--yes", action="store_true", help="accept the placement from the config without prompts")
    s.set_defaults(fn=cmd_dataset)

    s = sub.add_parser("ct", help="CT scan to 3D models (research use, not for diagnosis or surgical planning)")
    s.add_argument("action", choices=["build", "serve", "emg-overlay"],
                   help="build: 01_clinical_CT -> results/ct; serve: local page to load any CT scan; "
                        "emg-overlay: electrode sites, CT depths and a session replay on the CT forearm")
    s.add_argument("--session", default=None, help="emg-overlay: dataset session folder")
    s.add_argument("--meshes", default=None, help="emg-overlay: CT mesh folder (default results/ct/meshes)")
    s.add_argument("--ct-arm", dest="ct_arm", choices=["left", "right"], default="left")
    s.add_argument("--emg-arm", dest="emg_arm", choices=["left", "right"], default="right")
    s.add_argument("--input", default=None, help="DICOM folder (default: 01_clinical_CT)")
    s.add_argument("--out", default=None, help="output folder (default: results/ct)")
    s.add_argument("--fast", action="store_true", help="skip the validation runs (coarse series, threshold sweep)")
    s.add_argument("--port", type=int, default=8010, help="port for `ct serve` (bound to 127.0.0.1)")
    s.set_defaults(fn=cmd_ct)

    a = p.parse_args(argv)
    a.fn(a, load_config(a.config))


if __name__ == "__main__":
    main()
