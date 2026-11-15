"""Liaison avec le Lightpad : rendu des zones sur les LEDs, touchers, retour visuel.

Tout se passe dans des threads de la couche `roli_blocks` ; l'interface interroge
`status` et reçoit les appuis via le rappel `on_zone_press(col, row)`.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roli_blocks import Block, BlockGroup, Color, Touch, find_block_port_pairs  # noqa: E402

from .model import Page, hex_to_rgb, lighten, zone_at, zone_rect  # noqa: E402

PressHandler = Callable[[int, int], None]


class PadController:
    def __init__(
        self,
        get_page: Callable[[], Page],
        on_zone_press: PressHandler,
        feedback_enabled: Callable[[], bool],
        feedback_amount: Callable[[], float] = lambda: 0.6,
        debounce_ms: Callable[[], int] = lambda: 150,
    ) -> None:
        self._get_page = get_page
        self._on_zone_press = on_zone_press
        self._feedback_enabled = feedback_enabled
        self._feedback_amount = feedback_amount
        self._debounce_ms = debounce_ms

        self._group: BlockGroup | None = None
        self._lightpad: Block | None = None
        self._lock = threading.RLock()
        self._active: dict[int, tuple[int, int]] = {}  # touch index -> zone
        self._last_fire: dict[tuple[int, int], float] = {}
        self.status = "Pad non connecté"
        self.info = ""
        self.last_press: tuple[int, int] | None = None

    # ------------------------------------------------------------- connexion

    @property
    def connected(self) -> bool:
        return self._lightpad is not None

    def connect(self) -> bool:
        with self._lock:
            if self.connected:
                return True
            pairs = find_block_port_pairs()
            if not pairs:
                self.status = "Pad non détecté (câble USB ? ROLI Dashboard ouvert ?)"
                return False
            group = BlockGroup(pairs[0])
            try:
                blocks = None
                for _ in range(3):
                    try:
                        blocks = group.fetch_topology(timeout=2.0)
                        break
                    except TimeoutError:
                        continue
                if not blocks:
                    group.close()
                    self.status = "Le pad ne répond pas (touche-le pour le réveiller, puis Connecter)"
                    return False
                lightpad = next((b for b in blocks if b.is_lightpad), None)
                if lightpad is None:
                    group.close()
                    self.status = "Aucun Lightpad dans la topologie"
                    return False
                group.on_touch(self._on_touch)
                group.begin_api_mode()
                group.load_bitmap_program(lightpad)
                time.sleep(0.4)
            except Exception as exc:  # noqa: BLE001
                try:
                    group.close()
                except Exception:  # noqa: BLE001
                    pass
                self.status = f"Erreur de connexion : {exc}"
                return False
            self._group = group
            self._lightpad = lightpad
            self.info = f"{lightpad.serial} · firmware {lightpad.firmware_version or '?'} · batterie {int(lightpad.battery_fraction * 100)} %"
            self.status = "Pad connecté"
        self.render()
        return True

    def disconnect(self) -> None:
        with self._lock:
            group, lightpad = self._group, self._lightpad
            self._group = None
            self._lightpad = None
            self._active.clear()
        if group is None:
            return
        try:
            if lightpad is not None and lightpad.grid is not None:
                group.fill(lightpad, Color(0, 0, 0))
                group.flush_grid(lightpad, full=True)
                time.sleep(0.2)
        except Exception:  # noqa: BLE001
            pass
        try:
            group.close()
        except Exception:  # noqa: BLE001
            pass
        self.status = "Pad déconnecté"

    # ------------------------------------------------------------------ rendu

    def render(self) -> None:
        """Dessine la page active (zones colorées, zones touchées éclaircies)."""
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
                if page.get(c, r) is None or page.get(c, r).is_empty:  # type: ignore[union-attr]
                    self._paint(grid, zone_rect(c, r), hex_to_rgb(lighten("#000000", self._feedback_amount())))
            try:
                group.flush_grid(lightpad, full=True)
            except Exception as exc:  # noqa: BLE001
                self.status = f"Pad perdu : {exc}"
                self._group = None
                self._lightpad = None

    @staticmethod
    def _paint(grid, rect: tuple[int, int, int, int], rgb: tuple[int, int, int]) -> None:
        x0, y0, w, h = rect
        col = Color(*rgb)
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                grid.set_pixel(x, y, col)

    # --------------------------------------------------------------- touchers

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
                pass
        if changed and self._feedback_enabled():
            self.render()
