"""Pad link manager: USB first, Bluetooth as fallback, automatic reconnection.

Responsibilities
- decide which carrier to use (`decide_action`, a pure function, unit-tested);
- open/close the BLOCKS session (`BlockGroup`) over the USB or BLE transport;
- render the active page on the LEDs, light touched zones, fire zone presses;
- reconnect with progressive back-off after a drop, re-sending the colours;
- expose a small, thread-safe status for the GUI (state, message, battery).

Everything runs on a background thread (`_loop`) plus the transport threads;
the GUI only reads attributes and calls the public methods.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roli_blocks import Block, BlockGroup, Color, Touch, find_block_port_pairs  # noqa: E402

from .ble import BleError, BleWorker  # noqa: E402
from .model import Page, hex_to_rgb, lighten, zone_at, zone_rect  # noqa: E402

log = logging.getLogger(__name__)

PressHandler = Callable[[int, int], None]

BACKOFF_STEPS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
BATTERY_POLL_S = 60.0
USB_RETRY_S = 5.0


class LinkState(str, Enum):
    DISCONNECTED = "disconnected"
    SEARCHING = "searching"
    USB = "usb"
    BLE = "ble"

    @property
    def label(self) -> str:
        return {
            LinkState.DISCONNECTED: "Déconnecté",
            LinkState.SEARCHING: "Recherche du pad…",
            LinkState.USB: "Connecté en USB",
            LinkState.BLE: "Connecté en Bluetooth",
        }[self]


def decide_action(state: LinkState, usb_present: bool, ble_configured: bool, ble_due: bool) -> str:
    """Pure policy: what the manager should do on this tick.

    Returns one of: "keep", "drop_usb", "connect_usb", "switch_to_usb",
    "connect_ble", "wait_ble", "idle".
    """
    if state == LinkState.USB:
        return "keep" if usb_present else "drop_usb"
    if state == LinkState.BLE:
        return "switch_to_usb" if usb_present else "keep"
    # Disconnected or searching.
    if usb_present:
        return "connect_usb"
    if ble_configured:
        return "connect_ble" if ble_due else "wait_ble"
    return "idle"


def backoff_delay(attempt: int) -> float:
    return BACKOFF_STEPS[min(max(attempt, 0), len(BACKOFF_STEPS) - 1)]


class PadController:
    def __init__(
        self,
        get_page: Callable[[], Page],
        on_zone_press: PressHandler,
        feedback_enabled: Callable[[], bool],
        feedback_amount: Callable[[], float] = lambda: 0.6,
        debounce_ms: Callable[[], int] = lambda: 150,
        get_ble_address: Callable[[], str | None] = lambda: None,
        ble_worker: BleWorker | None = None,
    ) -> None:
        self._get_page = get_page
        self._on_zone_press = on_zone_press
        self._feedback_enabled = feedback_enabled
        self._feedback_amount = feedback_amount
        self._debounce_ms = debounce_ms
        self._get_ble_address = get_ble_address
        self.ble = ble_worker or BleWorker()

        self._group: BlockGroup | None = None
        self._transport = None
        self._lightpad: Block | None = None
        self._lock = threading.RLock()
        self._active: dict[int, tuple[int, int]] = {}
        self._last_fire: dict[tuple[int, int], float] = {}

        self.state = LinkState.DISCONNECTED
        self.message = "Aucun pad : branche-le en USB ou choisis un pad Bluetooth."
        self.info = ""
        self.battery: int | None = None
        self.charging = False
        self.last_press: tuple[int, int] | None = None
        self.on_state_change: Callable[[], None] | None = None

        self._ble_attempt = 0
        self._next_ble_try = 0.0
        self._next_usb_try = 0.0
        self._next_battery = 0.0
        self._manual_disconnect = False
        self._ble_suspended = False  # set by the user disconnecting on purpose
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ public API

    @property
    def connected(self) -> bool:
        return self._lightpad is not None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="pad-link", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._close_session(final=True)

    def request_ble_now(self) -> None:
        """Forget the back-off and try the configured Bluetooth pad immediately."""
        self._ble_suspended = False
        self._ble_attempt = 0
        self._next_ble_try = 0.0
        self._wake.set()

    def disconnect(self) -> None:
        """User-initiated disconnect: close the session and stop retrying BLE."""
        self._ble_suspended = True
        self._close_session()
        self._set_state(LinkState.DISCONNECTED, "Déconnecté à ta demande.")

    def resume(self) -> None:
        self._ble_suspended = False
        self._next_usb_try = 0.0
        self.request_ble_now()

    def connect_ble(self, address: str) -> None:
        """Connect to a specific pad right now (from the Bluetooth dialog). Raises BleError."""
        if find_block_port_pairs():
            raise BleError("Le pad est branché en USB : l'USB est utilisé en priorité. "
                           "Débranche-le pour passer en Bluetooth.")
        if self.state == LinkState.BLE and self._transport is not None \
                and getattr(self._transport, "address", "").lower() == address.lower():
            return
        self._close_session()
        self._ble_suspended = False
        self._open_ble(address)  # raises BleError with a French message

    # ------------------------------------------------------------ manager loop

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("link manager tick failed")
            self._wake.wait(1.0)
            self._wake.clear()

    def _tick(self) -> None:
        now = time.monotonic()
        usb_present = bool(find_block_port_pairs())
        ble_address = self._get_ble_address()
        ble_configured = bool(ble_address) and not self._ble_suspended
        if self.connected and self._transport is not None and \
                getattr(self._transport, "disconnected", None) is not None and \
                self._transport.disconnected.is_set():
            self._on_drop("La liaison Bluetooth a été coupée.")
        action = decide_action(self.state, usb_present, ble_configured, now >= self._next_ble_try)

        if action == "keep":
            if self.state == LinkState.BLE and now >= self._next_battery:
                self._next_battery = now + BATTERY_POLL_S
                level = self._transport.read_battery() if self._transport else None
                if level is not None:
                    self.battery = level
            return
        if action == "drop_usb":
            self._on_drop("Pad USB débranché.")
            return
        if action == "switch_to_usb":
            log.info("USB plugged in while on Bluetooth: switching to USB")
            self._close_session()
            self._set_state(LinkState.DISCONNECTED, "Pad branché en USB : bascule…")
            action = "connect_usb"
        if action == "connect_usb":
            if now < self._next_usb_try:
                return
            self._set_state(LinkState.SEARCHING, "Connexion USB…")
            if not self._open_usb():
                self._next_usb_try = now + USB_RETRY_S
                self._set_state(LinkState.DISCONNECTED, self.message)
            return
        if action == "connect_ble":
            assert ble_address
            self._set_state(LinkState.SEARCHING, f"Recherche Bluetooth de {self._pad_label()}…")
            try:
                self._open_ble(ble_address)
            except BleError as exc:
                delay = backoff_delay(self._ble_attempt)
                self._ble_attempt += 1
                self._next_ble_try = time.monotonic() + delay
                self._set_state(LinkState.DISCONNECTED, f"{exc} Nouvel essai dans {delay:.0f} s.")
            return
        if action == "wait_ble":
            return
        if action == "idle":
            if self.state != LinkState.DISCONNECTED:
                self._set_state(LinkState.DISCONNECTED, self.message)
            elif self._ble_suspended:
                pass
            else:
                self.message = "Aucun pad : branche-le en USB ou choisis un pad Bluetooth."

    def _pad_label(self) -> str:
        addr = self._get_ble_address() or ""
        return addr

    # ------------------------------------------------------------ session open / close

    def _open_usb(self) -> bool:
        pairs = find_block_port_pairs()
        if not pairs:
            self.message = "Pad USB non détecté."
            return False
        group = BlockGroup(pairs[0])
        try:
            if not self._start_session(group, LinkState.USB):
                return False
        except Exception as exc:  # noqa: BLE001
            log.warning("USB session failed: %s", exc)
            self.message = f"Erreur de connexion USB : {exc}"
            try:
                group.close()
            except Exception:  # noqa: BLE001
                pass
            return False
        self.charging = True
        return True

    def _open_ble(self, address: str) -> None:
        transport = self.ble.connect(address, on_disconnect=self._wake.set)
        group = BlockGroup(transport=transport)
        try:
            if not self._start_session(group, LinkState.BLE, transport):
                raise BleError(self.message)
        except BleError:
            raise
        except Exception as exc:  # noqa: BLE001
            try:
                group.close()
            except Exception:  # noqa: BLE001
                pass
            raise BleError(f"Le pad ne répond pas au protocole BLOCKS en Bluetooth : {exc}") from exc
        self._ble_attempt = 0
        self._next_battery = 0.0

    def _start_session(self, group: BlockGroup, state: LinkState, transport=None) -> bool:
        blocks = None
        for _ in range(3):
            try:
                blocks = group.fetch_topology(timeout=2.0)
                break
            except TimeoutError:
                continue
        if not blocks:
            group.close()
            self.message = "Le pad ne répond pas (touche-le pour le réveiller)."
            return False
        lightpad = next((b for b in blocks if b.is_lightpad), None)
        if lightpad is None:
            group.close()
            self.message = "Aucun Lightpad dans la topologie."
            return False
        group.on_touch(self._on_touch)
        group.begin_api_mode()
        group.load_bitmap_program(lightpad)
        time.sleep(0.4)
        with self._lock:
            self._group = group
            self._transport = transport
            self._lightpad = lightpad
            self._active.clear()
            self.battery = int(lightpad.battery_fraction * 100)
            self.charging = lightpad.battery_charging
            self.info = f"{lightpad.serial} · firmware {lightpad.firmware_version or '?'}"
        self._set_state(state, state.label)
        log.info("session open over %s: %s", state.value, self.info)
        self.render()
        return True

    def _close_session(self, final: bool = False) -> None:
        with self._lock:
            group, lightpad = self._group, self._lightpad
            self._group = None
            self._transport = None
            self._lightpad = None
            self._active.clear()
        if group is None:
            return
        try:
            if lightpad is not None and lightpad.grid is not None:
                group.fill(lightpad, Color(0, 0, 0))
                group.flush_grid(lightpad, full=True)
                time.sleep(0.15)
        except Exception:  # noqa: BLE001
            pass
        try:
            group.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("close failed: %s", exc)
        if final:
            self.ble.stop()

    def _on_drop(self, why: str) -> None:
        log.warning("link dropped: %s", why)
        self._close_session()
        self._ble_attempt = 0
        self._next_ble_try = 0.0
        self._set_state(LinkState.DISCONNECTED, why + " Reconnexion automatique…")

    def _set_state(self, state: LinkState, message: str) -> None:
        changed = state != self.state or message != self.message
        self.state = state
        self.message = message
        if changed and self.on_state_change:
            try:
                self.on_state_change()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ rendering

    def render(self) -> None:
        """Draw the active page (coloured zones, touched zones lightened)."""
        with self._lock:
            group, lightpad = self._group, self._lightpad
            if group is None or lightpad is None or lightpad.grid is None:
                return
            page = self._get_page()
            grid = lightpad.grid
            grid.fill(Color(0, 0, 0))
            pressed = set(self._active.values()) if self._feedback_enabled() else set()
            for key, zone in page.zones.items():
                if zone.is_empty:
                    continue
                c, r = (int(v) for v in key.split(","))
                color = lighten(zone.color, self._feedback_amount()) if (c, r) in pressed else zone.color
                self._paint(grid, zone_rect(c, r), hex_to_rgb(color))
            for (c, r) in pressed:
                z = page.get(c, r)
                if z is None or z.is_empty:
                    self._paint(grid, zone_rect(c, r), hex_to_rgb(lighten("#000000", self._feedback_amount())))
            try:
                group.flush_grid(lightpad, full=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("render failed, dropping link: %s", exc)
                self._wake.set()
                if self._transport is not None and hasattr(self._transport, "disconnected"):
                    self._transport.disconnected.set()
                else:
                    self._on_drop("Envoi vers le pad impossible.")

    @staticmethod
    def _paint(grid, rect: tuple[int, int, int, int], rgb: tuple[int, int, int]) -> None:
        x0, y0, w, h = rect
        col = Color(*rgb)
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                grid.set_pixel(x, y, col)

    # ------------------------------------------------------------ touches

    def _on_touch(self, _blk: Block, t: Touch) -> None:
        zone = zone_at(t.x_normalized, t.y_normalized)
        changed = False
        fire: tuple[int, int] | None = None
        with self._lock:
            if t.is_start:
                if zone is not None:
                    self._active[t.touch_index] = zone
                    changed = True
                    now = time.monotonic()
                    if now - self._last_fire.get(zone, 0.0) >= self._debounce_ms() / 1000:
                        self._last_fire[zone] = now
                        fire = zone
            elif t.is_end:
                if self._active.pop(t.touch_index, None) is not None:
                    changed = True
        if fire is not None:
            self.last_press = fire
            try:
                self._on_zone_press(*fire)
            except Exception:  # noqa: BLE001
                log.exception("zone press handler failed")
        if changed and self._feedback_enabled():
            self.render()
