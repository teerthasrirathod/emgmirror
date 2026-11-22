"""Every software requirement traces to at least one existing test, and every cited test exists."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRS = ROOT / "docs" / "ct_platform" / "device" / "S1_software_requirements.md"
TESTS = Path(__file__).resolve().parent


def _tests() -> set[str]:
    out = set()
    for f in TESTS.glob("test_ctseg_*.py"):
        for name in re.findall(r"^def (test_\w+)", f.read_text(), re.M):
            out.add(f"{f.name}::{name}")
    return out


def test_every_requirement_has_existing_tests():
    rows = [ln for ln in SRS.read_text().splitlines() if re.match(r"\| SRS-\d{3} \|", ln)]
    assert len(rows) >= 25
    ids = [re.match(r"\| (SRS-\d{3})", r).group(1) for r in rows]
    assert len(ids) == len(set(ids)), "duplicate requirement ids"
    existing = _tests()
    for r in rows:
        cited = re.findall(r"`(test_ctseg_\w+\.py::test_\w+)`", r)
        assert cited, f"{r[:12]} has no test"
        missing = [c for c in cited if c not in existing]
        assert not missing, f"{r[:12]} cites missing tests {missing}"


def test_hazard_tests_are_all_traced():
    text = SRS.read_text()
    hazard_tests = [t for t in _tests() if t.startswith("test_ctseg_hazards.py::")]
    assert hazard_tests
    for t in hazard_tests:
        assert t in text, f"{t} is not traced to a requirement"
