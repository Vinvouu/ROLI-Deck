"""Round-trip and corner-case tests for bitstream + sysex."""

from __future__ import annotations

import pytest

from roli_blocks.protocol.bitstream import BitReader, BitWriter, checksum
from roli_blocks.protocol.sysex import DecodedFrame, FrameError, decode, encode


# -----------------------------------------------------------------------------
# checksum: known vectors from real device traffic
# -----------------------------------------------------------------------------

def test_checksum_request_topology_payload() -> None:
    # MessageType=0x01 + DeviceCommand(9 bits)=0x01 packs to [0x01, 0x01, 0x00]
    assert checksum(bytes([0x01, 0x01, 0x00])) == 0x5D


def test_checksum_empty() -> None:
    # seed = len(payload) = 0 → result 0
    assert checksum(b"") == 0


# -----------------------------------------------------------------------------
# BitWriter / BitReader: round-trip
# -----------------------------------------------------------------------------

def test_round_trip_various_widths() -> None:
    fields = [
        (0, 7), (1, 7), (0x7F, 7),
        (0, 8), (0xFF, 8),
        (0x12345678, 32),
        (0, 1), (1, 1),
        (0x1FF, 9),  # 9-bit DeviceCommand size
        (0xFFF, 12),  # touch X/Y
    ]
    w = BitWriter()
    for v, n in fields:
        w.write(v, n)
    r = BitReader(w.bytes)
    for v, n in fields:
        assert r.read(n) == v


def test_writer_rejects_value_too_big() -> None:
    w = BitWriter()
    with pytest.raises(ValueError):
        w.write(0x80, 7)


def test_reader_remaining_bits() -> None:
    w = BitWriter()
    w.write(0x55, 7)  # full byte
    w.write(0x03, 2)
    r = BitReader(w.bytes)
    # 2 bytes × 7 bits = 14 total, no reads yet
    assert r.remaining_bits() == 14
    assert r.read(7) == 0x55
    assert r.remaining_bits() == 7
    assert r.read(2) == 0x03


def test_writer_request_topology_matches_real_packet() -> None:
    """Ensure our writer reproduces the bytes used to drive a real Lightpad."""
    w = BitWriter()
    w.write(0x01, 7)  # MessageType deviceCommandMessage
    w.write(0x01, 9)  # DeviceCommand requestTopologyMessage
    w.flush_partial_byte()
    assert w.bytes == bytes([0x01, 0x01, 0x00])


# -----------------------------------------------------------------------------
# sysex envelope
# -----------------------------------------------------------------------------

def test_encode_request_topology() -> None:
    """Full host->device frame for requestTopology, master broadcast index 0."""
    payload = bytes([0x01, 0x01, 0x00])
    frame = encode(0, payload, from_host=True)
    assert frame == bytes(
        [0xF0, 0x00, 0x21, 0x10, 0x77, 0x00, 0x01, 0x01, 0x00, 0x5D, 0xF7]
    )


def test_decode_real_topology_response() -> None:
    """Real captured response from a Lightpad Block (topoIdx=30, charging, low battery)."""
    msg = bytes(
        [
            0xF0, 0x00, 0x21, 0x10, 0x77, 0x5E,  # header + devIdx (dir=1, topo=30)
            0x00, 0x00, 0x00, 0x00, 0x10, 0x10, 0x20, 0x00, 0x00,
            0x26, 0x68, 0x66, 0x5C, 0x61, 0x1A, 0x5C, 0x62, 0x64,
            0x2C, 0x61, 0x58, 0x2C, 0x19, 0x23, 0x24, 0x4F, 0x50,
            0x41, 0x47, 0x21, 0x4C, 0x2B, 0x4C, 0x0B, 0x0C, 0x60,
            0x41, 0x27, 0x00, 0x00,
            0x42, 0xF7,
        ]
    )
    f: DecodedFrame = decode(msg)
    assert f.from_device is True
    assert f.device_index == 30
    assert len(f.payload) == 40

    r = BitReader(f.payload)
    assert r.read(32) == 0  # PacketTimestamp

    # First message: deviceTopology
    assert r.read(7) == 0x01
    assert r.read(8) == 1  # ProtocolVersion
    assert r.read(7) == 1  # DeviceCount
    assert r.read(8) == 0  # ConnectionCount

    # Device entry: 16 chars × 7 bits + topoIdx + battery + charging
    serial = "".join(chr(r.read(7)) for _ in range(16))
    assert serial == "LPM9C58EIYB1Y2FH"
    assert r.read(7) == 30  # topology index
    assert 0 <= r.read(5) <= 31  # battery
    assert r.read(1) in (0, 1)  # charging

    # Subsequent messages: deviceVersion, deviceName
    assert r.read(7) == 0x06  # deviceVersion
    assert r.read(7) == 30  # topoIdx
    vlen = r.read(7)
    version = "".join(chr(r.read(7)) for _ in range(vlen))
    assert version.startswith("1.1.0")


def test_decode_rejects_bad_checksum() -> None:
    msg = bytearray(encode(0, b"\x01\x01\x00", from_host=True))
    msg[-2] ^= 0x01
    with pytest.raises(FrameError, match="checksum"):
        decode(bytes(msg))


def test_decode_rejects_short_message() -> None:
    with pytest.raises(FrameError):
        decode(b"\xF0\xF7")


def test_decode_rejects_wrong_manufacturer() -> None:
    with pytest.raises(FrameError, match="header"):
        decode(b"\xF0\x7E\x00\x00\x00\x00\x00\xF7")


def test_encode_decode_round_trip_device_to_host() -> None:
    payload = b"\x10\x20\x30\x40\x50"
    frame = encode(15, payload, from_host=False)
    f = decode(frame)
    assert f.device_index == 15
    assert f.from_device is True
    assert f.payload == payload
