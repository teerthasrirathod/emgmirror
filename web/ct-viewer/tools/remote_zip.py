"""Read single members of a large remote ZIP file with HTTP range requests.

The TotalSegmentator dataset on Zenodo is one 28 GB zip. This lets the sample
builder pull one subject (about 30 MB) without downloading the whole archive.
"""
from __future__ import annotations

import io
import urllib.request

BLOCK = 1 << 20  # 1 MiB


class HttpRangeFile(io.RawIOBase):
    """A read-only, seekable file object backed by HTTP Range requests."""

    def __init__(self, url: str, user_agent: str = "ct-viewer-sample/1.0") -> None:
        self.url = url
        self.ua = user_agent
        self.pos = 0
        self.cache: dict[int, bytes] = {}
        req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": self.ua})
        with urllib.request.urlopen(req, timeout=60) as r:
            cr = r.headers.get("Content-Range")
            if r.status != 206 or not cr:
                raise OSError("server does not support range requests")
            self.size = int(cr.split("/")[-1])
            self.url = r.geturl()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def _fetch(self, start: int, end: int) -> bytes:
        for attempt in range(5):
            try:
                req = urllib.request.Request(
                    self.url, headers={"Range": f"bytes={start}-{end - 1}", "User-Agent": self.ua}
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                if len(data) == end - start:
                    return data
            except OSError:
                if attempt == 4:
                    raise
        raise OSError("short read")

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        if n == 0:
            return b""
        # Large reads go straight through; small reads use a block cache.
        if n > 4 * BLOCK:
            data = self._fetch(self.pos, self.pos + n)
            self.pos += n
            return data
        out = bytearray()
        while len(out) < n:
            b = self.pos // BLOCK
            if b not in self.cache:
                s = b * BLOCK
                self.cache[b] = self._fetch(s, min(s + BLOCK, self.size))
                if len(self.cache) > 64:
                    self.cache.pop(next(iter(self.cache)))
            blk = self.cache[b]
            off = self.pos - b * BLOCK
            take = blk[off : off + n - len(out)]
            out += take
            self.pos += len(take)
        return bytes(out)

    def readinto(self, buf) -> int:  # type: ignore[override]
        data = self.read(len(buf))
        buf[: len(data)] = data
        return len(data)
