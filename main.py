"""Entry point: ROLI Deck, a command pad for the ROLI Lightpad BLOCK.

    python main.py             # start the application
    python main.py --selftest  # check that every runtime dependency is importable
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def selftest() -> int:
    """Import every runtime dependency (used to validate the PyInstaller build)."""
    import importlib.metadata as md

    lines = []
    ok = True
    for mod, dist in [("rtmidi", "python-rtmidi"), ("bleak", "bleak"), ("pystray", "pystray"),
                      ("PIL", "pillow"), ("winrt.windows.devices.bluetooth", "winrt-Windows.Devices.Bluetooth"),
                      ("winrt.windows.devices.enumeration", "winrt-Windows.Devices.Enumeration"),
                      ("winrt.windows.devices.radios", "winrt-Windows.Devices.Radios"),
                      ("bleak.backends.winrt.client", "bleak"), ("tkinter", "tkinter")]:
        try:
            __import__(mod)
            try:
                ver = md.version(dist)
            except Exception:  # noqa: BLE001
                ver = "?"
            lines.append(f"OK   {mod} ({ver})")
        except Exception as exc:  # noqa: BLE001
            ok = False
            lines.append(f"FAIL {mod}: {exc}")
    from app.paths import base_dir

    report = "\n".join(lines) + f"\nRESULT {'OK' if ok else 'FAIL'}\n"
    (base_dir() / "selftest.txt").write_text(report, encoding="utf-8")
    if sys.stdout is not None:
        print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    from app.logsetup import setup_logging
    from app.paths import LOG_PATH

    setup_logging(LOG_PATH)
    from app.gui import run

    run()
