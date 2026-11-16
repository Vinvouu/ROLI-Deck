"""Shared helpers for the BLE diagnostics: logging, CLI arguments, device lookup,
Windows pairing inspection (WinRT) and the BLE MIDI connection wrapper."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .ble_midi import MIDI_CHAR_UUID, MIDI_SERVICE_UUID, BleMidiParser, MidiEvent

LOG_DIR = Path(__file__).resolve().parent / "logs"
DEFAULT_NAME = "Lightpad"


# ------------------------------------------------------------------ logging / CLI


def setup_logging(script_name: str) -> Path:
    """Log to the console and to logs/<script>_<timestamp>.log (UTF-8)."""
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"{script_name}_{datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
    logging.getLogger("bleak").setLevel(logging.WARNING)
    logging.info("log file: %s", path)
    return path


def add_device_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--address", help="BLE address of the pad (skips the name filter)")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"name filter (default '{DEFAULT_NAME}')")
    ap.add_argument("--scan-timeout", type=float, default=10.0, help="seconds to look for the pad")


def now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def address_to_int(address: str) -> int:
    return int(address.replace(":", "").replace("-", ""), 16)


# ------------------------------------------------------------------ discovery


def looks_like_lightpad(device: BLEDevice, adv: AdvertisementData, name_filter: str) -> bool:
    name = (device.name or adv.local_name or "").lower()
    has_midi = MIDI_SERVICE_UUID in [u.lower() for u in adv.service_uuids]
    return (name_filter.lower() in name) or (has_midi and "lightpad" in name)


async def find_pad(args: argparse.Namespace) -> BLEDevice | None:
    """Scan until the pad shows up (by address or by name + MIDI service)."""
    if args.address:
        logging.info("looking for %s ...", args.address)
        return await BleakScanner.find_device_by_address(args.address, timeout=args.scan_timeout)
    logging.info("scanning for a device named like '%s' advertising BLE MIDI ...", args.name)
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: looks_like_lightpad(d, adv, args.name), timeout=args.scan_timeout
    )


# ------------------------------------------------------------------ Windows pairing (WinRT)


async def paired_ble_devices() -> list[tuple[str, str, bool]]:
    """(name, id, is_paired) for every BLE device Windows knows as paired."""
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    # The (aqs_filter, additional_properties) overload; None = no extra properties.
    infos = await DeviceInformation.find_all_async(selector, None)
    return [(i.name, i.id, bool(i.pairing.is_paired)) for i in infos]


async def pairing_state(address: str) -> tuple[bool, bool, str]:
    """(known_to_windows, is_paired, protection_level) for a BLE address."""
    from winrt.windows.devices.bluetooth import BluetoothLEDevice

    dev = await BluetoothLEDevice.from_bluetooth_address_async(address_to_int(address))
    if dev is None:
        return False, False, "n/a"
    pairing = dev.device_information.pairing
    level = str(pairing.protection_level)
    is_paired = bool(pairing.is_paired)
    dev.close()
    return True, is_paired, level


async def unpair(address: str) -> str:
    """Remove the Windows pairing (bonding) of a BLE address. Returns the result status."""
    from winrt.windows.devices.bluetooth import BluetoothLEDevice

    dev = await BluetoothLEDevice.from_bluetooth_address_async(address_to_int(address))
    if dev is None:
        return "device unknown to Windows"
    result = await dev.device_information.pairing.unpair_async()
    dev.close()
    return str(result.status)


# ------------------------------------------------------------------ MIDI connection


class MidiConnection:
    """One BLE MIDI link: connect, subscribe, decode, report disconnects."""

    def __init__(
        self,
        device: BLEDevice | str,
        on_event: Callable[[MidiEvent], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.device = device
        self.parser = BleMidiParser()
        self.on_event = on_event
        self.on_disconnect = on_disconnect
        self.connected_at: float | None = None
        self.disconnected_at: float | None = None
        self.packets = 0
        self.bytes = 0
        self.disconnected = asyncio.Event()
        self._closing = False
        self.client = BleakClient(device, disconnected_callback=self._disconnected, timeout=20.0)

    @property
    def mtu(self) -> int:
        return self.client.mtu_size

    async def connect(self) -> None:
        t0 = time.monotonic()
        logging.info("[%s] connecting (no pairing requested) ...", now())
        await self.client.connect()
        self.connected_at = time.monotonic()
        self.disconnected.clear()
        logging.info("[%s] connected in %.2f s, MTU=%d", now(), self.connected_at - t0, self.mtu)
        svcs = [s.uuid for s in self.client.services]
        logging.info("services: %s", ", ".join(svcs))
        if MIDI_SERVICE_UUID not in [s.lower() for s in svcs]:
            logging.warning("BLE MIDI service not found on this device!")
        char = self.client.services.get_characteristic(MIDI_CHAR_UUID)
        if char is None:
            raise RuntimeError("BLE MIDI characteristic not found")
        logging.info("MIDI characteristic properties: %s", ", ".join(char.properties))
        await self.client.start_notify(MIDI_CHAR_UUID, self._notify)
        logging.info("[%s] subscribed to MIDI notifications", now())

    def _notify(self, _char, data: bytearray) -> None:
        self.packets += 1
        self.bytes += len(data)
        try:
            events = self.parser.feed(data)
        except ValueError as exc:
            logging.warning("undecodable packet %s: %s", data.hex(" "), exc)
            return
        for ev in events:
            if self.on_event:
                self.on_event(ev)

    def _disconnected(self, _client) -> None:
        self.disconnected_at = time.monotonic()
        up = (self.disconnected_at - self.connected_at) if self.connected_at else 0.0
        if self._closing:
            logging.info("[%s] link closed by us after %.1f s", now(), up)
        else:
            logging.warning("[%s] DISCONNECTED by the pad or Windows after %.1f s "
                            "(bleak gives no reason code on Windows)", now(), up)
        self.disconnected.set()
        if self.on_disconnect:
            self.on_disconnect()

    async def write(self, packet: bytes) -> None:
        await self.client.write_gatt_char(MIDI_CHAR_UUID, packet, response=False)

    async def keepalive_read(self) -> int:
        """Read the MIDI characteristic (spec: returns an empty payload). Keeps the link busy."""
        data = await self.client.read_gatt_char(MIDI_CHAR_UUID)
        return len(data)

    async def close(self) -> None:
        self._closing = True
        try:
            if self.client.is_connected:
                await self.client.disconnect()
        except Exception as exc:  # noqa: BLE001
            logging.warning("disconnect error: %s", exc)


def log_event(ev: MidiEvent) -> None:
    logging.info("[%s] ts=%4d  %s", now(), ev.timestamp, ev.describe())


def run(coro_factory: Callable[[], Awaitable[None]]) -> None:
    try:
        asyncio.run(coro_factory())
    except KeyboardInterrupt:
        logging.info("stopped by user")
