"""BLOCKS protocol transport over BLE MIDI (same interface as roli_blocks.transport.Transport).

The asyncio/bleak side runs in its own thread with its own event loop; the
synchronous `send()` hands packets to that loop and waits for the write to be
issued. Outgoing SysEx frames are fragmented per the BLE MIDI spec, incoming
notifications are reassembled, then decoded exactly like USB SysEx.
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
from bleak.backends.device import BLEDevice

from roli_blocks.protocol import messages as M
from roli_blocks.protocol import sysex
from roli_blocks.protocol.opcodes import DNA_PING_INTERVAL_MS, MASTER_PING_INTERVAL_MS, DeviceCommand

from .ble_midi import MIDI_CHAR_UUID, BleMidiParser, encode_packets


class BleTransport:
    """Drop-in replacement for `roli_blocks.transport.Transport` over Bluetooth."""

    def __init__(self, device: BLEDevice | str, *, inter_packet_delay: float = 0.0) -> None:
        self._device = device
        self._delay = inter_packet_delay
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ble-loop", daemon=True)
        self._client: BleakClient | None = None
        self._parser = BleMidiParser()
        self._packets: deque[M.DecodedPacket] = deque()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._closed = False
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

    def open(self, timeout: float = 20.0) -> None:
        self._thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        fut.result(timeout=timeout + 5)

    async def _connect(self) -> None:
        client = BleakClient(self._device, disconnected_callback=self._on_disconnect, timeout=20.0)
        await client.connect()
        self._client = client
        self.mtu = client.mtu_size
        self.max_packet = max(20, self.mtu - 3)
        await client.start_notify(MIDI_CHAR_UUID, self._on_notify)
        logging.info("BLE transport ready: MTU=%d -> %d-byte MIDI packets", self.mtu, self.max_packet)

    def _on_disconnect(self, _client) -> None:
        if self._closed:
            logging.info("BLE transport: link closed")
        else:
            logging.warning("BLE transport: link dropped")
        self.disconnected.set()

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
            logging.warning("BLE close: %s", exc)
        self._loop.call_soon_threadsafe(self._loop.stop)

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

    # ------------------------------------------------------------ ping (same as USB transport)

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
