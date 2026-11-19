"""Bilateral analysis edge cases."""

import copy

from emgmirror.analysis.bilateral import analyze
from emgmirror.report import write_report


def test_dead_reference_electrode_gives_no_activation_instead_of_crashing(cfg, mirror_session, tmp_path):
    """A flat reference (right) channel has MVC net RMS 0: the left-vs-right ratio is undefined, not a crash."""
    s = copy.deepcopy(mirror_session)
    block = s.blocks[0]
    c = next(i for i, ch in enumerate(block.channels) if ch.arm == "right" and ch.muscle == "flexors")
    block.data[:, c] = 0.0
    res = analyze(s, cfg)
    assert res["activation"]["flexors"]["pct"] is None
    assert res["summary"]["flexors"]["activation_pct"] is None
    assert res["summary"]["extensors"]["activation_pct"] is not None  # the other muscle is unaffected
    out = write_report(s, cfg, res, tmp_path / "report.html")
    assert "<td>flexors</td><td>–</td>" in out.read_text(encoding="utf-8")
