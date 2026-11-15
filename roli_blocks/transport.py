"""rtmidi-backed MIDI transport with SysEx framing and a ping daemon thread.

Concurrency model:
  - rtmidi delivers callbacks on its own thread; we accumulate into a deque
    that the consumer drains via `poll_packets()`.
  - A daemon thread sends a `ping` device-command every 400ms (master interval)
    so the device keeps API mode alive (its timeout is 5 seconds).
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

import rtmidi

from .protocol import messages as M
from .protocol import sysex
from .protocol.opcodes import (
    DNA_PING_INTERVAL_MS,
    MASTER_PING_INTERVAL_MS,
    DeviceCommand,
)


def _looks_like_block(name: str) -> bool:
    return " BLOCK" in name or " Block" in name


# python-rtmidi suffixes every port name with its index in the port list
# ("ROLI Lightpad BLOCK 0"). On Windows (WinMM backend) that index differs
# between the input list and the output list (e.g. "Microsoft GS Wavetable
# Synth" occupies output slot 0), so an exact-name comparison never pairs
# the Lightpad's input with its output. Strip the suffix before pairing.
_TRAILING_INDEX = re.compile(r"\s+\d+$")


def _normalise_port_name(name: str) -> str:
    return _TRAILING_INDEX.sub("", name).strip()


@dataclass(frozen=True, slots=True)
class MidiPortPair:
    in_index: int
    in_name: str
    out_index: int
    out_name: str


def find_block_port_pairs() -> list[MidiPortPair]:
    """Locate input/output port pairs whose names look like a BLOCKS device.

    Mirrors `roli_MIDIDeviceDetector.findDevices` — name match + occurrence
    pairing — but compares names with their trailing port index removed
    (see `_normalise_port_name`) so that it also works on Windows.
    """
    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()
    try:
        in_ports = midi_in.get_ports()
        out_ports = midi_out.get_ports()
    finally:
        del midi_in
        del midi_out

    pairs: list[MidiPortPair] = []
    used_outputs: set[int] = set()
    for i_idx, i_name in enumerate(in_ports):
        if not _looks_like_block(i_name):
            continue
        wanted = _normalise_port_name(i_name)
        for o_idx, o_name in enumerate(out_ports):
            if o_idx in used_outputs:
                continue
            if _looks_like_block(o_name) and _normalise_port_name(o_name) == wanted:
                pairs.append(MidiPortPair(i_idx, i_name, o_idx, o_name))
                used_outputs.add(o_idx)
                break
    return pairs


class Transport:
    """One open BLOCKS connection — wraps a single (input, output) MIDI port pair.

    Use `send(payload, device_index)` to send, `poll_packets()` to drain
    inbound device→host SysEx packets that have already been validated.
    """

    def __init__(self, pair: MidiPortPair) -> None:
        self._pair = pair
        self._in = rtmidi.MidiIn()
        self._out = rtmidi.MidiOut()
        self._in.ignore_types(sysex=False, timing=True, active_sense=True)
        self._in.open_port(pair.in_index)
        self._out.open_port(pair.out_index)

        self._packets: deque[M.DecodedPacket] = deque()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # serialise concurrent SysEx sends
        self._closed = False

        # Ping bookkeeping
        self._ping_targets: set[int] = set()  # topology indices to keep pinging
        self._ping_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._in.set_callback(self._on_midi)

    # ------------------------------------------------------------------ MIDI

    def _on_midi(self, event, _data) -> None:
        msg, _delta = event
        raw = bytes(msg)
        try:
            frame = sysex.decode(raw)
        except sysex.FrameError:
            return
        if not frame.from_device:
            return
        try:
            packet = M.decode_packet(frame.device_index, frame.payload)
        except Exception:
            return
        with self._lock:
            self._packets.append(packet)

    def send(self, payload: bytes | bytearray, device_index: int = 0) -> None:
        if self._closed:
            raise RuntimeError("transport closed")
        frame = sysex.encode(device_index, payload, from_host=True)
        # Serialise sends across threads. The ping daemon and the user's
        # callback (running on the dispatch thread) both invoke this; without
        # this lock concurrent send_message calls on macOS were getting
        # silently coalesced/dropped by the device.
        with self._send_lock:
            self._out.send_message(list(frame))

    def send_command(self, command: DeviceCommand, device_index: int = 0) -> None:
        self.send(M.build_device_command(command), device_index)

    # ------------------------------------------------------------- consumer

    def poll_packets(self) -> list[M.DecodedPacket]:
        with self._lock:
            out = list(self._packets)
            self._packets.clear()
        return out

    def wait_for_packet(self, predicate: Callable[[M.DecodedPacket], bool], timeout: float) -> M.DecodedPacket | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for pkt in self.poll_packets():
                if predicate(pkt):
                    return pkt
            time.sleep(0.005)
        return None

    # ------------------------------------------------------------------ ping

    def start_pinging(self, topology_indices: Iterable[int], *, master_index: int = 0) -> None:
        """Start the daemon thread that pings each given topology index.

        `master_index` gets the fast (400ms) interval; others get the DNA (1666ms) interval.
        """
        self._ping_targets = set(topology_indices)
        self._master_index = master_index
        if self._ping_thread is not None:
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._ping_loop, name="roli-ping", daemon=True)
        self._ping_thread = t
        t.start()

    def update_ping_targets(self, topology_indices: Iterable[int], *, master_index: int) -> None:
        self._ping_targets = set(topology_indices)
        self._master_index = master_index

    def _ping_loop(self) -> None:
        last_sent: dict[int, float] = {}
        while not self._stop_event.is_set():
            now = time.monotonic()
            for idx in tuple(self._ping_targets):
                interval = (
                    MASTER_PING_INTERVAL_MS / 1000.0
                    if idx == self._master_index
                    else DNA_PING_INTERVAL_MS / 1000.0
                )
                if now - last_sent.get(idx, 0.0) >= interval:
                    try:
                        self.send_command(DeviceCommand.ping, idx)
                    except Exception:
                        pass
                    last_sent[idx] = now
            self._stop_event.wait(0.05)

    # ----------------------------------------------------------------- close

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        if self._ping_thread is not None:
            self._ping_thread.join(timeout=1.0)
        try:
            self._in.close_port()
        except Exception:
            pass
        try:
            self._out.close_port()
        except Exception:
            pass
        del self._in
        del self._out

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
