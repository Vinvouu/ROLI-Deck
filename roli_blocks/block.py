"""High-level Block + BlockGroup APIs.

A `BlockGroup` opens a Transport, fetches the topology, and exposes the
discovered Blocks. Touch / button events arrive as callbacks.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ._bytecode import BITMAP_LED_PROGRAM_BYTECODE
from .led import Color, LightpadGrid, encode_program_upload
from .protocol import messages as M
from .protocol.opcodes import DeviceCommand, block_type_for_serial
from .transport import MidiPortPair, Transport, find_block_port_pairs


TouchHandler = Callable[["Block", M.Touch], None]
ButtonHandler = Callable[["Block", M.ControlButton], None]
LogHandler = Callable[["Block", str], None]


@dataclass
class Block:
    """One physical BLOCKS device, identified by serial."""

    serial: str
    topology_index: int
    battery_level: int = 0
    battery_charging: bool = False
    firmware_version: str = ""
    name: str = ""
    grid: LightpadGrid | None = None  # set by BlockGroup.load_bitmap_program()

    @property
    def block_type(self) -> str:
        return block_type_for_serial(self.serial)

    @property
    def is_lightpad(self) -> bool:
        return self.block_type == "lightpad"

    @property
    def battery_fraction(self) -> float:
        return self.battery_level / 31.0

    def __repr__(self) -> str:
        return (
            f"Block(serial={self.serial!r}, type={self.block_type}, "
            f"topo={self.topology_index}, battery={int(self.battery_fraction * 100)}%"
            f"{', charging' if self.battery_charging else ''})"
        )


class BlockGroup:
    """Opens a single BLOCKS connection (one master + DNA chain) and dispatches events."""

    def __init__(self, pair: MidiPortPair | None = None, *, transport=None) -> None:
        # Either a MIDI port pair (USB transport) or any object exposing the
        # Transport interface (send / poll_packets / wait_for_packet /
        # start_pinging / update_ping_targets / close), e.g. a BLE transport.
        if transport is None:
            if pair is None:
                raise ValueError("BlockGroup needs a MidiPortPair or a transport")
            transport = Transport(pair)
        self._transport = transport
        self._blocks: dict[int, Block] = {}  # by topology_index
        self._lock = threading.Lock()
        self._on_touch: list[TouchHandler] = []
        self._on_button: list[ButtonHandler] = []
        self._on_log: list[LogHandler] = []
        self._dispatch_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # PacketIndex must be monotonic per device for sharedDataChange — see
        # roli_LittleFootRemoteHeap.h. We wrap at the 10-bit PacketCounter range
        # the device acks with, since that's the field the device actually tracks.
        self._next_packet_index: dict[int, int] = {}
        self._packet_index_lock = threading.Lock()
        # Active touch tracking. The device drops incoming sharedDataChange
        # messages while it is actively reporting touches, so callers should
        # use `is_idle(block)` to gate flushes. The dispatch loop maintains
        # this count from touchStart / touchEnd events.
        self._active_touches: dict[int, set[int]] = {}  # topo_idx -> {touch_idx}

    # --------------------------------------------------- discovery / lifecycle

    @classmethod
    def auto_open(cls) -> "BlockGroup":
        pairs = find_block_port_pairs()
        if not pairs:
            raise RuntimeError(
                "No BLOCKS device found on MIDI bus. Is the device plugged in and "
                "is ROLI Dashboard / BLOCKS Code closed?"
            )
        return cls(pairs[0])

    def fetch_topology(self, *, timeout: float = 2.0) -> list[Block]:
        """Send requestTopology, wait for the response, populate `self.blocks`."""
        self._transport.send_command(DeviceCommand.requestTopologyMessage, 0)
        pkt = self._transport.wait_for_packet(
            lambda p: any(isinstance(m, M.TopologyMessage) for m in p.messages),
            timeout=timeout,
        )
        if pkt is None:
            raise TimeoutError("No topology response within timeout")

        with self._lock:
            self._blocks.clear()
            for msg in pkt.messages:
                if isinstance(msg, M.TopologyMessage):
                    for d in msg.devices:
                        self._blocks[d.topology_index] = Block(
                            serial=d.serial,
                            topology_index=d.topology_index,
                            battery_level=d.battery_level,
                            battery_charging=d.battery_charging,
                        )
                elif isinstance(msg, M.DeviceVersionMessage):
                    if msg.topology_index in self._blocks:
                        self._blocks[msg.topology_index].firmware_version = msg.version
                elif isinstance(msg, M.DeviceNameMessage):
                    if msg.topology_index in self._blocks:
                        self._blocks[msg.topology_index].name = msg.name

        return list(self._blocks.values())

    @property
    def blocks(self) -> list[Block]:
        with self._lock:
            return list(self._blocks.values())

    def block_at(self, topology_index: int) -> Block | None:
        return self._blocks.get(topology_index)

    @property
    def master(self) -> Block | None:
        with self._lock:
            if not self._blocks:
                return None
            return min(self._blocks.values(), key=lambda b: b.topology_index)

    # ------------------------------------------------------------------- API mode

    def begin_api_mode(self) -> None:
        """Send beginAPIMode to every known block and start the ping loop.

        Mirrors `ConnectedDeviceGroup::startApiModeOnConnectedBlocks`: we send
        endAPIMode before beginAPIMode to clear any stale state from a previous run.
        """
        if not self._blocks:
            raise RuntimeError("No blocks discovered — call fetch_topology() first.")
        master = self.master
        assert master is not None
        for idx in self._blocks:
            self._transport.send_command(DeviceCommand.endAPIMode, idx)
            self._transport.send_command(DeviceCommand.beginAPIMode, idx)
        self._transport.start_pinging(self._blocks.keys(), master_index=master.topology_index)

        if self._dispatch_thread is None:
            self._stop_event.clear()
            t = threading.Thread(target=self._dispatch_loop, name="roli-events", daemon=True)
            self._dispatch_thread = t
            t.start()

    def end_api_mode(self) -> None:
        for idx in list(self._blocks):
            try:
                self._transport.send_command(DeviceCommand.endAPIMode, idx)
            except Exception:
                pass

    # ----------------------------------------------------------------- LED grid

    def _next_pkt_idx(self, topology_index: int) -> int:
        """Allocate a fresh PacketIndex for sharedDataChange messages to a device."""
        with self._packet_index_lock:
            cur = self._next_packet_index.get(topology_index, 0)
            cur = (cur + 1) & 0x3FF  # wrap at 10-bit PacketCounter range
            self._next_packet_index[topology_index] = cur
            return cur

    def load_bitmap_program(self, block: Block) -> LightpadGrid:
        """Upload BitmapLEDProgram to a Lightpad and return the in-memory grid handle.

        Mirrors `BlockImplementation::loadProgram`: clears the whole heap to
        zero, then writes the program bytecode at offset 0. Without the clear
        step, residual bytes from a previous session remain in the data area
        and our diff encoder (which assumes zeros) sends mismatched updates.

        Raises if `block` is not a Lightpad. After this call, write pixels with
        `set_pixel(block, x, y, color)` and call `flush_grid(block)` to push.
        """
        if not block.is_lightpad:
            raise ValueError(f"{block.serial} is not a Lightpad")

        from .led import LIGHTPAD_HEAP_BYTES
        from .protocol.messages import SharedDataChangeBuilder

        # 1) Clear the whole heap to zero (program area + data area + slack).
        clear_idx = self._next_pkt_idx(block.topology_index)
        clear_payload = (
            SharedDataChangeBuilder(clear_idx)
            .fill(0, LIGHTPAD_HEAP_BYTES)
            .end(last_change=True)
        )
        self._transport.send(clear_payload, device_index=block.topology_index)

        # 2) Upload bytecode at heap offset 0.
        prog_idx = self._next_pkt_idx(block.topology_index)
        prog_payload = encode_program_upload(prog_idx, BITMAP_LED_PROGRAM_BYTECODE)
        self._transport.send(prog_payload, device_index=block.topology_index)

        # 3) Set up an in-memory mirror of the data area.
        grid = LightpadGrid(program_size=len(BITMAP_LED_PROGRAM_BYTECODE))
        block.grid = grid
        return grid

    def is_idle(self, block: Block) -> bool:
        """True when no touches are currently active on `block`.

        sharedDataChange is unreliable while a touch is in progress; callers
        that need every pixel update to land should gate flushes on this.
        """
        active = self._active_touches.get(block.topology_index)
        return not active

    def flush_grid(self, block: Block, *, only_when_idle: bool = False, full: bool = False) -> bool:
        """Send any pending pixel changes for a Lightpad. Returns True if sent.

        With ``only_when_idle=True``, the flush is skipped while any touches
        are active on the block.

        With ``full=True`` we always send the entire data region, not just the
        diff against the last send. Use this when the receiver is unreliable
        (the Lightpad drops sharedDataChange while it's busy reporting touches);
        full sends are larger but each one is self-sufficient, so any single
        successful delivery brings the device into sync.
        """
        if block.grid is None:
            return False
        if only_when_idle and not self.is_idle(block):
            return False
        idx = self._next_pkt_idx(block.topology_index)
        payload = block.grid.encode_diff(packet_index=idx, full=full)
        if payload is None:
            return False
        self._transport.send(payload, device_index=block.topology_index)
        return True

    def set_pixel(self, block: Block, x: int, y: int, color: Color) -> None:
        if block.grid is None:
            raise RuntimeError("Call load_bitmap_program(block) first.")
        block.grid.set_pixel(x, y, color)

    def fill(self, block: Block, color: Color) -> None:
        if block.grid is None:
            raise RuntimeError("Call load_bitmap_program(block) first.")
        block.grid.fill(color)

    # ----------------------------------------------------------------- callbacks

    def on_touch(self, handler: TouchHandler) -> TouchHandler:
        self._on_touch.append(handler)
        return handler

    def on_button(self, handler: ButtonHandler) -> ButtonHandler:
        self._on_button.append(handler)
        return handler

    def on_log(self, handler: LogHandler) -> LogHandler:
        self._on_log.append(handler)
        return handler

    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            for pkt in self._transport.poll_packets():
                blk = self._blocks.get(pkt.device_index)
                if blk is None:
                    continue
                for msg in pkt.messages:
                    if isinstance(msg, M.Touch):
                        active = self._active_touches.setdefault(pkt.device_index, set())
                        if msg.is_end:
                            active.discard(msg.touch_index)
                        elif msg.is_start:
                            # touch_start always reseats the active flag — if a
                            # previous touch_end was dropped, the next start for
                            # the same index would otherwise leave the set in
                            # an inconsistent state. We trust the device to send
                            # a start before any move/end for a given touch.
                            active.add(msg.touch_index)
                        else:
                            active.add(msg.touch_index)
                        for h in self._on_touch:
                            try:
                                h(blk, msg)
                            except Exception:
                                pass
                    elif isinstance(msg, M.ControlButton):
                        for h in self._on_button:
                            try:
                                h(blk, msg)
                            except Exception:
                                pass
                    elif isinstance(msg, M.LogMessage):
                        for h in self._on_log:
                            try:
                                h(blk, msg.text)
                            except Exception:
                                pass
            self._stop_event.wait(0.005)

    # --------------------------------------------------------------------- raw

    @property
    def transport(self) -> Transport:
        return self._transport

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        self._stop_event.set()
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=1.0)
        self.end_api_mode()
        self._transport.close()

    def __enter__(self) -> "BlockGroup":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
