"""Packaging checks: what a teammate gets from a clone and an install."""

from __future__ import annotations

from pathlib import Path

import pytest

from emgmirror import config

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "emgmirror"


def test_every_static_folder_is_package_data():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = set(data["tool"]["setuptools"]["package-data"]["emgmirror"])
    folders = {p.parent.relative_to(PKG).as_posix() for p in PKG.rglob("static/*") if p.is_file()}
    assert folders, "no static folders found"
    missing = sorted(f for f in folders if f"{f}/*" not in patterns)
    assert not missing, f"static folders not shipped in a wheel: {missing}"


def test_missing_default_config_gives_a_clear_message(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_CONFIG", tmp_path / "configs" / "default.yaml")
    with pytest.raises(SystemExit) as e:
        config.load_config()
    assert "pip install -e" in str(e.value)
