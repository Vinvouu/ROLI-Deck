"""Tests for typed message decoders and host-side builders."""

from __future__ import annotations

from roli_blocks.protocol.messages import (
    ControlButton,
    DecodedPacket,
    DeviceNameMessage,
    DeviceVersionMessage,
    SharedDataChangeBuilder,
    TopologyMessage,
    Touch,
    build_begin_api_mode,
    build_end_api_mode,
    build_ping,
    build_request_topology,
    decode_packet,
)
from roli_blocks.protocol.opcodes import DeviceCommand
from roli_blocks.protocol.sysex import decode, encode


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------

def test_build_request_topology_matches_known_bytes() -> None:
    assert build_request_topology() == bytes([0x01, 0x01, 0x00])


def test_build_begin_api_mode() -> None:
    # MessageType=0x01 (deviceCommandMessage), DeviceCommand=0x00 (beginAPIMode)
    assert build_begin_api_mode() == bytes([0x01, 0x00, 0x00])


def test_build_end_api_mode() -> None:
    # MessageType=0x01, DeviceCommand=0x02
    assert build_end_api_mode() == bytes([0x01, 0x02, 0x00])


def test_build_ping() -> None:
    # MessageType=0x01, DeviceCommand=0x03
    assert build_ping() == bytes([0x01, 0x03, 0x00])


# -----------------------------------------------------------------------------
# Decoder against captured Lightpad response
# -----------------------------------------------------------------------------

CAPTURED_TOPOLOGY = bytes(
    [
        0xF0, 0x00, 0x21, 0x10, 0x77, 0x5E,
        0x00, 0x00, 0x00, 0x00, 0x10, 0x10, 0x20, 0x00, 0x00,
        0x26, 0x68, 0x66, 0x5C, 0x61, 0x1A, 0x5C, 0x62, 0x64,
        0x2C, 0x61, 0x58, 0x2C, 0x19, 0x23, 0x24, 0x4F, 0x50,
        0x41, 0x47, 0x21, 0x4C, 0x2B, 0x4C, 0x0B, 0x0C, 0x60,
        0x41, 0x27, 0x00, 0x00,
        0x42, 0xF7,
    ]
)


def test_decode_real_topology_packet() -> None:
    f = decode(CAPTURED_TOPOLOGY)
    pkt = decode_packet(f.device_index, f.payload)
    assert isinstance(pkt, DecodedPacket)
    assert pkt.device_index == 30
    assert pkt.packet_timestamp == 0
    assert len(pkt.messages) == 3

    topo = pkt.messages[0]
    assert isinstance(topo, TopologyMessage)
    assert topo.is_extension is False
    assert topo.protocol_version == 1
    assert len(topo.devices) == 1
    assert len(topo.connections) == 0
    assert topo.is_final is True
    dev = topo.devices[0]
    assert dev.serial == "LPM9C58EIYB1Y2FH"
    assert dev.topology_index == 30
    assert dev.battery_charging is True

    ver = pkt.messages[1]
    assert isinstance(ver, DeviceVersionMessage)
    assert ver.topology_index == 30
    assert ver.version == "1.1.0"

    name = pkt.messages[2]
    assert isinstance(name, DeviceNameMessage)
    assert name.topology_index == 30


# -----------------------------------------------------------------------------
# Touch packet round-trip via builder + decoder
# -----------------------------------------------------------------------------

def test_touch_packet_round_trip() -> None:
    """Build a synthetic device→host packet that contains touchStartWithVelocity,
    touchMove, touchEnd and verify all fields decode."""
    from roli_blocks.protocol.bitstream import BitWriter

    w = BitWriter()
    w.write(0x12345678, 32)  # PacketTimestamp

    # touchStartWithVelocity (0x13)
    w.write(0x13, 7)
    w.write(0, 5)            # offset
    w.write(0, 5)            # touchIndex
    w.write(2048, 12)        # x — center
    w.write(2048, 12)        # y
    w.write(200, 8)          # z
    w.write(10, 8)           # vx
    w.write(20, 8)           # vy
    w.write(30, 8)           # vz

    # touchMove (0x11) — no velocity
    w.write(0x11, 7)
    w.write(5, 5)            # offset (5ms later)
    w.write(0, 5)
    w.write(2100, 12)
    w.write(2050, 12)
    w.write(220, 8)

    # touchEnd (0x12)
    w.write(0x12, 7)
    w.write(10, 5)
    w.write(0, 5)
    w.write(2200, 12)
    w.write(2100, 12)
    w.write(0, 8)

    w.write(0, 7)  # MessageType=0 terminator
    w.flush_partial_byte()

    pkt = decode_packet(device_index=0, payload=w.bytes)
    assert pkt.packet_timestamp == 0x12345678
    touches = [m for m in pkt.messages if isinstance(m, Touch)]
    assert len(touches) == 3

    t0, t1, t2 = touches
    assert t0.is_start and not t0.is_end
    assert (t0.x, t0.y, t0.z) == (2048, 2048, 200)
    assert (t0.vx, t0.vy, t0.vz) == (10, 20, 30)
    assert t0.timestamp_ms == 0x12345678

    assert not t1.is_start and not t1.is_end
    assert t1.timestamp_ms == 0x12345678 + 5
    assert (t1.x, t1.y, t1.z) == (2100, 2050, 220)

    assert t2.is_end
    assert t2.timestamp_ms == 0x12345678 + 10
    assert t2.z == 0


def test_button_packet_round_trip() -> None:
    from roli_blocks.protocol.bitstream import BitWriter

    w = BitWriter()
    w.write(1000, 32)
    w.write(0x20, 7)  # controlButtonDown
    w.write(0, 5)
    w.write(42, 12)
    w.write(0x21, 7)  # controlButtonUp
    w.write(2, 5)
    w.write(42, 12)
    w.write(0, 7)
    w.flush_partial_byte()

    pkt = decode_packet(0, w.bytes)
    btns = [m for m in pkt.messages if isinstance(m, ControlButton)]
    assert len(btns) == 2
    assert btns[0].is_down and btns[0].button_id == 42 and btns[0].timestamp_ms == 1000
    assert not btns[1].is_down and btns[1].timestamp_ms == 1002


# -----------------------------------------------------------------------------
# SharedDataChangeBuilder smoke
# -----------------------------------------------------------------------------

def test_shared_data_change_builds_without_errors() -> None:
    payload = (
        SharedDataChangeBuilder(packet_index=1)
        .skip(10)
        .write_bytes(b"\x11\x22\x33")
        .skip(5)
        .fill(0xAA, 30)
        .end(last_change=True)
    )
    assert payload[0] == 0x02  # MessageType sharedDataChange (low 7 bits of byte 0)
    # Wrap in SysEx and verify it round-trips through the framer.
    frame = encode(0, payload, from_host=True)
    df = decode(frame)
    assert df.payload == payload
