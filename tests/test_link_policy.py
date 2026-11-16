"""Unit tests for the USB/Bluetooth arbitration policy and the config fields."""

from __future__ import annotations

from pathlib import Path

from app.model import Config
from app.pad import BACKOFF_STEPS, LinkState, backoff_delay, decide_action


def test_usb_has_priority_when_disconnected():
    assert decide_action(LinkState.DISCONNECTED, usb_present=True, ble_configured=True, ble_due=True) == "connect_usb"


def test_ble_used_when_no_usb_and_configured():
    assert decide_action(LinkState.DISCONNECTED, False, True, True) == "connect_ble"
    assert decide_action(LinkState.SEARCHING, False, True, True) == "connect_ble"


def test_ble_waits_for_backoff():
    assert decide_action(LinkState.DISCONNECTED, False, True, ble_due=False) == "wait_ble"


def test_idle_without_any_carrier():
    assert decide_action(LinkState.DISCONNECTED, False, False, True) == "idle"


def test_usb_unplug_drops_link():
    assert decide_action(LinkState.USB, usb_present=False, ble_configured=True, ble_due=True) == "drop_usb"
    assert decide_action(LinkState.USB, True, True, True) == "keep"


def test_usb_plugged_while_on_ble_switches():
    assert decide_action(LinkState.BLE, usb_present=True, ble_configured=True, ble_due=False) == "switch_to_usb"
    assert decide_action(LinkState.BLE, False, True, False) == "keep"


def test_backoff_is_progressive_and_capped():
    delays = [backoff_delay(i) for i in range(10)]
    assert delays[:6] == list(BACKOFF_STEPS)
    assert all(d == BACKOFF_STEPS[-1] for d in delays[6:])
    assert delays == sorted(delays)


def test_config_keeps_bluetooth_pad(tmp_path: Path):
    cfg = Config.default()
    cfg.ble_address = "48:B6:20:12:D7:E2"
    cfg.ble_name = "Lightpad Block I3CQ"
    p = tmp_path / "config.json"
    cfg.save(p)
    back = Config.load(p)
    assert back.ble_address == "48:B6:20:12:D7:E2"
    assert back.ble_name == "Lightpad Block I3CQ"
    back.ble_address = None
    back.save(p)
    assert Config.load(p).ble_address is None
