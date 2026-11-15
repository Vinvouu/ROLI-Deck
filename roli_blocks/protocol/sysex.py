"""SysEx envelope encoding/decoding for BLOCKS packets.

Frame layout:
    F0 00 21 10 77 [devIdx]  [payload bytes]  [checksum]  F7
    └─── header ───┘ └─ 1B ─┘ └── M bytes ──┘ └── 1 ──┘ └ 1 ┘

`devIdx`:
  bit 6 (0x40)  direction (1 = device->host, 0 = host->device)
  bits 0..5     topology index (0..63 — 63 reserved as broadcast)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .bitstream import checksum

ROLI_SYSEX_HEADER: Final = bytes([0xF0, 0x00, 0x21, 0x10, 0x77])
SYSEX_END: Final = 0xF7

DIRECTION_BIT: Final = 0x40
TOPOLOGY_INDEX_MASK: Final = 0x3F
TOPOLOGY_INDEX_BROADCAST: Final = 63


def encode(device_index: int, payload: bytes | bytearray, *, from_host: bool = True) -> bytes:
    """Wrap a bit-stream payload in a complete SysEx frame."""
    if not 0 <= device_index <= TOPOLOGY_INDEX_MASK:
        raise ValueError(f"device_index {device_index} out of range 0..63")
    devidx_byte = device_index & TOPOLOGY_INDEX_MASK
    if not from_host:
        devidx_byte |= DIRECTION_BIT
    return (
        ROLI_SYSEX_HEADER
        + bytes([devidx_byte])
        + bytes(payload)
        + bytes([checksum(payload), SYSEX_END])
    )


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    device_index: int
    from_device: bool
    payload: bytes


class FrameError(ValueError):
    pass


def decode(message: bytes | bytearray) -> DecodedFrame:
    """Validate envelope and extract payload. Raises FrameError if malformed."""
    if len(message) < 8:
        raise FrameError(f"too short ({len(message)} bytes)")
    if bytes(message[:5]) != ROLI_SYSEX_HEADER:
        raise FrameError("not a ROLI SysEx (header mismatch)")
    if message[-1] != SYSEX_END:
        raise FrameError(f"missing F7 terminator (got 0x{message[-1]:02X})")
    devidx_byte = message[5]
    payload = bytes(message[6:-2])
    cks = message[-2]
    expected = checksum(payload)
    if cks != expected:
        raise FrameError(f"checksum mismatch: got 0x{cks:02X}, expected 0x{expected:02X}")
    return DecodedFrame(
        device_index=devidx_byte & TOPOLOGY_INDEX_MASK,
        from_device=bool(devidx_byte & DIRECTION_BIT),
        payload=payload,
    )
