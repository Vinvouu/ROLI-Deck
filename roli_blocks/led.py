"""LED grid control via BitmapLEDProgram + remote heap writes.

Mechanism (from BlockImplementation.cpp + BitmapLEDProgram.cpp):
  1. Compile BitmapLEDProgram LittleFoot source -> bytecode (M5 — embedded constant).
  2. Upload the bytecode by writing it to remote-heap byte 0 with sharedDataChange.
  3. The data area starts immediately after the bytecode at byte offset `program_size`.
     Each pixel is 16 bits at heap bit offset `program_size * 8 + (x + y * W) * 16`,
     packed RGB565 (R: 5 bits high, G: 6 bits, B: 5 bits low).
  4. Touching pixels means setting bits in this data region; we batch them into
     sharedDataChange messages along with skip/fill commands.

This module exposes Color, the abstract data layout, and the sharedDataChange
encoder for arbitrary (offset, byte) writes. The Lightpad surface exposes a
1-byte-per-color repacking that's easier to reason about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .protocol.messages import SharedDataChangeBuilder
from .protocol.opcodes import PACKET_INDEX_BITS

LIGHTPAD_GRID_W: Final = 15
LIGHTPAD_GRID_H: Final = 15

# RGB565 → 16 bits per pixel at consecutive bit positions
PIXEL_BITS: Final = 16

# Per `padBlockProgramAndHeapSize` from BlocksProtocolDefinitions.h
LIGHTPAD_HEAP_BYTES: Final = 7200


@dataclass(frozen=True, slots=True)
class Color:
    r: int
    g: int
    b: int

    @classmethod
    def from_hex(cls, hex_str: str) -> "Color":
        s = hex_str.lstrip("#")
        return cls(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))

    def to_rgb565(self) -> int:
        """Pack into 16 bits with R:5 G:6 B:5 layout (R in low bits)."""
        return ((self.r >> 3) & 0x1F) | (((self.g >> 2) & 0x3F) << 5) | (((self.b >> 3) & 0x1F) << 11)


BLACK: Final = Color(0, 0, 0)
WHITE: Final = Color(255, 255, 255)
RED: Final = Color(255, 0, 0)
GREEN: Final = Color(0, 255, 0)
BLUE: Final = Color(0, 0, 255)


class LightpadGrid:
    """In-memory mirror of a Lightpad's pixel data area.

    The grid maintains a 15×15 RGB array. To push changes to the device,
    call `diff_chunks(...)` to produce sharedDataChange payloads.

    The data area lives at byte offset `program_size` in the remote heap,
    and uses 16 contiguous bits per pixel laid out as RGB565.
    """

    def __init__(self, program_size: int, w: int = LIGHTPAD_GRID_W, h: int = LIGHTPAD_GRID_H) -> None:
        self.w = w
        self.h = h
        self.program_size = program_size
        self.data_bytes = (w * h * PIXEL_BITS + 7) // 8
        # Two buffers: current desired state and what was last sent
        self._pixels: list[Color] = [BLACK] * (w * h)
        self._last_sent_bits: bytearray = bytearray(self.data_bytes)
        self._dirty: bool = True

    # ----------------------------------------------- public mutation

    def set_pixel(self, x: int, y: int, color: Color) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        idx = x + y * self.w
        if self._pixels[idx] != color:
            self._pixels[idx] = color
            self._dirty = True

    def fill(self, color: Color) -> None:
        if all(p == color for p in self._pixels):
            return
        self._pixels = [color] * (self.w * self.h)
        self._dirty = True

    # ----------------------------------------------- transmission helpers

    def _render_bits(self) -> bytearray:
        """Render the current pixel buffer into the device-side bit layout."""
        out = bytearray(self.data_bytes)
        for i, p in enumerate(self._pixels):
            v = p.to_rgb565()
            bit = i * PIXEL_BITS
            # write 16 bits LSB-first into the byte stream
            for b in range(PIXEL_BITS):
                if v & (1 << b):
                    out[(bit + b) >> 3] |= 1 << ((bit + b) & 7)
        return out

    def encode_full_update(self, packet_index: int) -> bytes:
        """Encode a sharedDataChange that overwrites the entire data region."""
        bits = self._render_bits()
        b = SharedDataChangeBuilder(packet_index)
        b.skip(self.program_size)
        b.write_bytes(bytes(bits))
        return b.end(last_change=True)

    def encode_diff(self, packet_index: int, *, full: bool = False) -> bytes | None:
        """Encode only the bytes that differ since the last send. None if no changes.

        With ``full=True`` the entire data area is sent as one contiguous run
        regardless of what we've previously sent — useful for unreliable
        delivery scenarios (e.g. while the device is reporting touches and
        drops some incoming sharedDataChange messages).
        """
        bits = self._render_bits()
        if not full and bits == self._last_sent_bits:
            return None

        b = SharedDataChangeBuilder(packet_index)
        b.skip(self.program_size)
        if full:
            b.write_bytes(bytes(bits))
            payload = b.end(last_change=True)
            self._last_sent_bits = bits
            self._dirty = False
            return payload

        cursor = 0  # heap-byte index relative to program start
        # walk byte-by-byte, batching contiguous changed runs
        i = 0
        n = len(bits)
        emitted = False
        while i < n:
            if bits[i] == self._last_sent_bits[i]:
                i += 1
                continue
            run_start = i
            while i < n and bits[i] != self._last_sent_bits[i]:
                i += 1
            run = bytes(bits[run_start:i])
            # skip from cursor to run_start within data region
            skip = run_start - cursor
            if skip:
                b.skip(skip)
            b.write_bytes(run)
            cursor = i
            emitted = True
        payload = b.end(last_change=True)
        if not emitted:
            return None
        # Save what we just sent — caller is responsible for actually sending it
        self._last_sent_bits = bits
        self._dirty = False
        return payload

    def reset_send_state(self) -> None:
        """Forget what we've sent (e.g. after re-uploading the program)."""
        self._last_sent_bits = bytearray(self.data_bytes)
        self._dirty = True


# -----------------------------------------------------------------------------
# Program upload encoder (M5 boundary)
# -----------------------------------------------------------------------------


def encode_program_upload(packet_index: int, program_bytes: bytes) -> bytes:
    """Encode a sharedDataChange that writes the program bytes to heap offset 0.

    Used once per session to load BitmapLEDProgram (or any LittleFoot bytecode).
    """
    if packet_index >= (1 << PACKET_INDEX_BITS):
        raise ValueError("packet_index too big")
    return (
        SharedDataChangeBuilder(packet_index)
        .write_bytes(program_bytes)
        .end(last_change=True)
    )
