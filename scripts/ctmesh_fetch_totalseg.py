"""Fetch a few subjects from the public TotalSegmentator dataset (Zenodo 6802613, CC BY 4.0) without
downloading the whole archive: the zip is read through HTTP range requests and only the chosen members are
extracted. Each subject is converted into the S1 input contract (labels.nii.gz + labels.json) by
``emgmirror.ctmesh.fixtures.totalseg_to_contract``.

Usage:
    python scripts/ctmesh_fetch_totalseg.py OUT_DIR --list               # print subject ids + meta rows
    python scripts/ctmesh_fetch_totalseg.py OUT_DIR s0011 s0287 ...      # fetch and convert

Research use, not for diagnosis or surgical planning.
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

RECORD = "https://zenodo.org/api/records/22688904"  # v3.0.0 of concept record 6802613
URL = RECORD + "/files/Totalsegmentator_dataset_v300.zip/content"


class HttpRangeFile(io.RawIOBase):
    """Read-only, seekable file over HTTP range requests, with a block cache."""

    def __init__(self, url: str, block: int = 1 << 20):
        self.url, self.block, self.pos, self.cache = url, block, 0, {}
        req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Range"].split("/")[1])

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def _blk(self, i: int) -> bytes:
        if i not in self.cache:
            lo = i * self.block
            hi = min(lo + self.block, self.size) - 1
            for attempt in range(5):
                try:
                    req = urllib.request.Request(self.url, headers={"Range": f"bytes={lo}-{hi}"})
                    with urllib.request.urlopen(req, timeout=120) as r:
                        self.cache[i] = r.read()
                    break
                except OSError:
                    if attempt == 4:
                        raise
            if len(self.cache) > 256:
                self.cache.pop(next(iter(self.cache)))
        return self.cache[i]

    def read(self, n=-1):
        if n < 0:
            n = self.size - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.size:
            i, o = divmod(self.pos, self.block)
            b = self._blk(i)[o:o + n]
            out += b
            self.pos += len(b)
            n -= len(b)
        return bytes(out)

    def readinto(self, buf):
        d = self.read(len(buf))
        buf[:len(d)] = d
        return len(d)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("subjects", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--url", default=URL)
    ap.add_argument("--with-ct", action="store_true", help="also fetch ct.nii.gz (not needed for meshing)")
    a = ap.parse_args(argv)
    z = zipfile.ZipFile(HttpRangeFile(a.url))
    names = z.namelist()
    a.out.mkdir(parents=True, exist_ok=True)
    meta = [n for n in names if n.endswith("meta.csv")]
    if meta:
        (a.out / "meta.csv").write_bytes(z.read(meta[0]))
    if a.list:
        subs = sorted({p for n in names for p in n.split("/") if p.startswith("s") and p[1:].isdigit()})
        print(len(subs), "subjects; meta.csv saved to", a.out / "meta.csv")
        return 0
    from emgmirror.ctmesh.fixtures import totalseg_to_contract
    for s in a.subjects:
        raw = a.out / "raw" / s
        members = [n for n in names if f"/{s}/" in f"/{n}" and n.endswith(".nii.gz")
                   and ("/segmentations/" in n or a.with_ct)]
        if not members:
            print("not found:", s, file=sys.stderr)
            continue
        for n in members:
            rel = n[n.index(s + "/") + len(s) + 1:]
            dst = raw / rel
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(z.read(n))
        totalseg_to_contract(raw, a.out / s)
        print("ok", s, len(members), "files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
