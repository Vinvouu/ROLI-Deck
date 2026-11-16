"""BLOCKS protocol transport over BLE MIDI (same contract as `transport.Transport`).

The BLOCKS protocol is identical on USB and on Bluetooth: only the carrier
changes. This class fragments outgoing SysEx frames into BLE MIDI packets and
reassembles incoming notifications, so `BlockGroup` works unchanged on top of it.

All bleak calls run on an asyncio event loop that lives in its own thread. The
loop is either supplied by the caller (`loop=`, e.g. the application's BLE
worker) or created privately. Synchronous methods hand coroutines to that loop
with `run_coroutine_threadsafe`, so they never block the GUI thread for longer
than one BLE write.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Iterable
from typing import Callable

from bleak import BleakClient

from .ble_midi import MIDI_CHAR_UUID, BleMidiParser, encode_packets
from .protocol import messages as M
from .protocol import sysex
from .protocol.opcodes import DNA_PING_INTERVAL_MS, MASTER_PING_INTERVAL_MS, DeviceCommand

BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

log = logging.getLogger(__name__)


class BleTransport:
    """Drop-in replacement for `roli_blocks.transport.Transport` over Bluetooth."""

    def __init__(
        self,
        device,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        on_disconnect: Callable[[], None] | None = None,
        inter_packet_delay: float = 0.0,
        connect_timeout: float = 15.0,
    ) -> None:
        self._device = device
        self._delay = inter_packet_delay
        self._connect_timeout = connect_timeout
        self._own_loop = loop is None
        self._loop = loop or asyncio.new_event_loop()
        self._thread: threading.Thread | None = None
        self._client: BleakClient | None = None
        self._parser = BleMidiParser()
        self._packets: deque[M.DecodedPacket] = deque()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._closed = False
        self._on_disconnect = on_disconnect
        self.mtu = 23
        self.max_packet = 20
        self.stats = {"tx_frames": 0, "tx_packets": 0, "tx_bytes": 0, "rx_packets": 0,
                      "rx_frames": 0, "rx_acks": 0, "rx_bad": 0}
        self.disconnected = threading.Event()

        self._ping_targets: set[int] = set()
        self._master_index = 0
        self._ping_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------ lifecycle

    @property
    def address(self) -> str:
        return getattr(self._device, "address", str(self._device))

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected and not self.disconnected.is_set()

    def open(self) -> None:
        """Connect and subscribe. Raises on failure (timeout, refused, not found)."""
        if self._own_loop:
            self._thread = threading.Thread(target=self._loop.run_forever, name="ble-loop", daemon=True)
            self._thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        fut.result(timeout=self._connect_timeout + 10)

    async def _connect(self) -> None:
        client = BleakClient(self._device, disconnected_callback=self._handle_disconnect,
                             timeout=self._connect_timeout)
        await client.connect()
        self._client = client
        self.mtu = client.mtu_size
        self.max_packet = max(20, self.mtu - 3)
        await client.start_notify(MIDI_CHAR_UUID, self._on_notify)
        log.info("BLE transport ready on %s: MTU=%d -> %d-byte MIDI packets",
                 self.address, self.mtu, self.max_packet)

    def _handle_disconnect(self, _client) -> None:
        if self._closed:
            log.info("BLE link closed (%s)", self.address)
        else:
            log.warning("BLE link dropped (%s)", self.address)
        self.disconnected.set()
        if self._on_disconnect and not self._closed:
            try:
                self._on_disconnect()
            except Exception:  # noqa: BLE001
                log.exception("on_disconnect callback failed")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        if self._ping_thread is not None:
            self._ping_thread.join(timeout=1.0)

        async def _close() -> None:
            if self._client is not None and self._client.is_connected:
                try:
                    await self._client.stop_notify(MIDI_CHAR_UUID)
                except Exception:  # noqa: BLE001
                    pass
                await self._client.disconnect()

        try:
            asyncio.run_coroutine_threadsafe(_close(), self._loop).result(timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.warning("BLE close: %s", exc)
        if self._own_loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ------------------------------------------------------------ battery

    def read_battery(self, timeout: float = 5.0) -> int | None:
        """Battery level in percent via the standard Battery Service, or None."""
        if not self.is_connected:
            return None

        async def _read() -> int | None:
            assert self._client is not None
            data = await self._client.read_gatt_char(BATTERY_LEVEL_CHAR_UUID)
            return int(data[0]) if data else None

        try:
            return asyncio.run_coroutine_threadsafe(_read(), self._loop).result(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.debug("battery read failed: %s", exc)
            return None

    # ------------------------------------------------------------ receive

    def _on_notify(self, _char, data: bytearray) -> None:
        self.stats["rx_packets"] += 1
        try:
            events = self._parser.feed(data)
        except ValueError:
            self.stats["rx_bad"] += 1
            return
        for ev in events:
            if not ev.is_sysex:
                continue
            try:
                frame = sysex.decode(ev.data)
            except sysex.FrameError:
                self.stats["rx_bad"] += 1
                continue
            if not frame.from_device:
                continue
            try:
                packet = M.decode_packet(frame.device_index, frame.payload)
            except Exception:  # noqa: BLE001
                self.stats["rx_bad"] += 1
                continue
            self.stats["rx_frames"] += 1
            self.stats["rx_acks"] += sum(isinstance(m, M.PacketACK) for m in packet.messages)
            with self._lock:
                self._packets.append(packet)

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

    # ------------------------------------------------------------ send

    def send(self, payload: bytes | bytearray, device_index: int = 0) -> None:
        if self._closed or self._client is None:
            raise RuntimeError("transport closed")
        if self.disconnected.is_set():
            raise RuntimeError("BLE link is down")
        frame = sysex.encode(device_index, payload, from_host=True)
        packets = encode_packets([frame], self.max_packet)
        with self._send_lock:
            fut = asyncio.run_coroutine_threadsafe(self._write_all(packets), self._loop)
            fut.result(timeout=10)
        self.stats["tx_frames"] += 1
        self.stats["tx_packets"] += len(packets)
        self.stats["tx_bytes"] += len(frame)

    async def _write_all(self, packets: list[bytes]) -> None:
        assert self._client is not None
        for pkt in packets:
            await self._client.write_gatt_char(MIDI_CHAR_UUID, pkt, response=False)
            if self._delay:
                await asyncio.sleep(self._delay)

    def send_command(self, command: DeviceCommand, device_index: int = 0) -> None:
        self.send(M.build_device_command(command), device_index)

    # ------------------------------------------------------------ ping (mirrors the USB transport)

    def start_pinging(self, topology_indices: Iterable[int], *, master_index: int = 0) -> None:
        self._ping_targets = set(topology_indices)
        self._master_index = master_index
        if self._ping_thread is not None:
            return
        self._stop_event.clear()
        self._ping_thread = threading.Thread(target=self._ping_loop, name="ble-ping", daemon=True)
        self._ping_thread.start()

    def update_ping_targets(self, topology_indices: Iterable[int], *, master_index: int) -> None:
        self._ping_targets = set(topology_indices)
        self._master_index = master_index

    def _ping_loop(self) -> None:
        last_sent: dict[int, float] = {}
        while not self._stop_event.is_set():
            now = time.monotonic()
            for idx in tuple(self._ping_targets):
                interval = (MASTER_PING_INTERVAL_MS if idx == self._master_index else DNA_PING_INTERVAL_MS) / 1000
                if now - last_sent.get(idx, 0.0) >= interval:
                    try:
                        self.send_command(DeviceCommand.ping, idx)
                    except Exception:  # noqa: BLE001
                        pass
                    last_sent[idx] = now
            self._stop_event.wait(0.05)
