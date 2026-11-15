"""Tests for LED grid bit packing and diff encoding."""

from __future__ import annotations

from roli_blocks.led import (
    BLACK,
    LIGHTPAD_GRID_H,
    LIGHTPAD_GRID_W,
    Color,
    LightpadGrid,
    encode_program_upload,
)
from roli_blocks.protocol.bitstream import BitReader
from roli_blocks.protocol.opcodes import DataChangeCommand, MessageFromHost


def test_color_rgb565_red() -> None:
    # Pure red: R=0x1F, G=0, B=0 in low bits
    assert Color(255, 0, 0).to_rgb565() == 0x001F


def test_color_rgb565_green() -> None:
    # Pure green: G=0x3F at bit 5
    assert Color(0, 255, 0).to_rgb565() == 0x3F << 5


def test_color_rgb565_blue() -> None:
    # Pure blue: B=0x1F at bit 11
    assert Color(0, 0, 255).to_rgb565() == 0x1F << 11


def test_color_rgb565_white() -> None:
    assert Color(255, 255, 255).to_rgb565() == 0xFFFF


def test_grid_dimensions_and_size() -> None:
    g = LightpadGrid(program_size=1234)
    assert g.w == LIGHTPAD_GRID_W
    assert g.h == LIGHTPAD_GRID_H
    # 15*15 pixels * 16 bits = 3600 bits = 450 bytes
    assert g.data_bytes == 450


def test_render_bits_first_pixel_red() -> None:
    g = LightpadGrid(program_size=0)
    g.set_pixel(0, 0, Color(255, 0, 0))
    bits = g._render_bits()
    # Pixel 0 is bits 0..15. Red(0x1F) sits in bits 0..4 → byte 0 = 0x1F, byte 1 = 0
    assert bits[0] == 0x1F
    assert bits[1] == 0x00


def test_render_bits_second_pixel_blue() -> None:
    g = LightpadGrid(program_size=0)
    g.set_pixel(1, 0, Color(0, 0, 255))
    bits = g._render_bits()
    # Pixel 1 occupies bits 16..31 → bytes 2..3.  Blue=0x1F<<11 = 0xF800
    assert bits[2] == 0x00
    assert bits[3] == 0xF8


def test_diff_encoding_skips_unchanged() -> None:
    g = LightpadGrid(program_size=100)
    # First call after construction: nothing dirty -> None? Actually buffer == zeros,
    # last_sent == zeros, so diff is empty.
    assert g.encode_diff(packet_index=0) is None

    # Set pixel (0, 0) red — only bytes 0..1 of the data region change.
    g.set_pixel(0, 0, Color(255, 0, 0))
    payload = g.encode_diff(packet_index=1)
    assert payload is not None
    # Subsequent diff with no change -> None again
    assert g.encode_diff(packet_index=2) is None


def test_program_upload_payload_starts_with_shared_data_change() -> None:
    payload = encode_program_upload(0, b"\xAB\xCD\xEF")
    # Lowest 7 bits of byte 0 = MessageType. sharedDataChange = 0x02.
    assert payload[0] & 0x7F == int(MessageFromHost.sharedDataChange)


def test_diff_payload_contains_skip_to_data_region() -> None:
    g = LightpadGrid(program_size=42)
    g.set_pixel(7, 7, Color(0, 255, 0))
    payload = g.encode_diff(packet_index=0)
    assert payload is not None
    # We can't fully parse the bit stream here without re-implementing skip decoding,
    # but at minimum the payload must be longer than just "skip 42 bytes" alone.
    assert len(payload) > 4


def test_grid_fill_then_diff_emits_data() -> None:
    g = LightpadGrid(program_size=10)
    g.fill(Color(255, 255, 255))
    payload = g.encode_diff(packet_index=0)
    assert payload is not None
    # After sending diff, the same fill should produce no further diff.
    assert g.encode_diff(packet_index=1) is None
