"""Optional end-to-end run with the real TotalSegmentator weights (skipped unless they are installed).

Enable with ``CTSEG_REAL_WEIGHTS=1`` once TotalSegmentator is installed and the ``total`` 3 mm and ``body``
3 mm weights are downloaded (``totalseg_download_weights -t total_fast``, ``-t body_fast``).
"""

import json
import os

import numpy as np
import pytest
import SimpleITK as sitk

from emgmirror.ctseg.config import SegConfig
from emgmirror.ctseg.engine import segment

from ctseg_helpers import make_phantom


def _weights_ready() -> bool:
    if os.environ.get("CTSEG_REAL_WEIGHTS") != "1":
        return False
    try:
        from totalsegmentator.config import get_weights_dir
    except ImportError:
        return False
    d = get_weights_dir()
    return any(d.glob("Dataset297_*")) and any(d.glob("Dataset300_*"))


@pytest.mark.slow_weights
@pytest.mark.skipif(not _weights_ready(), reason="set CTSEG_REAL_WEIGHTS=1 with TotalSegmentator weights installed")
def test_real_totalsegmentator_on_phantom(tmp_path):
    img, _ = make_phantom(shape=(60, 96, 120))
    p = tmp_path / "ct.nii.gz"
    sitk.WriteImage(img, str(p))
    res = segment(p, tmp_path / "out", SegConfig(profile="minimal", fast=True, device="cpu"))
    lab = sitk.ReadImage(str(res.labels_path))
    assert lab.GetSize() == img.GetSize() and lab.GetPixelID() == sitk.sitkUInt16
    prov = json.loads((tmp_path / "out" / "provenance.json").read_text())
    assert prov["privacy"]["totalsegmentator_send_usage_stats"] is False
    assert prov["software"]["TotalSegmentator"]
    assert np.isfinite(sum(v["volume_ml"] for v in res.labels_json["qc"]["structures"].values()))
