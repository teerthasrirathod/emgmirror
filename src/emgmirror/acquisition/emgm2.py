"""EMGM/2 wire protocol: framing, packet encoding and a streaming decoder.

Pure functions and classes only (no serial port), so the protocol is testable without hardware and
can be reused by any transport. The specification is docs/ACQUISITION_PROTOCOL.md; the reference
device implementation is firmware/emg_esp32_v2.

Packet on the wire::

    COBS( type:u8 | payload | crc16:u16le ) 0x00

CRC is CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no final xor) over
type + payload. Integers are little-endian.
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass, field

import numpy as np

PROTOCOL = "EMGM/2"

HELLO, DATA, STATUS, LOG, ACK = 0x01, 0x02, 0x03, 0x04, 0x05
TYPE_NAMES = {HELLO: "hello", DATA: "data", STATUS: "status", LOG: "log", ACK: "ack"}

FLAG_INCOMPLETE_ROW = 0x01
FLAG_RUN_START = 0x02
FLAG_AFTER_DROP = 0x04

INVALID_SAMPLE = 0xFFFF
DATA_HEADER = struct.Struct("<IIQIBBBB")  # seq, first_index, t_us, dma_overflow, n_rows, n_ch, os, flags


def crc16(data: bytes) -> int:
    # binascii.crc_hqx is CRC-16 with poly 0x1021, MSB-first, no final xor: with init 0xFFFF it is
    # exactly CRC-16/CCITT-FALSE, computed in C.
    return binascii.crc_hqx(data, 0xFFFF)


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    for seg in bytes(data).split(b"\x00"):
        while len(seg) >= 254:
            out.append(0xFF)
            out += seg[:254]
            seg = seg[254:]
        out.append(len(seg) + 1)
        out += seg
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise ValueError("zero byte inside COBS frame")
        end = i + code
        if end > n:
            raise ValueError("COBS code runs past end of frame")
        out += data[i + 1:end]
        i = end
        if code < 0xFF and i < n:
            out.append(0)
    return bytes(out)


def encode_packet(ptype: int, payload: bytes) -> bytes:
    body = bytes([ptype]) + payload
    body += struct.pack("<H", crc16(body))
    return cobs_encode(body) + b"\x00"


def encode_text(ptype: int, text: str) -> bytes:
    return encode_packet(ptype, text.encode("ascii"))


def encode_data(seq: int, first_index: int, t_us: int, dma_overflow: int, raw: np.ndarray, os: int,
                flags: int = 0) -> bytes:
    raw = np.asarray(raw, dtype="<u2")
    n_rows, n_ch = raw.shape
    head = DATA_HEADER.pack(seq & 0xFFFFFFFF, first_index & 0xFFFFFFFF, t_us & 0xFFFFFFFFFFFFFFFF,
                            dma_overflow & 0xFFFFFFFF, n_rows, n_ch, os, flags)
    return encode_packet(DATA, head + raw.tobytes())


def parse_kv(text: str) -> dict[str, str]:
    out = {}
    for tok in text.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


@dataclass
class DataFrame:
    seq: int
    first_index: int  # 32-bit as sent; the source unwraps it
    t_us: int  # device esp_timer at the conv-done ISR of this frame (end of the frame)
    #   -1 = unknown (the firmware sends (u64)-1 when the frame has no ISR timestamp)
    dma_overflow: int
    os: int
    flags: int
    raw: np.ndarray  # (n_rows, n_ch) uint16 sums of `os` conversions; 0xFFFF = invalid


@dataclass
class Packet:
    type: int
    payload: bytes
    text: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    frame: DataFrame | None = None

    @property
    def name(self) -> str:
        return TYPE_NAMES.get(self.type, f"unknown_{self.type:#04x}")


def decode_body(body: bytes) -> Packet:
    """Decode one de-COBSed packet body. Raises ValueError on any inconsistency."""
    if len(body) < 3:
        raise ValueError("packet too short")
    (crc,) = struct.unpack("<H", body[-2:])
    if crc16(body[:-2]) != crc:
        raise ValueError("crc mismatch")
    ptype, payload = body[0], body[1:-2]
    if ptype == DATA:
        if len(payload) < DATA_HEADER.size:
            raise ValueError("data header truncated")
        seq, idx, t_us, ovf, n_rows, n_ch, os, flags = DATA_HEADER.unpack_from(payload)
        if t_us >= 1 << 63:  # the firmware's "unknown" sentinel is a signed -1 sent as u64
            t_us -= 1 << 64
        need = DATA_HEADER.size + 2 * n_rows * n_ch
        if len(payload) != need or n_ch == 0 or n_rows == 0:
            raise ValueError(f"data length {len(payload)} != {need}")
        raw = np.frombuffer(payload, dtype="<u2", offset=DATA_HEADER.size).reshape(n_rows, n_ch).copy()
        return Packet(ptype, payload, frame=DataFrame(seq, idx, t_us, ovf, os, flags, raw))
    if ptype in TYPE_NAMES:
        text = payload.decode("ascii", errors="replace")
        return Packet(ptype, payload, text=text, fields=parse_kv(text))
    raise ValueError(f"unknown packet type {ptype:#04x}")


class StreamDecoder:
    """Bytes in, packets out. Survives garbage, partial frames and corruption.

    Counters (never reset):
      packets        valid packets decoded
      crc_errors     frames with a bad CRC
      frame_errors   frames that were not valid COBS or had an inconsistent layout
      bytes_dropped  bytes discarded because they could not be framed
    """

    MAX_FRAME = 4096

    def __init__(self):
        self.buf = bytearray()
        self.packets = 0
        self.crc_errors = 0
        self.frame_errors = 0
        self.bytes_dropped = 0
        self.synced = False  # the first delimiter has been seen; bytes before it are a partial frame

    def feed(self, data: bytes) -> list[Packet]:
        self.buf += data
        out: list[Packet] = []
        while True:
            end = self.buf.find(b"\x00")
            if end < 0:
                if len(self.buf) > self.MAX_FRAME:  # no delimiter in a sane distance: discard
                    self.bytes_dropped += len(self.buf)
                    self.frame_errors += 1
                    self.buf.clear()
                break
            frame = bytes(self.buf[:end])
            del self.buf[:end + 1]
            if not self.synced:
                # Bytes before the first delimiter are the tail of a packet sent before we opened the
                # port (or legacy text). They are not an error.
                self.synced = True
                self.bytes_dropped += len(frame)
                continue
            if not frame:
                continue
            try:
                body = cobs_decode(frame)
            except ValueError:
                self.frame_errors += 1
                self.bytes_dropped += len(frame) + 1
                continue
            try:
                pkt = decode_body(body)
            except ValueError as exc:
                if "crc" in str(exc):
                    self.crc_errors += 1
                else:
                    self.frame_errors += 1
                self.bytes_dropped += len(frame) + 1
                continue
            self.packets += 1
            out.append(pkt)
        return out

    def counters(self) -> dict[str, int]:
        return {"packets": self.packets, "crc_errors": self.crc_errors, "frame_errors": self.frame_errors,
                "bytes_dropped": self.bytes_dropped}
