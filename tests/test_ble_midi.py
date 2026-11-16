"""Unit tests for the BLE MIDI codec, with simulated packets (no hardware)."""

from __future__ import annotations

import pytest

from roli_blocks.ble_midi import BleMidiParser, MidiEvent, encode_packets, message_length


def hdr(ts: int) -> int:
    return 0x80 | ((ts >> 7) & 0x3F)


def tsb(ts: int) -> int:
    return 0x80 | (ts & 0x7F)


def test_single_note_on_with_timestamp():
    p = BleMidiParser()
    ts = 0x1234 & 0x1FFF
    events = p.feed(bytes([hdr(ts), tsb(ts), 0x90, 60, 100]))
    assert events == [MidiEvent(ts, bytes([0x90, 60, 100]))]
    assert events[0].describe().startswith("Note On")


def test_two_messages_in_one_packet_each_with_timestamp():
    p = BleMidiParser()
    pkt = bytes([hdr(5), tsb(5), 0x90, 60, 100, tsb(7), 0x80, 60, 0])
    events = p.feed(pkt)
    assert [e.data for e in events] == [bytes([0x90, 60, 100]), bytes([0x80, 60, 0])]
    assert [e.timestamp for e in events] == [5, 7]


def test_running_status_without_timestamp():
    p = BleMidiParser()
    pkt = bytes([hdr(1), tsb(1), 0x90, 60, 100, 62, 90, 64, 80])
    events = p.feed(pkt)
    assert [e.data for e in events] == [
        bytes([0x90, 60, 100]), bytes([0x90, 62, 90]), bytes([0x90, 64, 80])
    ]


def test_running_status_with_new_timestamp():
    p = BleMidiParser()
    pkt = bytes([hdr(1), tsb(1), 0x90, 60, 100, tsb(9), 62, 90])
    events = p.feed(pkt)
    assert events[1] == MidiEvent(9, bytes([0x90, 62, 90]))


def test_running_status_persists_across_packets():
    p = BleMidiParser()
    p.feed(bytes([hdr(1), tsb(1), 0xB0, 7, 100]))
    events = p.feed(bytes([hdr(2), tsb(2), 10, 64]))
    assert events == [MidiEvent(2, bytes([0xB0, 10, 64]))]


def test_sysex_in_single_packet():
    p = BleMidiParser()
    pkt = bytes([hdr(3), tsb(3), 0xF0, 0x00, 0x21, 0x10, 0x77, 0x01, tsb(4), 0xF7])
    events = p.feed(pkt)
    assert events == [MidiEvent(3, bytes([0xF0, 0x00, 0x21, 0x10, 0x77, 0x01, 0xF7]))]
    assert events[0].is_sysex


def test_sysex_split_across_three_packets_with_realtime_inside():
    p = BleMidiParser()
    first = bytes([hdr(3), tsb(3), 0xF0, 0x00, 0x21, 0x10])
    second = bytes([hdr(3), 0x77, 0x40, tsb(5), 0xF8, 0x02, 0x03])  # real-time interleaved
    third = bytes([hdr(3), 0x04, tsb(6), 0xF7])
    assert p.feed(first) == []
    ev2 = p.feed(second)
    assert ev2 == [MidiEvent(5, bytes([0xF8]))]
    ev3 = p.feed(third)
    assert ev3 == [MidiEvent(3, bytes([0xF0, 0x00, 0x21, 0x10, 0x77, 0x40, 0x02, 0x03, 0x04, 0xF7]))]


def test_timestamp_high_bits_come_from_header():
    p = BleMidiParser()
    ts = 0x1F80 | 0x05  # high 6 bits all set, low = 5
    events = p.feed(bytes([hdr(ts), tsb(ts), 0xC0, 12]))
    assert events[0].timestamp == ts
    assert events[0].data == bytes([0xC0, 12])


def test_invalid_header_raises():
    with pytest.raises(ValueError):
        BleMidiParser().feed(bytes([0x00, 0x80, 0x90, 1, 2]))


def test_stray_data_without_status_is_ignored():
    p = BleMidiParser()
    assert p.feed(bytes([hdr(0), tsb(0), 0x10, 0x20])) == []


def test_message_lengths():
    assert message_length(0x90) == 3
    assert message_length(0xC5) == 2
    assert message_length(0xE0) == 3
    assert message_length(0xF2) == 3
    assert message_length(0xF6) == 1


# ---------------------------------------------------------------------- encoder


def test_encode_channel_messages_round_trip():
    msgs = [bytes([0x90, 60, 100]), bytes([0x80, 60, 0]), bytes([0xB0, 1, 2])]
    packets = encode_packets(msgs, max_len=20, timestamp=42)
    assert len(packets) == 1
    p = BleMidiParser()
    decoded = [e.data for pkt in packets for e in p.feed(pkt)]
    assert decoded == msgs


def test_encode_long_sysex_fragments_and_round_trips():
    body = bytes(range(0, 120)) + bytes(range(0, 120))
    sysex = bytes([0xF0]) + bytes(b & 0x7F for b in body) + bytes([0xF7])
    packets = encode_packets([sysex], max_len=20, timestamp=100)
    assert len(packets) > 10
    assert all(len(pkt) <= 20 for pkt in packets)
    assert all(pkt[0] & 0xC0 == 0x80 for pkt in packets)
    # continuation packets carry data right after the header (no timestamp byte)
    assert packets[1][1] & 0x80 == 0
    # the final packet ends with timestamp + F7
    assert packets[-1][-1] == 0xF7 and packets[-1][-2] & 0x80
    p = BleMidiParser()
    decoded = [e.data for pkt in packets for e in p.feed(pkt)]
    assert decoded == [sysex]


def test_encode_sysex_with_large_mtu_is_single_packet():
    sysex = bytes([0xF0, 0x00, 0x21, 0x10, 0x77, 0x00, 0x01, 0x03, 0x00, 0x05, 0xF7])
    packets = encode_packets([sysex], max_len=512, timestamp=1)
    assert len(packets) == 1
    assert BleMidiParser().feed(packets[0])[0].data == sysex


def test_encode_rejects_tiny_mtu():
    with pytest.raises(ValueError):
        encode_packets([bytes([0x90, 1, 2])], max_len=3)
