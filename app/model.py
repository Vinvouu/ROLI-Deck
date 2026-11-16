"""Modèle de données : grille de zones, pages, profils et configuration.

Géométrie du pad : 15 × 15 LEDs. Chaque zone est un carré de 3 × 3 LEDs, les
zones étant séparées d'une LED (3 + 1 + 3 + 1 + 3 + 1 + 3 = 15), soit une grille
de 4 × 4 = 16 zones.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

GRID_LEDS = 15
N_COLS = 4
N_ROWS = 4
ZONE_SIZE = 3
GAP = 1

CONFIG_VERSION = 1


# --------------------------------------------------------------------------- géométrie


def bands() -> list[tuple[int, int]]:
    """Positions (début, longueur) des 4 bandes de 3 LEDs sur un axe de 15 LEDs."""
    return [(i * (ZONE_SIZE + GAP), ZONE_SIZE) for i in range(N_COLS)]


def zone_rect(col: int, row: int) -> tuple[int, int, int, int]:
    """Rectangle (x, y, largeur, hauteur) en LEDs de la zone (col, row)."""
    xs, ys = bands(), bands()
    x, w = xs[col]
    y, h = ys[row]
    return x, y, w, h


def zone_at(x_norm: float, y_norm: float) -> tuple[int, int] | None:
    """Zone sous un toucher (coordonnées 0..1), ou None dans un interstice."""
    px = min(int(x_norm * GRID_LEDS), GRID_LEDS - 1)
    py = min(int(y_norm * GRID_LEDS), GRID_LEDS - 1)
    col = next((i for i, (s, n) in enumerate(bands()) if s <= px < s + n), None)
    row = next((i for i, (s, n) in enumerate(bands()) if s <= py < s + n), None)
    if col is None or row is None:
        return None
    return col, row


def zone_key(col: int, row: int) -> str:
    return f"{col},{row}"


def parse_zone_key(key: str) -> tuple[int, int]:
    c, r = key.split(",")
    return int(c), int(r)


# --------------------------------------------------------------------------- couleurs


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return (0, 0, 0)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def lighten(hex_color: str, amount: float = 0.6) -> str:
    """Éclaircit vers le blanc (retour visuel à l'appui)."""
    r, g, b = hex_to_rgb(hex_color)
    if (r, g, b) == (0, 0, 0):
        r, g, b = (60, 60, 60)
    return rgb_to_hex((
        int(r + (255 - r) * amount),
        int(g + (255 - g) * amount),
        int(b + (255 - b) * amount),
    ))


# --------------------------------------------------------------------------- données


@dataclass
class Zone:
    label: str = ""
    action: str = "none"
    params: dict = field(default_factory=dict)
    color: str = "#000000"

    @property
    def is_empty(self) -> bool:
        return self.action == "none"


@dataclass
class Page:
    name: str = "Page 1"
    zones: dict[str, Zone] = field(default_factory=dict)

    def zone(self, col: int, row: int) -> Zone:
        return self.zones.setdefault(zone_key(col, row), Zone())

    def get(self, col: int, row: int) -> Zone | None:
        return self.zones.get(zone_key(col, row))

    def clear(self, col: int, row: int) -> None:
        self.zones.pop(zone_key(col, row), None)


@dataclass
class Profile:
    name: str = "Défaut"
    pages: list[Page] = field(default_factory=lambda: [Page()])


@dataclass
class Config:
    version: int = CONFIG_VERSION
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])
    active_profile: int = 0
    active_page: int = 0
    feedback: bool = True
    feedback_amount: float = 0.6
    debounce_ms: int = 150
    # Bluetooth pad remembered for automatic reconnection (None = none chosen).
    ble_address: str | None = None
    ble_name: str = ""

    # ------------------------------------------------------------ navigation

    @property
    def profile(self) -> Profile:
        self.active_profile = max(0, min(self.active_profile, len(self.profiles) - 1))
        return self.profiles[self.active_profile]

    @property
    def page(self) -> Page:
        p = self.profile
        self.active_page = max(0, min(self.active_page, len(p.pages) - 1))
        return p.pages[self.active_page]

    def set_profile(self, index: int) -> None:
        self.active_profile = max(0, min(index, len(self.profiles) - 1))
        self.active_page = 0

    def set_profile_by_name(self, name: str) -> bool:
        for i, p in enumerate(self.profiles):
            if p.name == name:
                self.set_profile(i)
                return True
        return False

    def set_page(self, index: int) -> None:
        self.active_page = max(0, min(index, len(self.profile.pages) - 1))

    def next_page(self) -> None:
        self.active_page = (self.active_page + 1) % len(self.profile.pages)

    def prev_page(self) -> None:
        self.active_page = (self.active_page - 1) % len(self.profile.pages)

    # ------------------------------------------------------------ persistance

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        profiles = []
        for pd in d.get("profiles", []):
            pages = []
            for pg in pd.get("pages", []):
                zones = {k: Zone(**z) for k, z in pg.get("zones", {}).items()}
                pages.append(Page(name=pg.get("name", "Page"), zones=zones))
            profiles.append(Profile(name=pd.get("name", "Profil"), pages=pages or [Page()]))
        cfg = cls(
            version=d.get("version", CONFIG_VERSION),
            profiles=profiles or [Profile()],
            active_profile=d.get("active_profile", 0),
            active_page=d.get("active_page", 0),
            feedback=d.get("feedback", True),
            feedback_amount=d.get("feedback_amount", 0.6),
            debounce_ms=d.get("debounce_ms", 150),
            ble_address=d.get("ble_address") or None,
            ble_name=d.get("ble_name", ""),
        )
        return cfg

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            return cls.default()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def default(cls) -> "Config":
        """Configuration de départ : quelques exemples façon Stream Deck."""
        page = Page(name="Page 1")
        page.zones[zone_key(0, 0)] = Zone("Chrome", "launch", {"target": "chrome"}, "#2b7cff")
        page.zones[zone_key(1, 0)] = Zone("Explorateur", "launch", {"target": "explorer"}, "#ffb000")
        page.zones[zone_key(2, 0)] = Zone("Bloc-notes", "launch", {"target": "notepad"}, "#00c8c8")
        page.zones[zone_key(0, 1)] = Zone("Vol −", "volume_down", {"steps": "2"}, "#00a000")
        page.zones[zone_key(1, 1)] = Zone("Muet", "mute", {}, "#a00000")
        page.zones[zone_key(2, 1)] = Zone("Vol +", "volume_up", {"steps": "2"}, "#00ff00")
        page.zones[zone_key(3, 1)] = Zone("Lecture", "play_pause", {}, "#ffffff")
        page.zones[zone_key(0, 2)] = Zone("Copier", "hotkey", {"keys": "ctrl+c"}, "#ff6a00")
        page.zones[zone_key(1, 2)] = Zone("Coller", "hotkey", {"keys": "ctrl+v"}, "#ff2ad4")
        page.zones[zone_key(2, 2)] = Zone(
            "Copier-coller", "macro", {"steps": "touche ctrl+c\nattendre 0.2\ntouche ctrl+v"}, "#b000ff"
        )
        page.zones[zone_key(3, 3)] = Zone("Page suiv.", "page_next", {}, "#404040")
        page2 = Page(name="Page 2")
        page2.zones[zone_key(3, 3)] = Zone("Page préc.", "page_prev", {}, "#404040")
        return cls(profiles=[Profile(name="Défaut", pages=[page, page2])])
