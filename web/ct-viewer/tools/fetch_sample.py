#!/usr/bin/env python3
"""Download one public TotalSegmentator subject (CT + per-structure masks).

Source: TotalSegmentator dataset v1 or v2 small subset, Zenodo records 6802614
or 10047263, CC BY 4.0. Only one subject is read with HTTP range requests.

    python3 web/ct-viewer/tools/fetch_sample.py --subject s0024 --out data/ct_sample/raw
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from remote_zip import HttpRangeFile, extract_members_parallel  # noqa: E402

DATASETS = {
    "v1": {
        "url": "https://zenodo.org/api/records/6802614/files/Totalsegmentator_dataset.zip/content",
        "prefix": "Totalsegmentator_dataset/",
        "name": "TotalSegmentator dataset v1",
        "doi": "10.5281/zenodo.6802613",
    },
    "v2-small": {
        "url": "https://zenodo.org/api/records/10047263/files/Totalsegmentator_dataset_small_v201.zip/content",
        "prefix": "",
        "name": "TotalSegmentator dataset small v2.0.1",
        "doi": "10.5281/zenodo.10047263",
    },
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=DATASETS, default="v2-small")
    ap.add_argument("--subject")
    ap.add_argument("--out", default="data/ct_sample/raw")
    args = ap.parse_args()
    dataset = DATASETS[args.dataset]
    subject = args.subject or ("s0011" if args.dataset == "v2-small" else "s0024")
    out = pathlib.Path(args.out) / subject
    out.mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(HttpRangeFile(dataset["url"]))
    prefix = f"{dataset['prefix']}{subject}/"
    members = [i for i in z.infolist() if i.filename.startswith(prefix) and not i.is_dir()]
    if not members:
        sys.exit(f"subject {args.subject} not found")
    total = sum(i.compress_size for i in members)
    print(f"{len(members)} files, {total / 1e6:.1f} MB compressed; downloading bounded ranges", flush=True)
    missing = [i for i in members if not (out / i.filename[len(prefix):]).is_file() or (out / i.filename[len(prefix):]).stat().st_size != i.file_size]
    if missing:
        # ZIP paths contain the subject prefix; map them back under the selected subject folder.
        trimmed = []
        for info in missing:
            copy = zipfile.ZipInfo(info.filename[len(prefix):])
            for attr in ("header_offset", "compress_size", "file_size", "compress_type", "CRC"):
                setattr(copy, attr, getattr(info, attr))
            trimmed.append(copy)
        extract_members_parallel(z, trimmed, out)
    (out / "SOURCE.txt").write_text(
        f"{dataset['name']}, subject {subject}\n"
        f"https://doi.org/{dataset['doi']}  licence CC BY 4.0\n"
        "Dataset reference labels only; no emgmirror inference. This case covers thorax, abdomen and pelvis; skull, humeri and femora are cut at scan edge, and hands, feet and lower legs are outside this scan.\n"
    )
    print("done:", out)


if __name__ == "__main__":
    main()
