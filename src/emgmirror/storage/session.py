"""On-disk session format.

A session is a folder::

    <session_id>/
        session.json      metadata: source, fs, blocks, trials, subject, ground truth, provenance
        block_000.npz     data: float32 array (n_samples, n_channels) in millivolts
        block_001.npz     (optional) force: float32 (n_samples,) normalised 0..1
        analysis.json     cache written by `emgmirror analyze`

A *block* is one continuous recording. Mirror mode produces one block holding
channels from both arms; sequential mode (small kits) produces one block per
arm. Every block carries labelled *trials* (task, start, end), so analysis does
not care how the data was captured.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class Channel:
    name: str
    arm: str  # "left" | "right"
    muscle: str


@dataclass
class Trial:
    task: str
    start_s: float
    end_s: float
    level: float = 0.0  # target effort shown to the subject (0..1)


@dataclass
class Block:
    channels: list[Channel]
    trials: list[Trial]
    data: np.ndarray  # (n_samples, n_channels) mV
    force: np.ndarray | None = None  # (n_samples,) optional dynamometer

    @property
    def arms(self) -> list[str]:
        return sorted({c.arm for c in self.channels})


@dataclass
class Session:
    session_id: str
    source: str  # "synthetic" | "serial" | "replay" | ...
    fs: float
    blocks: list[Block]
    subject: dict[str, Any] = field(default_factory=dict)
    ground_truth: dict[str, Any] | None = None  # synthetic sessions only
    notes: str = ""
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    # Where the data came from: raw recording id, device identity, unit conversion, integrity and any
    # gap fills. Empty for synthetic sessions.
    provenance: dict[str, Any] = field(default_factory=dict)

    def save(self, root: str | Path) -> Path:
        folder = Path(root) / self.session_id
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "session_id": self.session_id,
            "source": self.source,
            "fs": self.fs,
            "created": self.created,
            "notes": self.notes,
            "subject": self.subject,
            "ground_truth": self.ground_truth,
            "provenance": self.provenance,
            "blocks": [],
        }
        for i, block in enumerate(self.blocks):
            fname = f"block_{i:03d}.npz"
            arrays = {"data": block.data.astype(np.float32)}
            if block.force is not None:
                arrays["force"] = block.force.astype(np.float32)
            np.savez_compressed(folder / fname, **arrays)
            meta["blocks"].append(
                {
                    "file": fname,
                    "channels": [asdict(c) for c in block.channels],
                    "trials": [asdict(t) for t in block.trials],
                    "n_samples": int(block.data.shape[0]),
                }
            )
        (folder / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        stale = folder / "analysis.json"
        if stale.exists():
            stale.unlink()
        return folder

    @classmethod
    def load(cls, folder: str | Path) -> "Session":
        folder = Path(folder)
        meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        blocks = []
        for b in meta["blocks"]:
            with np.load(folder / b["file"]) as npz:
                data = npz["data"].astype(np.float64)
                force = npz["force"].astype(np.float64) if "force" in npz else None
            blocks.append(
                Block(
                    channels=[Channel(**c) for c in b["channels"]],
                    trials=[Trial(**t) for t in b["trials"]],
                    data=data,
                    force=force,
                )
            )
        return cls(
            session_id=meta["session_id"],
            source=meta["source"],
            fs=float(meta["fs"]),
            blocks=blocks,
            subject=meta.get("subject", {}),
            ground_truth=meta.get("ground_truth"),
            notes=meta.get("notes", ""),
            created=meta.get("created", ""),
            provenance=meta.get("provenance", {}),
        )


def list_sessions(root: str | Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(Path(root).glob("*/session.json"), reverse=True):
        meta = json.loads(p.read_text(encoding="utf-8"))
        out.append(
            {
                "session_id": meta["session_id"],
                "source": meta["source"],
                "created": meta.get("created", ""),
                "n_blocks": len(meta["blocks"]),
                "notes": meta.get("notes", ""),
            }
        )
    return out


def new_session_id(prefix: str) -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}_{prefix}"
