"""System-tray icon (pystray) reflecting the link state.

The icon runs on its own thread; menu callbacks are forwarded to the GUI, which
must marshal them onto the Tk thread (`root.after`).
"""

from __future__ import annotations

import logging
from typing import Callable

from PIL import Image, ImageDraw

from .pad import LinkState

log = logging.getLogger(__name__)

STATE_COLORS = {
    LinkState.DISCONNECTED: "#7a7a7a",
    LinkState.SEARCHING: "#e6b400",
    LinkState.USB: "#2ecc40",
    LinkState.BLE: "#2b7cff",
}
STATE_LETTER = {
    LinkState.DISCONNECTED: "",
    LinkState.SEARCHING: "…",
    LinkState.USB: "U",
    LinkState.BLE: "B",
}


def make_icon_image(state: LinkState, size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = STATE_COLORS[state]
    d.ellipse((4, 4, size - 4, size - 4), fill=color, outline="#202020", width=3)
    # 4x4 mini grid in the middle, like the pad
    cell = size // 9
    x0 = (size - 4 * cell - 3) // 2
    for r in range(4):
        for c in range(4):
            x = x0 + c * (cell + 1)
            y = x0 + r * (cell + 1)
            d.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#ffffff" if state != LinkState.DISCONNECTED else "#c0c0c0")
    return img


class TrayIcon:
    def __init__(self, on_show: Callable[[], None], on_bluetooth: Callable[[], None],
                 on_quit: Callable[[], None]) -> None:
        import pystray

        self._pystray = pystray
        self._icon = pystray.Icon(
            "roli_deck",
            make_icon_image(LinkState.DISCONNECTED),
            "ROLI Deck — déconnecté",
            menu=pystray.Menu(
                pystray.MenuItem("Afficher la fenêtre", lambda: on_show(), default=True),
                pystray.MenuItem("Connecter en Bluetooth…", lambda: on_bluetooth()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quitter", lambda: on_quit()),
            ),
        )
        self._state: LinkState | None = None
        self._title = ""
        self._icon.run_detached()

    def update(self, state: LinkState, text: str) -> None:
        title = ("ROLI Deck — " + text)[:120]
        try:
            if state != self._state:
                self._state = state
                self._icon.icon = make_icon_image(state)
            if title != self._title:
                self._title = title
                self._icon.title = title
        except Exception as exc:  # noqa: BLE001
            log.debug("tray update failed: %s", exc)

    def notify(self, text: str) -> None:
        try:
            self._icon.notify(text, "ROLI Deck")
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001
            pass
