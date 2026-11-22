"""Pin the TotalSegmentator `total` weights used by the device build (controlled change; run by a maintainer).

    python scripts/ctseg_pin_device_weights.py --weights-dir ~/.totalsegmentator/nnunet/results

Writes src/emgmirror/ctseg/resources/device_weights.json with a SHA-256 for every weight file of datasets
291-295 (1.5 mm `total`) and 297 (3 mm `total`, `--fast`). Changing this file is a model change: update the SOUP
register (docs/ct_platform/device/SOUP_register_ctseg.md) and re-run verification.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

from emgmirror.ctseg import device

OUT = Path(__file__).resolve().parents[1] / "src" / "emgmirror" / "ctseg" / "resources" / "device_weights.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights-dir", type=Path, required=True)
    a = ap.parse_args()
    ids = device.total_dataset_ids(False) + device.total_dataset_ids(True)
    m = device.build_manifest(a.weights_dir, ids, importlib.metadata.version("TotalSegmentator"))
    OUT.write_text(json.dumps(m, indent=1) + "\n")
    for i, d in m["datasets"].items():
        print(i, d["tree_sha256"], len(d["files"]), "files")


if __name__ == "__main__":
    main()
