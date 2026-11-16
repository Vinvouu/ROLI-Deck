"""Where the application keeps its files (config, log): next to the project when
run from source, next to the executable when frozen by PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = base_dir() / "config.json"
LOG_PATH = base_dir() / "roli_deck.log"
