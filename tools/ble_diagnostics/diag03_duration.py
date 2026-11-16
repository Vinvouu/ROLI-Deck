"""Diagnostic 3 — stay connected for several minutes without touching the pad.

    python -m tools.ble_diagnostics.diag03_duration --minutes 5
    python -m tools.ble_diagnostics.diag03_duration --minutes 5 --keepalive 20

Without --keepalive the link is left idle. With --keepalive N, the MIDI
characteristic is read every N seconds (the spec says a read returns an empty
payload), which keeps GATT traffic flowing. Compare both runs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
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
    conn = MidiConnection(dev, on_event=log_event)
    await conn.connect()
    total = args.minutes * 60
    t0 = time.monotonic()
    last_report = t0
    last_keepalive = t0
    logging.info("idle test for %.1f min, keep-alive=%s", args.minutes,
                 f"read every {args.keepalive:.0f} s" if args.keepalive else "none")
    try:
        while time.monotonic() - t0 < total:
            try:
                await asyncio.wait_for(conn.disconnected.wait(), timeout=1.0)
                logging.error("[%s] link dropped after %.0f s idle", now(), time.monotonic() - t0)
                return
            except asyncio.TimeoutError:
                pass
            t = time.monotonic()
            if args.keepalive and t - last_keepalive >= args.keepalive:
                last_keepalive = t
                try:
                    n = await conn.keepalive_read()
                    logging.info("[%s] keep-alive read ok (%d bytes)", now(), n)
                except Exception as exc:  # noqa: BLE001
                    logging.warning("[%s] keep-alive read failed: %s", now(), exc)
            if t - last_report >= 30:
                last_report = t
                logging.info("[%s] still connected, %.0f s elapsed, %d packets received",
                             now(), t - t0, conn.packets)
        logging.info("[%s] SUCCESS: still connected after %.1f min", now(), args.minutes)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(ap)
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--keepalive", type=float, default=0.0, help="seconds between keep-alive reads (0 = off)")
    args = ap.parse_args()
    setup_logging("diag03_duration")
    run(lambda: main(args))
