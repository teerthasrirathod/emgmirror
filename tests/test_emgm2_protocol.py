"""EMGM/2 framing: CRC, COBS, packet layout, and a decoder that survives bad input."""

import numpy as np
import pytest

from emgmirror.acquisition import emgm2


def test_crc16_ccitt_false_check_value():
    # Standard check value for CRC-16/CCITT-FALSE.
    assert emgm2.crc16(b"123456789") == 0x29B1


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x00\x00", b"\x11\x00\x22", bytes(range(1, 255)),
                                  bytes(range(1, 255)) + b"\x00" + bytes(300), bytes(range(256)) * 3])
def test_cobs_roundtrip_has_no_zero_bytes(data):
    enc = emgm2.cobs_encode(data)
    assert b"\x00" not in enc
    assert emgm2.cobs_decode(enc) == data


def test_data_packet_roundtrip():
    raw = np.array([[0, 1, 16380], [65534, 7, 0xFFFF]], dtype=np.uint16)
    wire = emgm2.encode_data(seq=5, first_index=2**32 - 1, t_us=123456789012, dma_overflow=3, raw=raw, os=4,
                             flags=emgm2.FLAG_AFTER_DROP)
    assert wire.endswith(b"\x00") and wire.count(b"\x00") == 1
    pkts = emgm2.StreamDecoder().feed(b"\x00" + wire)
    assert len(pkts) == 1
    f = pkts[0].frame
    assert (f.seq, f.first_index, f.t_us, f.dma_overflow, f.os, f.flags) == (5, 2**32 - 1, 123456789012, 3, 4,
                                                                             emgm2.FLAG_AFTER_DROP)
    assert np.array_equal(f.raw, raw)


def test_text_packets_parse_key_values():
    wire = emgm2.encode_text(emgm2.HELLO, "proto=EMGM/2 fs=2000 pins=35,32,33")
    p = emgm2.StreamDecoder().feed(b"\x00" + wire)[0]
    assert p.name == "hello" and p.fields == {"proto": "EMGM/2", "fs": "2000", "pins": "35,32,33"}


def test_decoder_handles_split_feeds_garbage_and_corruption():
    good = [emgm2.encode_text(emgm2.STATUS, f"n={i}") for i in range(5)]
    bad = bytearray(good[2])
    bad[3] ^= 0x40  # flip a bit: CRC must catch it
    stream = b"partial-tail-of-old-packet\x00" + good[0] + good[1] + bytes(bad) + b"\x01\x02junk\x00" + good[3] + good[4]
    dec = emgm2.StreamDecoder()
    got = []
    for i in range(0, len(stream), 7):  # arbitrary split points
        got += dec.feed(stream[i:i + 7])
    assert [p.fields["n"] for p in got] == ["0", "1", "3", "4"]
    assert dec.crc_errors + dec.frame_errors == 2


def test_decoder_rejects_inconsistent_data_length():
    raw = np.zeros((2, 3), dtype=np.uint16)
    wire = bytearray(emgm2.encode_data(0, 0, 0, 0, raw, 4))
    body = emgm2.cobs_decode(bytes(wire[:-1]))
    # Claim 3 rows while carrying 2, then re-CRC so only the length check can catch it.
    body = bytearray(body[:-2])
    body[1 + 20] = 3
    body += emgm2.crc16(bytes(body)).to_bytes(2, "little")
    dec = emgm2.StreamDecoder()
    assert dec.feed(b"\x00" + emgm2.cobs_encode(bytes(body)) + b"\x00") == []
    assert dec.frame_errors == 1


def test_decoder_discards_runaway_garbage_without_delimiter():
    dec = emgm2.StreamDecoder()
    dec.feed(b"\x00")
    dec.feed(b"\x05" * (emgm2.StreamDecoder.MAX_FRAME + 10))
    assert dec.frame_errors == 1 and not dec.buf
