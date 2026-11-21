"""Shared fixtures for the ctseg tests."""

from __future__ import annotations

import pytest
import SimpleITK as sitk

from ctseg_helpers import make_phantom


def pytest_configure(config):
    config.addinivalue_line("markers", "slow_weights: needs TotalSegmentator installed with downloaded weights")


@pytest.fixture
def phantom():
    return make_phantom()


@pytest.fixture
def phantom_file(tmp_path, phantom):
    p = tmp_path / "ct.nii.gz"
    sitk.WriteImage(phantom[0], str(p))
    return p


@pytest.fixture
def ts_home(tmp_path, monkeypatch):
    home = tmp_path / "ts_home"
    monkeypatch.setenv("TOTALSEG_HOME_DIR", str(home))
    return home
