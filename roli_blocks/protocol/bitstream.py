"""Bit-stream packing for BLOCKS SysEx payloads.

Mirrors `Packed7BitArrayBuilder` / `Packed7BitArrayReader` from
`roli_blocks_basics/protocol/roli_BitPackingUtilities.h`.

Each payload byte holds 7 useful bits (MSB always 0 — SysEx requirement).
Multi-bit values are packed LSB-first within each byte and across bytes.
"""

from __future__ import annotations


def checksum(payload: bytes | bytearray) -> int:
    """Per-packet checksum used between the SysEx payload and the F7 terminator.

    Seed is `len(payload)`; for each byte `c = c + (c*2 + b)` mod 256;
    final result is masked to 7 bits.
    """
    c = len(payload) & 0xFF
    for b in payload:
        c = (c + (c * 2 + b)) & 0xFF
    return c & 0x7F


class BitWriter:
    """Append-only writer that packs bit fields into a 7-bit-per-byte stream."""

    __slots__ = ("_buf", "_bits_in_cur")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._bits_in_cur = 0  # bits already used in self._buf[-1]; 0 means start fresh

    def __len__(self) -> int:
        """Number of bytes that would be emitted if frozen now."""
        return len(self._buf)

    @property
    def bytes(self) -> bytes:
        return bytes(self._buf)

    def write(self, value: int, num_bits: int) -> None:
        if not 0 <= num_bits <= 32:
            raise ValueError("num_bits must be 0..32")
        if value < 0 or (num_bits < 32 and value >> num_bits):
            raise ValueError(f"value 0x{value:X} does not fit in {num_bits} bits")

        while num_bits > 0:
            if self._bits_in_cur == 0:
                if num_bits < 7:
                    self._buf.append(value & 0x7F)
                    self._bits_in_cur = num_bits
                    return
                if num_bits == 7:
                    self._buf.append(value & 0x7F)
                    return
                self._buf.append(value & 0x7F)
                value >>= 7
                num_bits -= 7
            else:
                bits_to_do = min(7 - self._bits_in_cur, num_bits)
                self._buf[-1] |= (value & ((1 << bits_to_do) - 1)) << self._bits_in_cur
                value >>= bits_to_do
                num_bits -= bits_to_do
                self._bits_in_cur += bits_to_do
                if self._bits_in_cur == 7:
                    self._bits_in_cur = 0

    def flush_partial_byte(self) -> None:
        """Force the current partial byte to be considered complete (zero-padded)."""
        if self._bits_in_cur != 0:
            self._bits_in_cur = 0


class BitReader:
    """Read sequential bit fields from a 7-bit-per-byte stream."""

    __slots__ = ("_data", "_byte_index", "_bits_read_in_cur")

    def __init__(self, data: bytes | bytearray) -> None:
        self._data = data
        self._byte_index = 0
        self._bits_read_in_cur = 0

    def remaining_bits(self) -> int:
        return (len(self._data) - self._byte_index) * 7 - self._bits_read_in_cur

    def read(self, num_bits: int) -> int:
        if not 0 <= num_bits <= 32:
            raise ValueError("num_bits must be 0..32")
        if num_bits == 0:
            return 0
        if self.remaining_bits() < num_bits:
            raise EOFError(f"need {num_bits} bits, have {self.remaining_bits()}")

        value = 0
        bits_so_far = 0
        while num_bits > 0:
            cur = self._data[self._byte_index] >> self._bits_read_in_cur
            available = 7 - self._bits_read_in_cur
            if available > num_bits:
                value |= (cur & ((1 << num_bits) - 1)) << bits_so_far
                self._bits_read_in_cur += num_bits
                return value
            value |= cur << bits_so_far
            num_bits -= available
            bits_so_far += available
            self._bits_read_in_cur = 0
            self._byte_index += 1
        return value
