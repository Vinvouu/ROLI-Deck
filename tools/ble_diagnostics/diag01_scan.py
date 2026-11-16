"""Diagnostic 1 — BLE scan: list visible devices and spot the Lightpad.

    python -m tools.ble_diagnostics.diag01_scan [--duration 8]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bleak import BleakScanner  # noqa: E402

from roli_blocks.ble_midi import MIDI_SERVICE_UUID  # noqa: E402
from tools.ble_diagnostics.common import run, setup_logging  # noqa: E402


async def main(duration: float) -> None:
    logging.info("scanning for %.0f s ...", duration)
    found = await BleakScanner.discover(timeout=duration, return_adv=True)
    rows = sorted(found.values(), key=lambda t: -(t[1].rssi or -200))
    logging.info("%d device(s) seen", len(rows))
    pads = []
    for dev, adv in rows:
        name = dev.name or adv.local_name or "-"
        svcs = [u.lower() for u in adv.service_uuids]
        midi = MIDI_SERVICE_UUID in svcs
        tag = "  <== BLE MIDI" if midi else ""
        logging.info("%s  rssi=%4s dBm  name=%-24s services=%s%s", dev.address, adv.rssi, name,
                     [u[:8] for u in svcs] or "-", tag)
        if midi or "lightpad" in name.lower():
            pads.append((dev, adv))
    if not pads:
        logging.warning("no Lightpad / BLE MIDI device seen. Is the pad on, unplugged from USB, "
                        "and not already connected to another device?")
        return
    for dev, adv in pads:
        logging.info("LIGHTPAD: name=%r address=%s rssi=%s dBm manufacturer_data=%s",
                     dev.name or adv.local_name, dev.address, adv.rssi,
                     {k: v.hex() for k, v in adv.manufacturer_data.items()} or "-")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=float, default=8.0, help="scan duration in seconds")
    args = ap.parse_args()
    setup_logging("diag01_scan")
    run(lambda: main(args.duration))
