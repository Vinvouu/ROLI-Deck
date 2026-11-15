"""Simulation clavier Windows via SendInput (ctypes, sans dépendance).

- send_hotkey("ctrl+shift+t") : raccourci clavier
- send_text("Bonjour")        : saisie de texte Unicode
- media_key("volume_up")      : touches multimédia (volume, lecture…)
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "entree": 0x0D, "entrée": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "echap": 0x1B, "space": 0x20, "espace": 0x20, "backspace": 0x08,
    "retour": 0x08, "delete": 0x2E, "del": 0x2E, "suppr": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "debut": 0x24, "début": 0x24, "end": 0x23, "fin": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "gauche": 0x25, "up": 0x26, "haut": 0x26, "right": 0x27, "droite": 0x27,
    "down": 0x28, "bas": 0x28, "printscreen": 0x2C, "impr": 0x2C, "capslock": 0x14,
    "numlock": 0x90, "scrolllock": 0x91, "apps": 0x5D, "menu": 0x5D,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x70 + _i - 1

EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B, 0x5D, 0x90,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}

MEDIA_KEYS = {
    "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
    "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
}

if IS_WINDOWS:
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
    _user32.VkKeyScanW.restype = ctypes.c_short

    def _send(inputs: list) -> None:
        arr = (INPUT * len(inputs))(*inputs)
        sent = _user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise OSError(f"SendInput : {sent}/{len(inputs)} événements envoyés "
                          f"(erreur {ctypes.get_last_error()})")

    def _vk_event(vk: int, up: bool) -> "INPUT":
        flags = KEYEVENTF_KEYUP if up else 0
        if vk in EXTENDED:
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return inp

    def _unicode_event(ch: str, up: bool) -> "INPUT":
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
        return inp

else:  # pragma: no cover - autre OS : on journalise seulement

    def _send(inputs: list) -> None:
        raise OSError("La simulation clavier n'est disponible que sous Windows.")

    def _vk_event(vk: int, up: bool):
        return (vk, up)

    def _unicode_event(ch: str, up: bool):
        return (ch, up)


def _resolve_key(name: str) -> int:
    key = name.strip().lower()
    if not key:
        raise ValueError("touche vide")
    if key in VK:
        return VK[key]
    if len(key) == 1:
        if IS_WINDOWS:
            code = _user32.VkKeyScanW(key)
            if code != -1:
                return code & 0xFF
        return ord(key.upper())
    raise ValueError(f"touche inconnue : {name!r}")


def parse_hotkey(spec: str) -> list[int]:
    """'ctrl+shift+t' → [0x11, 0x10, 0x54]. Les séparateurs '+' et '-' sont acceptés."""
    parts = [p for p in spec.replace("-", "+").split("+") if p.strip()]
    if spec.strip() in ("+", "-"):
        parts = [spec.strip()]
    return [_resolve_key(p) for p in parts]


def send_hotkey(spec: str, hold_ms: int = 30) -> None:
    """Appuie puis relâche une combinaison, modificateurs d'abord."""
    vks = parse_hotkey(spec)
    _send([_vk_event(vk, up=False) for vk in vks])
    time.sleep(hold_ms / 1000)
    _send([_vk_event(vk, up=True) for vk in reversed(vks)])


def send_text(text: str, delay_ms: int = 5) -> None:
    """Saisit du texte tel quel (Unicode), indépendamment de la disposition clavier."""
    for ch in text:
        if ch == "\n":
            send_hotkey("enter")
            continue
        _send([_unicode_event(ch, False), _unicode_event(ch, True)])
        if delay_ms:
            time.sleep(delay_ms / 1000)


def media_key(name: str, times: int = 1) -> None:
    vk = MEDIA_KEYS[name]
    for _ in range(max(1, times)):
        _send([_vk_event(vk, False), _vk_event(vk, True)])
        time.sleep(0.02)
