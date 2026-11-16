"""Diagnostic 4 — automatic reconnection with progressive back-off.

    python -m tools.ble_diagnostics.diag04_reconnect [--cycles 3]

Connect, then switch the pad off and on again (or walk out of range): the
script logs the drop, retries with delays 1, 2, 4, 8, 15, 30 s, and logs the
time each reconnection took. Stops after --cycles successful reconnections
(0 = run until Ctrl+C).
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

BACKOFF = [1, 2, 4, 8, 15, 30]


async def main(args: argparse.Namespace) -> None:
    reconnections = 0
    attempt = 0
    while True:
        dev = await find_pad(args)
        if dev is None:
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            logging.info("[%s] pad not visible (attempt %d), retry in %d s", now(), attempt, delay)
            await asyncio.sleep(delay)
            continue
        conn = MidiConnection(dev, on_event=log_event)
        t0 = time.monotonic()
        try:
            await conn.connect()
        except Exception as exc:  # noqa: BLE001
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            logging.warning("[%s] connect failed (%s), retry in %d s", now(), exc, delay)
            await conn.close()
            await asyncio.sleep(delay)
            continue
        if attempt:
            reconnections += 1
            logging.info("[%s] RECONNECTED #%d after %d attempt(s), %.2f s", now(), reconnections,
                         attempt, time.monotonic() - t0)
            if args.cycles and reconnections >= args.cycles:
                await conn.close()
                logging.info("done: %d reconnection(s) verified", reconnections)
                return
        attempt = 0
        logging.info("connected — now switch the pad off/on to test reconnection (Ctrl+C to quit)")
        await conn.disconnected.wait()
        await conn.close()
        attempt = 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(ap)
    ap.add_argument("--cycles", type=int, default=0, help="stop after N successful reconnections")
    args = ap.parse_args()
    setup_logging("diag04_reconnect")
    run(lambda: main(args))
