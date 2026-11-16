"""BLE MIDI 1.0 codec (MIDI over Bluetooth Low Energy) — no bleak dependency.

Packet layout (spec "MIDI over Bluetooth Low Energy", MMA 2015):

    [header] [timestamp] [status] [data...] [timestamp] [status] [data...] ...

- header     : 1 0 t12 t11 t10 t9 t8 t7   (bit 7 set, bit 6 clear, 6 high timestamp bits)
- timestamp  : 1 t6 t5 t4 t3 t2 t1 t0     (bit 7 set, 7 low timestamp bits) -> 13-bit ms counter
- every status byte is preceded by a timestamp byte
- running status: data bytes may follow a completed message without a new status
  byte (optionally after a fresh timestamp byte)
- SysEx may span several packets: continuation packets carry only data bytes
  right after the header, the terminating F7 is preceded by a timestamp byte,
  and system real-time messages (F8..FF) may be interleaved inside a SysEx.

`BleMidiParser.feed(packet)` is incremental: it keeps SysEx and running-status
state across packets. `encode_packets(...)` does the reverse for sending.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

MIDI_SERVICE_UUID = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHAR_UUID = "7772e5db-3868-4112-a1a9-f2669d106bf3"


@dataclass(frozen=True, slots=True)
class MidiEvent:
    """One decoded MIDI message with its 13-bit BLE timestamp (ms, wraps at 8192)."""

    timestamp: int
    data: bytes

    @property
    def status(self) -> int:
        return self.data[0]

    @property
    def is_sysex(self) -> bool:
        return self.data[0] == 0xF0

    @property
    def is_realtime(self) -> bool:
        return self.data[0] >= 0xF8

    def describe(self) -> str:
        s = self.data[0]
        ch = (s & 0x0F) + 1
        kind = s & 0xF0
        d = self.data
        if s == 0xF0:
            return f"SysEx {len(d)} bytes: " + " ".join(f"{b:02X}" for b in d[:12]) + (" ..." if len(d) > 12 else "")
        if kind == 0x90 and len(d) == 3 and d[2] > 0:
            return f"Note On  ch{ch} note={d[1]} vel={d[2]}"
        if kind == 0x80 or (kind == 0x90 and len(d) == 3):
            return f"Note Off ch{ch} note={d[1]} vel={d[2] if len(d) > 2 else 0}"
        if kind == 0xA0 and len(d) == 3:
            return f"Poly AT  ch{ch} note={d[1]} pressure={d[2]}"
        if kind == 0xB0 and len(d) == 3:
            return f"CC       ch{ch} cc={d[1]} value={d[2]}"
        if kind == 0xD0 and len(d) == 2:
            return f"Chan AT  ch{ch} pressure={d[1]}"
        if kind == 0xE0 and len(d) == 3:
            return f"PitchBend ch{ch} value={(d[2] << 7 | d[1]) - 8192:+d}"
        if s == 0xFE:
            return "Active Sensing"
        if s == 0xF8:
            return "Timing Clock"
        return "MIDI " + " ".join(f"{b:02X}" for b in d)


def message_length(status: int) -> int:
    """Total length (status + data) of a channel/system common message, 0 if unknown."""
    if 0x80 <= status <= 0xBF or 0xE0 <= status <= 0xEF:
        return 3
    if 0xC0 <= status <= 0xDF:
        return 2
    return {0xF1: 2, 0xF2: 3, 0xF3: 2, 0xF6: 1}.get(status, 1)


@dataclass
class BleMidiParser:
    """Incremental decoder. Call `feed()` for every notification payload."""

    events: list[MidiEvent] = field(default_factory=list)
    _sysex: bytearray | None = None
    _running_status: int = 0
    _pending: bytearray = field(default_factory=bytearray)
    _pending_len: int = 0
    _timestamp: int = 0
    _last_sysex_timestamp: int = 0

    def feed(self, packet: bytes | bytearray | memoryview) -> list[MidiEvent]:
        """Decode one BLE packet, return the complete messages it produced."""
        packet = bytes(packet)
        out: list[MidiEvent] = []
        if not packet:
            return out
        header = packet[0]
        if header & 0xC0 != 0x80:
            raise ValueError(f"invalid BLE MIDI header byte 0x{header:02X}")
        ts_high = (header & 0x3F) << 7
        self._timestamp = ts_high | (self._timestamp & 0x7F)
        prev_was_timestamp = False

        for b in packet[1:]:
            if b & 0x80:
                if not prev_was_timestamp:
                    # Timestamp byte (status bytes are always preceded by one).
                    self._timestamp = ts_high | (b & 0x7F)
                    prev_was_timestamp = True
                    continue
                prev_was_timestamp = False
                self._handle_status(b, out)
            else:
                prev_was_timestamp = False
                self._handle_data(b, out)
        self.events.extend(out)
        return out

    # ------------------------------------------------------------------ internals

    def _emit(self, data: bytes, out: list[MidiEvent], timestamp: int | None = None) -> None:
        out.append(MidiEvent(self._timestamp if timestamp is None else timestamp, data))

    def _handle_status(self, b: int, out: list[MidiEvent]) -> None:
        if b >= 0xF8:  # system real-time, may interrupt anything
            self._emit(bytes([b]), out)
            return
        if b == 0xF0:
            self._sysex = bytearray([0xF0])
            self._last_sysex_timestamp = self._timestamp
            self._pending.clear()
            self._pending_len = 0
            return
        if b == 0xF7:
            if self._sysex is not None:
                self._sysex.append(0xF7)
                self._emit(bytes(self._sysex), out, self._last_sysex_timestamp)
                self._sysex = None
            return
        # Any other status byte terminates an unfinished SysEx (malformed stream).
        self._sysex = None
        length = message_length(b)
        if length == 1:
            self._emit(bytes([b]), out)
            self._running_status = 0
            return
        self._running_status = b if b < 0xF0 else 0
        self._pending = bytearray([b])
        self._pending_len = length

    def _handle_data(self, b: int, out: list[MidiEvent]) -> None:
        if self._sysex is not None:
            self._sysex.append(b)
            return
        if not self._pending:
            if not self._running_status:
                return  # stray data byte, ignore
            self._pending = bytearray([self._running_status])
            self._pending_len = message_length(self._running_status)
        self._pending.append(b)
        if len(self._pending) >= self._pending_len:
            self._emit(bytes(self._pending), out)
            self._pending = bytearray()


# ------------------------------------------------------------------------ encoding


def ble_timestamp(now_ms: float | None = None) -> int:
    ms = int(time.monotonic() * 1000) if now_ms is None else int(now_ms)
    return ms & 0x1FFF


def _header(ts: int) -> int:
    return 0x80 | ((ts >> 7) & 0x3F)


def _ts_byte(ts: int) -> int:
    return 0x80 | (ts & 0x7F)


def encode_packets(messages: list[bytes], max_len: int, timestamp: int | None = None) -> list[bytes]:
    """Pack complete MIDI messages into BLE packets of at most `max_len` bytes.

    Channel messages are each prefixed with a timestamp byte. A SysEx is split
    across packets as the spec requires: data continues right after the header
    of the following packets, and the final F7 gets its own timestamp byte.
    """
    if max_len < 5:
        raise ValueError("max_len must be at least 5 bytes")
    ts = ble_timestamp() if timestamp is None else timestamp & 0x1FFF
    packets: list[bytes] = []
    cur = bytearray([_header(ts)])

    def flush() -> None:
        nonlocal cur
        if len(cur) > 1:
            packets.append(bytes(cur))
        cur = bytearray([_header(ts)])

    for msg in messages:
        if not msg:
            continue
        if msg[0] == 0xF0:
            body = msg[1:-1] if msg[-1] == 0xF7 else msg[1:]
            # Start: header, timestamp, F0 — then as many data bytes as fit.
            if len(cur) + 2 > max_len:
                flush()
            cur += bytes([_ts_byte(ts), 0xF0])
            i = 0
            while i < len(body):
                room = max_len - len(cur)
                if room <= 0:
                    flush()
                    continue
                chunk = body[i:i + room]
                cur += chunk
                i += len(chunk)
            # End: timestamp + F7 must fit together.
            if len(cur) + 2 > max_len:
                flush()
            cur += bytes([_ts_byte(ts), 0xF7])
        else:
            need = 1 + len(msg)
            if len(cur) + need > max_len:
                flush()
            if len(cur) + need > max_len:
                raise ValueError("message does not fit in a single BLE packet")
            cur += bytes([_ts_byte(ts)]) + bytes(msg)
    flush()
    return packets
