"""Recording protocol: the sequence of tasks and their target-effort profiles.

Shared by the simulator (as the descending drive) and the recorder (as the
on-screen target), so synthetic and real sessions have identical structure.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .storage import Trial


@dataclass
class Step:
    task: str
    kind: str  # hold | ramp | reps
    level: float
    duration_s: float
    instruction: str
    reps: int = 0


def steps(cfg: dict) -> list[Step]:
    return [
        Step(
            task=s["task"],
            kind=s["kind"],
            level=float(s["level"]),
            duration_s=float(s["duration_s"]),
            instruction=s.get("instruction", ""),
            reps=int(s.get("reps", 0)),
        )
        for s in cfg["protocol"]
    ]


def target_profile(step: Step, fs: float) -> np.ndarray:
    """Target effort (0..1) over the step, with short smooth on/off edges."""
    n = int(round(step.duration_s * fs))
    t = np.arange(n) / fs
    if step.kind == "hold":
        y = np.full(n, step.level)
    elif step.kind == "ramp":
        y = step.level * t / step.duration_s
    elif step.kind == "reps":
        period = step.duration_s / max(step.reps, 1)
        phase = (t % period) / period
        y = step.level * (phase < 0.5).astype(float)
    else:
        raise ValueError(f"unknown protocol kind: {step.kind}")
    edge = min(int(0.25 * fs), n // 4)
    if edge > 0 and step.kind != "ramp":
        win = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, edge))
        y[:edge] *= win
        y[-edge:] *= win[::-1]
    return y


def build_timeline(cfg: dict, fs: float) -> tuple[np.ndarray, list[Trial], dict[str, np.ndarray]]:
    """Concatenate the whole protocol.

    Returns the overall target effort, trial labels, and a per-task mask so the
    simulator can weight muscles by task.
    """
    gap = int(round(float(cfg.get("protocol_rest_between_s", 3)) * fs))
    pieces, trials, task_index = [], [], []
    cursor = 0
    for i, step in enumerate(steps(cfg)):
        prof = target_profile(step, fs)
        trials.append(Trial(task=step.task, start_s=cursor / fs, end_s=(cursor + len(prof)) / fs, level=step.level))
        pieces.append(prof)
        task_index.append(np.full(len(prof), i))
        cursor += len(prof)
        pieces.append(np.zeros(gap))
        task_index.append(np.full(gap, -1))
        cursor += gap
    target = np.concatenate(pieces)
    idx = np.concatenate(task_index)
    masks = {s.task: idx == i for i, s in enumerate(steps(cfg))}
    return target, trials, masks
