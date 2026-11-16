"""Diagnostic 5 — is the pad paired (bonded) in Windows? Optionally remove it.

    python -m tools.ble_diagnostics.diag05_pairing                      # report only
    python -m tools.ble_diagnostics.diag05_pairing --unpair             # asks for confirmation
    python -m tools.ble_diagnostics.diag05_pairing --address 48:B6:20:12:D7:E2

Uses the WinRT API (winrt-* packages installed with bleak). Nothing is changed
unless --unpair is given AND you confirm by typing "oui".
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.ble_diagnostics.common import (  # noqa: E402
    add_device_args, find_pad, paired_ble_devices, pairing_state, run, setup_logging, unpair,
)


async def main(args: argparse.Namespace) -> None:
    paired = await paired_ble_devices()
    logging.info("%d BLE device(s) paired in Windows:", len(paired))
    for name, dev_id, is_paired in paired:
        tag = "  <== Lightpad" if "lightpad" in (name or "").lower() else ""
        logging.info("  %-28s paired=%s  %s%s", name or "-", is_paired, dev_id, tag)

    address = args.address
    if not address:
        dev = await find_pad(args)
        if dev is None:
            logging.warning("pad not visible in a BLE scan; pass --address to inspect it anyway")
            return
        address = dev.address
    known, is_paired, level = await pairing_state(address)
    logging.info("pad %s: known_to_windows=%s paired=%s protection=%s", address, known, is_paired, level)

    if not args.unpair:
        return
    if not is_paired:
        logging.info("nothing to remove: the pad is not paired")
        return
    answer = input(f"Retirer l'appairage Windows du pad {address} ? (oui/non) ").strip().lower()
    if answer != "oui":
        logging.info("cancelled, pairing kept")
        return
    status = await unpair(address)
    logging.info("unpair result: %s", status)
    known, is_paired, level = await pairing_state(address)
    logging.info("after: known_to_windows=%s paired=%s protection=%s", known, is_paired, level)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(ap)
    ap.add_argument("--unpair", action="store_true", help="offer to remove the Windows pairing")
    args = ap.parse_args()
    setup_logging("diag05_pairing")
    run(lambda: main(args))
