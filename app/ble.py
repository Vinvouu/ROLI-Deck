"""Bluetooth worker: one asyncio loop in its own thread for every bleak/WinRT call.

The GUI and the link manager call the synchronous methods below; each one
schedules a coroutine on the worker loop and waits for its result with a
timeout, so the Tk main loop is never blocked by Bluetooth work.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from dataclasses import dataclass
from typing import Awaitable, TypeVar

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from roli_blocks.ble_midi import MIDI_SERVICE_UUID
from roli_blocks.ble_transport import BleTransport

log = logging.getLogger(__name__)
T = TypeVar("T")


class BleError(Exception):
    """Error with a French, user-facing message."""


@dataclass(frozen=True)
class FoundPad:
    name: str
    address: str
    rssi: int

    @property
    def signal_label(self) -> str:
        if self.rssi >= -60:
            return "signal fort"
        if self.rssi >= -75:
            return "signal moyen"
        return "signal faible"


def _address_to_int(address: str) -> int:
    return int(address.replace(":", "").replace("-", ""), 16)


class BleWorker:
    """Owns the asyncio loop thread. Create once, share with every BleTransport."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="ble-worker", daemon=True)
        self._thread.start()

    def run(self, coro: Awaitable[T], timeout: float) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)

    # ------------------------------------------------------------ radio

    def bluetooth_state(self) -> str:
        """'on', 'off', 'absent' or 'unknown' (WinRT Radio API; 'unknown' off Windows)."""
        if sys.platform != "win32":
            return "unknown"

        async def _state() -> str:
            from winrt.windows.devices.radios import Radio, RadioKind, RadioState

            radios = await Radio.get_radios_async()
            bt = [r for r in radios if r.kind == RadioKind.BLUETOOTH]
            if not bt:
                return "absent"
            return "on" if any(r.state == RadioState.ON for r in bt) else "off"

        try:
            return self.run(_state(), timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.warning("bluetooth_state failed: %s", exc)
            return "unknown"

    def require_bluetooth(self) -> None:
        state = self.bluetooth_state()
        if state == "off":
            raise BleError("Le Bluetooth est désactivé dans Windows. Active-le dans les paramètres rapides.")
        if state == "absent":
            raise BleError("Aucun adaptateur Bluetooth n'a été trouvé sur cet ordinateur.")

    # ------------------------------------------------------------ scan

    def scan(self, timeout: float = 6.0) -> list[FoundPad]:
        """Every BLE MIDI device (or anything named Lightpad) in range, strongest first."""
        self.require_bluetooth()

        async def _scan() -> list[FoundPad]:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
            pads: list[FoundPad] = []
            for dev, adv in found.values():
                name = dev.name or adv.local_name or ""
                midi = MIDI_SERVICE_UUID in [u.lower() for u in adv.service_uuids]
                if midi or "lightpad" in name.lower():
                    pads.append(FoundPad(name or "Lightpad (sans nom)", dev.address, adv.rssi or -100))
            return sorted(pads, key=lambda p: -p.rssi)

        try:
            return self.run(_scan(), timeout=timeout + 10)
        except BleError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BleError(f"La recherche Bluetooth a échoué : {exc}") from exc

    def find(self, address: str, timeout: float = 5.0) -> BLEDevice | None:
        async def _find() -> BLEDevice | None:
            return await BleakScanner.find_device_by_address(address, timeout=timeout)

        return self.run(_find(), timeout=timeout + 10)

    # ------------------------------------------------------------ pairing (Windows)

    def pairing_state(self, address: str) -> tuple[bool, bool]:
        """(known_to_windows, is_paired). Always (False, False) off Windows."""
        if sys.platform != "win32":
            return False, False

        async def _state() -> tuple[bool, bool]:
            from winrt.windows.devices.bluetooth import BluetoothLEDevice

            dev = await BluetoothLEDevice.from_bluetooth_address_async(_address_to_int(address))
            if dev is None:
                return False, False
            paired = bool(dev.device_information.pairing.is_paired)
            dev.close()
            return True, paired

        try:
            return self.run(_state(), timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.warning("pairing_state failed: %s", exc)
            return False, False

    def unpair(self, address: str) -> str:
        """Remove the Windows bonding. Only call after the user confirmed."""

        async def _unpair() -> str:
            from winrt.windows.devices.bluetooth import BluetoothLEDevice

            dev = await BluetoothLEDevice.from_bluetooth_address_async(_address_to_int(address))
            if dev is None:
                return "inconnu de Windows"
            result = await dev.device_information.pairing.unpair_async()
            dev.close()
            return str(result.status)

        try:
            status = self.run(_unpair(), timeout=20)
        except Exception as exc:  # noqa: BLE001
            raise BleError(f"Impossible de retirer l'appairage : {exc}") from exc
        log.info("unpair %s -> %s", address, status)
        return status

    # ------------------------------------------------------------ connect

    def connect(self, address: str, on_disconnect, scan_timeout: float = 5.0) -> BleTransport:
        """Scan for the address, then open a BLOCKS transport over BLE. Raises BleError."""
        self.require_bluetooth()
        try:
            device = self.find(address, timeout=scan_timeout)
        except Exception as exc:  # noqa: BLE001
            raise BleError(f"La recherche du pad a échoué : {exc}") from exc
        if device is None:
            raise BleError("Pad introuvable en Bluetooth : éteint, hors de portée, branché en USB, "
                           "ou déjà connecté à un autre appareil.")
        transport = BleTransport(device, loop=self.loop, on_disconnect=on_disconnect)
        try:
            transport.open()
        except asyncio.TimeoutError as exc:
            raise BleError("Le pad ne répond pas à la connexion (délai dépassé). "
                           "Est-il déjà connecté à un autre appareil ?") from exc
        except Exception as exc:  # noqa: BLE001
            raise BleError(f"Connexion Bluetooth refusée : {exc}") from exc
        return transport


def is_bleak_client_available() -> bool:
    return BleakClient is not None
