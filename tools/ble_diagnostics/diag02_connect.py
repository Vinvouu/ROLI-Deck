"""Diagnostic 2 — direct connection without Windows pairing, decoded MIDI input.

    python -m tools.ble_diagnostics.diag02_connect [--address 48:B6:20:12:D7:E2] [--duration 60]

Touch the pad: every decoded message (note on/off, aftertouch, pitch bend, SysEx)
is printed with its BLE timestamp. Connection and disconnection are timestamped.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.ble_diagnostics.common import (  # noqa: E402
    MidiConnection, add_device_args, find_pad, log_event, now, run, setup_logging,
)


async def main(args: argparse.Namespace) -> None:
    dev = await find_pad(args)
    if dev is None:
        logging.error("pad not found")
        return
    logging.info("found %r at %s", dev.name, dev.address)
    conn = MidiConnection(dev, on_event=log_event)
    try:
        await conn.connect()
        logging.info("listening for %.0f s — touch the pad now (Ctrl+C to stop)", args.duration)
        try:
            await asyncio.wait_for(conn.disconnected.wait(), timeout=args.duration)
            logging.warning("link dropped before the end of the test")
        except asyncio.TimeoutError:
            logging.info("[%s] still connected after %.0f s", now(), args.duration)
    finally:
        logging.info("received %d packets / %d bytes / %d MIDI messages",
                     conn.packets, conn.bytes, len(conn.parser.events))
        await conn.close()
        logging.info("[%s] closed", now())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(ap)
    ap.add_argument("--duration", type=float, default=60.0, help="seconds to stay connected")
    args = ap.parse_args()
    setup_logging("diag02_connect")
    run(lambda: main(args))
