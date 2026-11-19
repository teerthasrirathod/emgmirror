"""Guided recording from the real EMG kit.

Walks through the protocol in the terminal, records one continuous block with
labelled trials and saves it into a session. Run once per arm for small kits
(sequential mode) or once with both arms wired (mirror mode).
"""

from __future__ import annotations

import sys
import time

import numpy as np

from . import protocol
from .acquisition.sources import Source
from .processing.filters import StreamingFilter
from .storage import Block, Channel, Session, Trial, new_session_id


class RecordingAborted(RuntimeError):
    """A safety condition stopped the recording (for example, mains power appeared)."""


def _pump(src: Source, seconds: float, chunks: list, filt: StreamingFilter, label: str, target: np.ndarray | None,
          fs: float) -> None:
    t0 = time.perf_counter()
    got = 0
    while (el := time.perf_counter() - t0) < seconds:
        time.sleep(0.05)
        reason = getattr(src, "abort_reason", None)
        if reason:
            raise RecordingAborted(reason)
        c = src.read()
        if len(c):
            chunks.append(c)
            got += len(c)
            _, env = filt.process(c)
            goal = target[min(int(el * fs), len(target) - 1)] if target is not None else 0.0
            # Real hardware: show the band-passed RMS at the ADC pin (mV), which is what was actually
            # measured; the electrode-referred value depends on a gain nobody has measured yet.
            gain = getattr(src, "display_gain", None)
            if gain:
                bars = "  ".join(f"{ch.name} {e * gain:5.0f}mV {'#' * min(int(e * gain / 20), 25):<25}"
                                 for ch, e in zip(src.channels, env))
            else:
                bars = "  ".join(f"{ch.name}:{'#' * min(int(e * 40), 20):<20}" for ch, e in zip(src.channels, env))
            sys.stdout.write(f"\r{label:<16} target {goal * 100:3.0f}%  {bars}")
            sys.stdout.flush()
    sys.stdout.write("\n")


def record_block(src: Source, cfg: dict, countdown: int = 3) -> Block:
    fs = src.fs
    p = cfg["processing"]
    filt = StreamingFilter(fs, len(src.channels), tuple(p["band_hz"]), cfg["acquisition"]["mains_hz"],
                           p["envelope_window_s"])
    gap = float(cfg.get("protocol_rest_between_s", 3))
    chunks: list[np.ndarray] = []
    trials: list[Trial] = []

    def n_recorded() -> int:
        return sum(len(c) for c in chunks)

    def mark(label: str, **fields) -> None:
        # With the common pipeline (Acquisition) the raw recording gets the trial boundaries at the
        # device's own sample index, so the raw layer is self-describing.
        fn = getattr(src, "mark", None)
        if fn is not None:
            fn(label, **fields)

    src.start()
    try:
        print("Settling... keep the arm relaxed.")
        _pump(src, 2.0, [], filt, "settle", None, fs)  # filter warm-up, not stored
        for step in protocol.steps(cfg):
            print(f"\nNEXT: {step.task}  ({step.duration_s:.0f} s)\n  {step.instruction}")
            _pump(src, max(gap - countdown, 0.5), chunks, filt, "rest", None, fs)
            for k in range(countdown, 0, -1):
                _pump(src, 1.0, chunks, filt, f"start in {k}", None, fs)
            start = n_recorded() / fs
            mark("trial_start", task=step.task, level=step.level)
            _pump(src, step.duration_s, chunks, filt, step.task, protocol.target_profile(step, fs), fs)
            mark("trial_end", task=step.task)
            trials.append(Trial(step.task, start, n_recorded() / fs, step.level))
        _pump(src, 1.0, chunks, filt, "rest", None, fs)
    except BaseException as exc:
        # Close an open raw recording as interrupted *before* stop() would close it as complete
        # (safety abort, error, Ctrl+C). Sources without a raw recorder have no stop_recording.
        stop_recording = getattr(src, "stop_recording", None)
        if stop_recording is not None:
            reason = getattr(src, "abort_reason", None) or f"{type(exc).__name__}: {exc}".rstrip(": ")
            stop_recording(reason, status="interrupted")
        raise
    finally:
        src.stop()
    data = np.vstack(chunks) if chunks else np.empty((0, len(src.channels)))
    return Block(channels=list(src.channels), trials=trials, data=data)


def channels_for(cfg: dict, arm: str | None) -> list[Channel]:
    chans = [Channel(**c) for c in cfg["channels"]]
    return [c for c in chans if c.arm == arm] if arm else chans


def save_block(block: Block, cfg: dict, data_dir, session_id: str | None, source: str, fs: float,
               provenance: dict | None = None) -> Session:
    if session_id and (data_dir / session_id / "session.json").exists():
        s = Session.load(data_dir / session_id)
        if s.fs != fs:
            raise ValueError(f"session {session_id} is at {s.fs} Hz, this block at {fs} Hz")
        s.blocks.append(block)
    else:
        s = Session(session_id=session_id or new_session_id("recording"), source=source, fs=fs,
                    blocks=[block], subject=cfg.get("subject", {}))
    if provenance:
        s.provenance.setdefault("blocks", {})[str(len(s.blocks) - 1)] = provenance
    s.save(data_dir)
    return s
