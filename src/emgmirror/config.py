"""Configuration loading.

The YAML file is the single source of truth for channels, protocol, subject
measurements and simulation parameters. Values in a user file are deep-merged
over the packaged defaults, so a personal file only needs the keys it changes.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.yaml"
DATA_DIR = REPO_ROOT / "data" / "sessions"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _load_layer(path: Path, seen: tuple[Path, ...] = ()) -> dict:
    """One config file, with its optional `base:` (another config it builds on) merged underneath.

    `base` is resolved relative to the file, then to the repository root. Lists (such as
    `channels` or `hardware.channels`) are replaced as a whole, never merged item by item.
    """
    path = path.resolve()
    if path in seen:
        raise ValueError(f"config base loop: {' -> '.join(str(p) for p in (*seen, path))}")
    with open(path, encoding="utf-8") as fh:
        layer = yaml.safe_load(fh) or {}
    base = layer.pop("base", None)
    if base is None:
        return layer
    cand = (path.parent / base) if (path.parent / base).exists() else (REPO_ROOT / base)
    if cand.resolve() == DEFAULT_CONFIG:
        return layer
    return _deep_merge(_load_layer(cand, (*seen, path)), layer)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if not DEFAULT_CONFIG.is_file():
        raise SystemExit(
            f"emgmirror: default config not found at {DEFAULT_CONFIG}. emgmirror runs from a clone of "
            "its repository (it needs configs/, firmware/ and the data folders next to src/): clone the "
            'repository and install it with  pip install -e ".[dev]"  instead of a plain  pip install .'
        )
    with open(DEFAULT_CONFIG, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if path is not None and Path(path).resolve() != DEFAULT_CONFIG:
        cfg = _deep_merge(cfg, _load_layer(Path(path)))
    return cfg


def muscles(cfg: dict) -> list[str]:
    """Muscle groups that have at least one channel, in config order."""
    seen: list[str] = []
    for ch in cfg["channels"]:
        if ch["muscle"] not in seen:
            seen.append(ch["muscle"])
    return seen
