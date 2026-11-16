"""Diagnostic 6 — BLOCKS protocol (LEDs) over BLE MIDI: SysEx fragmentation test.

    python -m tools.ble_diagnostics.diag06_leds [--duration 10] [--delay 0]

Runs the same sequence as the USB prototype, but over Bluetooth: topology
request, API mode + pings, LittleFoot bitmap program upload, a red 4x4 zone,
then (with --feedback) zones light up while touched. Reports packet counts,
device ACKs and whether the topology answer came back over BLE.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roli_blocks import Block, BlockGroup, Color, Touch  # noqa: E402

from app.model import lighten, hex_to_rgb, zone_at, zone_rect  # noqa: E402
from tools.ble_diagnostics.ble_blocks_transport import BleTransport  # noqa: E402
from tools.ble_diagnostics.common import add_device_args, find_pad, now, run, setup_logging  # noqa: E402


def paint(grid, rect, rgb) -> None:
    x0, y0, w, h = rect
    c = Color(*rgb)
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            grid.set_pixel(x, y, c)


async def locate(args: argparse.Namespace):
    return await find_pad(args)


def main(args: argparse.Namespace) -> None:
    import asyncio

    dev = asyncio.run(locate(args))
    if dev is None:
        logging.error("pad not found")
        return
    transport = BleTransport(dev, inter_packet_delay=args.delay)
    t0 = time.monotonic()
    transport.open()
    logging.info("[%s] BLE link up in %.2f s", now(), time.monotonic() - t0)

    group = BlockGroup(transport=transport)
    try:
        t1 = time.monotonic()
        blocks = None
        for attempt in range(3):
            try:
                blocks = group.fetch_topology(timeout=3.0)
                break
            except TimeoutError:
                logging.warning("no topology answer (attempt %d)", attempt + 1)
        if not blocks:
            logging.error("the pad does not answer the BLOCKS protocol over BLE (stats=%s)", transport.stats)
            return
        lp: Block | None = next((b for b in blocks if b.is_lightpad), None)
        logging.info("[%s] topology in %.2f s: %s firmware=%s", now(), time.monotonic() - t1, blocks,
                     lp.firmware_version if lp else "?")
        if lp is None:
            return

        pressed: dict[int, tuple[int, int]] = {}

        def on_touch(blk: Block, t: Touch) -> None:
            z = zone_at(t.x_normalized, t.y_normalized)
            kind = "start" if t.is_start else ("end" if t.is_end else "move")
            if kind != "move":
                logging.info("[%s] touch %d %s zone=%s p=%.2f", now(), t.touch_index, kind, z, t.pressure)
            if not args.feedback:
                return
            changed = False
            if t.is_start and z is not None:
                pressed[t.touch_index] = z
                changed = True
            elif t.is_end and pressed.pop(t.touch_index, None) is not None:
                changed = True
            if changed:
                render(blk)

        def render(blk: Block) -> None:
            grid = blk.grid
            grid.fill(Color(0, 0, 0))
            paint(grid, zone_rect(0, 0), hex_to_rgb(args.color))
            for z in set(pressed.values()):
                paint(grid, zone_rect(*z), hex_to_rgb(lighten("#000000" if z != (0, 0) else args.color)))
            ts = time.monotonic()
            group.flush_grid(blk, full=True)
            logging.info("[%s] full frame sent in %.0f ms (%d packets so far)", now(),
                         (time.monotonic() - ts) * 1000, transport.stats["tx_packets"])

        group.on_touch(on_touch)
        group.begin_api_mode()
        t2 = time.monotonic()
        group.load_bitmap_program(lp)
        logging.info("[%s] program uploaded in %.0f ms", now(), (time.monotonic() - t2) * 1000)
        time.sleep(0.5)
        acks_before = transport.stats["rx_acks"]
        render(lp)
        time.sleep(1.0)
        logging.info("ACKs received for the frame: %d (stats=%s)",
                     transport.stats["rx_acks"] - acks_before, transport.stats)
        logging.info("zone (1,1) should be lit in %s for %.0f s%s", args.color, args.duration,
                     " — touch zones to test feedback" if args.feedback else "")
        end = time.monotonic() + args.duration
        while time.monotonic() < end and not transport.disconnected.is_set():
            time.sleep(0.2)
        if transport.disconnected.is_set():
            logging.error("link dropped during the test")
        group.fill(lp, Color(0, 0, 0))
        group.flush_grid(lp, full=True)
        time.sleep(0.3)
    finally:
        logging.info("final stats: %s", transport.stats)
        group.close()
        logging.info("[%s] closed", now())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(ap)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--color", default="#ff0000")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between BLE packets (flow control test)")
    ap.add_argument("--feedback", action="store_true", help="light touched zones")
    args = ap.parse_args()
    setup_logging("diag06_leds")
    try:
        main(args)
    except KeyboardInterrupt:
        logging.info("stopped by user")
