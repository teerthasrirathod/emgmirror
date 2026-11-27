"""Read single members of a large remote ZIP file with HTTP range requests.

The TotalSegmentator dataset on Zenodo is one 28 GB zip. This lets the sample
builder pull one subject (about 30 MB) without downloading the whole archive.
"""
from __future__ import annotations

import io
import concurrent.futures
import struct
import time
import urllib.error
import urllib.request
import zlib
import binascii
from pathlib import Path, PurePosixPath
import zipfile

BLOCK = 2 << 20  # Zenodo reliably returns 2 MiB range responses on this dataset.


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


def extract_members_parallel(zf: zipfile.ZipFile, members: list[zipfile.ZipInfo], root: Path) -> None:
    """Extract selected remote ZIP members concurrently using bounded range requests."""
    remote = zf.fp
    if not isinstance(remote, HttpRangeFile):
        raise TypeError("parallel extraction requires an HttpRangeFile-backed ZIP")
    chunk_size = 2 << 20

    def get_range(start: int, end: int) -> bytes:
        request = urllib.request.Request(
            remote.url,
            headers={"Range": f"bytes={start}-{end - 1}", "User-Agent": remote.ua},
        )
        for attempt in range(7):
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = response.read()
                    if response.status != 206 or len(data) != end - start:
                        raise OSError(f"short or invalid range response for bytes {start}-{end - 1}")
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 6:
                    raise
                time.sleep(min(60, int(exc.headers.get("Retry-After", 5)) * (attempt + 1)))
            except (OSError, TimeoutError):
                if attempt == 6:
                    raise
                time.sleep(2 * (attempt + 1))
        raise OSError("range request retries exhausted")

    member_starts = []
    for info in members:
        remote.seek(info.header_offset)
        header = remote.read(30)
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise OSError(f"invalid local ZIP header: {info.filename}")
        name_len, extra_len = struct.unpack_from("<HH", header, 26)
        start = info.header_offset + 30 + name_len + extra_len
        member_starts.append((info, start))

    def extract(item: tuple[zipfile.ZipInfo, int]) -> None:
        info, start = item
        ranges = [
            (offset, min(offset + chunk_size, info.compress_size))
            for offset in range(0, info.compress_size, chunk_size)
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            chunks = list(pool.map(lambda pair: get_range(start + pair[0], start + pair[1]), ranges))
        compressed = b"".join(chunks)
        if info.compress_type == zipfile.ZIP_DEFLATED:
            data = zlib.decompress(compressed, -15)
        elif info.compress_type == zipfile.ZIP_STORED:
            data = compressed
        else:
            raise OSError(f"unsupported ZIP compression method {info.compress_type}")
        if len(data) != info.file_size or (binascii.crc32(data) & 0xFFFFFFFF) != info.CRC:
            raise OSError(f"size or checksum mismatch: {info.filename}")
        rel = PurePosixPath(info.filename)
        target = root.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(extract, member_starts))
