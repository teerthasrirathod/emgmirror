#!/usr/bin/env python3
"""Download one public TotalSegmentator subject (CT + per-structure masks).

Source: TotalSegmentator dataset v1, Zenodo record 6802614 (concept 6802613),
licence CC BY 4.0, Wasserthal et al., Radiology: AI 2023.
Only the files of one subject are read (HTTP range requests), about 20-40 MB.

    python3 web/ct-viewer/tools/fetch_sample.py --subject s0024 --out data/ct_sample/raw
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from remote_zip import HttpRangeFile  # noqa: E402

URL = "https://zenodo.org/api/records/6802614/files/Totalsegmentator_dataset.zip/content"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="s0024")
    ap.add_argument("--out", default="data/ct_sample/raw")
    args = ap.parse_args()
    out = pathlib.Path(args.out) / args.subject
    out.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(HttpRangeFile(URL))
    prefix = f"Totalsegmentator_dataset/{args.subject}/"
    members = [i for i in z.infolist() if i.filename.startswith(prefix) and not i.is_dir()]
    if not members:
        sys.exit(f"subject {args.subject} not found")
    total = sum(i.compress_size for i in members)
    print(f"{len(members)} files, {total / 1e6:.1f} MB compressed")
    for i in members:
        rel = i.filename[len(prefix):]
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size == i.file_size:
            continue
        with z.open(i) as src, open(dst, "wb") as f:
            shutil.copyfileobj(src, f, 1 << 20)
    (out / "SOURCE.txt").write_text(
        "TotalSegmentator dataset v1, subject " + args.subject + "\n"
        "https://doi.org/10.5281/zenodo.6802613  licence CC BY 4.0\n"
        "Wasserthal J. et al., TotalSegmentator: robust segmentation of 104 anatomic\n"
        "structures in CT images. Radiology: Artificial Intelligence 2023.\n"
    )
    print("done:", out)


if __name__ == "__main__":
    main()
