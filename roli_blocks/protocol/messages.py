"""Typed device→host message decoding and host→device builders.

The decoder walks a single SysEx payload, peeling messages off the head until
MessageType=0 is reached or the stream is exhausted, and emits typed events.

Encoders return `bytes` ready to hand to `sysex.encode(deviceIndex, payload, from_host=True)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .bitstream import BitReader, BitWriter
from .opcodes import (
    BATTERY_CHARGING_BITS,
    BATTERY_LEVEL_BITS,
    BYTE_COUNT_FEW_BITS,
    BYTE_COUNT_MANY_BITS,
    BYTE_SEQUENCE_CONTINUES_BITS,
    BYTE_VALUE_BITS,
    CONNECTION_COUNT_BITS,
    CONNECTOR_PORT_BITS,
    CONTROL_BUTTON_ID_BITS,
    DATA_CHANGE_COMMAND_BITS,
    DEVICE_COMMAND_BITS,
    DEVICE_COUNT_BITS,
    MESSAGE_TYPE_BITS,
    PACKET_COUNTER_BITS,
    PACKET_INDEX_BITS,
    PACKET_TIMESTAMP_BITS,
    PACKET_TIMESTAMP_OFFSET_BITS,
    PROTOCOL_VERSION_BITS,
    SERIAL_LENGTH,
    TOPOLOGY_INDEX_BITS,
    TOUCH_INDEX_BITS,
    TOUCH_VEL_BITS,
    TOUCH_X_BITS,
    TOUCH_Y_BITS,
    TOUCH_Z_BITS,
    DataChangeCommand,
    DeviceCommand,
    MessageFromDevice,
    MessageFromHost,
)


# =============================================================================
# Device → host event dataclasses
# =============================================================================


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    serial: str
    topology_index: int
    battery_level: int  # 0..31
    battery_charging: bool


@dataclass(frozen=True, slots=True)
class Connection:
    device1: int
    port1: int
    device2: int
    port2: int


@dataclass(frozen=True, slots=True)
class TopologyMessage:
    is_extension: bool  # True if this is deviceTopologyExtend (0x04)
    protocol_version: int
    devices: tuple[DeviceInfo, ...]
    connections: tuple[Connection, ...]
    is_final: bool  # True when this packet completes the topology


@dataclass(frozen=True, slots=True)
class TopologyEnd:
    protocol_version: int


@dataclass(frozen=True, slots=True)
class DeviceVersionMessage:
    topology_index: int
    version: str


@dataclass(frozen=True, slots=True)
class DeviceNameMessage:
    topology_index: int
    name: str


@dataclass(frozen=True, slots=True)
class Touch:
    """Position+velocity reading. Velocities are 0 for non-velocity touch messages."""

    timestamp_ms: int  # absolute (packet_ts + offset)
    touch_index: int
    x: int  # 0..4095
    y: int  # 0..4095
    z: int  # 0..255 (pressure)
    vx: int = 0
    vy: int = 0
    vz: int = 0
    is_start: bool = False
    is_end: bool = False

    @property
    def x_normalized(self) -> float:
        return self.x / 4095.0

    @property
    def y_normalized(self) -> float:
        return self.y / 4095.0

    @property
    def pressure(self) -> float:
        return self.z / 255.0


@dataclass(frozen=True, slots=True)
class ControlButton:
    timestamp_ms: int
    button_id: int
    is_down: bool


@dataclass(frozen=True, slots=True)
class PacketACK:
    packet_counter: int


@dataclass(frozen=True, slots=True)
class LogMessage:
    text: str


@dataclass(frozen=True, slots=True)
class UnknownMessage:
    message_type: int
    payload_remaining_bits: int


@dataclass(frozen=True, slots=True)
class DecodedPacket:
    """One device→host SysEx packet, fully parsed."""

    device_index: int  # source topology index from the SysEx envelope byte
    packet_timestamp: int  # ms since device boot (32 bits)
    messages: tuple[object, ...] = field(default_factory=tuple)


# =============================================================================
# Decoder
# =============================================================================


def _read_string(r: BitReader, length: int) -> str:
    return "".join(chr(r.read(7)) for _ in range(length)).rstrip("\x00")


def _decode_topology(r: BitReader, *, is_extension: bool) -> TopologyMessage:
    proto = r.read(PROTOCOL_VERSION_BITS)
    n_devices = r.read(DEVICE_COUNT_BITS)
    n_connections = r.read(CONNECTION_COUNT_BITS)
    devices: list[DeviceInfo] = []
    for _ in range(n_devices):
        serial = "".join(chr(r.read(7)) for _ in range(SERIAL_LENGTH)).rstrip("\x00")
        topo_idx = r.read(TOPOLOGY_INDEX_BITS)
        battery = r.read(BATTERY_LEVEL_BITS)
        charging = bool(r.read(BATTERY_CHARGING_BITS))
        devices.append(
            DeviceInfo(
                serial=serial,
                topology_index=topo_idx,
                battery_level=battery,
                battery_charging=charging,
            )
        )
    connections: list[Connection] = []
    for _ in range(n_connections):
        d1 = r.read(TOPOLOGY_INDEX_BITS)
        p1 = r.read(CONNECTOR_PORT_BITS)
        d2 = r.read(TOPOLOGY_INDEX_BITS)
        p2 = r.read(CONNECTOR_PORT_BITS)
        connections.append(Connection(d1, p1, d2, p2))

    # Per HostPacketDecoder: a topology fits in one packet iff
    # numDevices < max && numConnections < max.
    is_final = n_devices < 6 and n_connections < 24
    return TopologyMessage(
        is_extension=is_extension,
        protocol_version=proto,
        devices=tuple(devices),
        connections=tuple(connections),
        is_final=is_final,
    )


def _decode_touch(
    r: BitReader, packet_timestamp: int, *, is_start: bool, is_end: bool, with_velocity: bool
) -> Touch:
    offset = r.read(PACKET_TIMESTAMP_OFFSET_BITS)
    touch_index = r.read(TOUCH_INDEX_BITS)
    x = r.read(TOUCH_X_BITS)
    y = r.read(TOUCH_Y_BITS)
    z = r.read(TOUCH_Z_BITS)
    vx = vy = vz = 0
    if with_velocity:
        vx = r.read(TOUCH_VEL_BITS)
        vy = r.read(TOUCH_VEL_BITS)
        vz = r.read(TOUCH_VEL_BITS)
    return Touch(
        timestamp_ms=(packet_timestamp + offset) & 0xFFFFFFFF,
        touch_index=touch_index,
        x=x,
        y=y,
        z=z,
        vx=vx,
        vy=vy,
        vz=vz,
        is_start=is_start,
        is_end=is_end,
    )


def _decode_button(r: BitReader, packet_timestamp: int, *, is_down: bool) -> ControlButton:
    offset = r.read(PACKET_TIMESTAMP_OFFSET_BITS)
    button_id = r.read(CONTROL_BUTTON_ID_BITS)
    return ControlButton(
        timestamp_ms=(packet_timestamp + offset) & 0xFFFFFFFF,
        button_id=button_id,
        is_down=is_down,
    )


def _decode_log_message(r: BitReader) -> LogMessage:
    chars: list[str] = []
    while r.remaining_bits() >= 7:
        chars.append(chr(r.read(7)))
    return LogMessage("".join(chars).rstrip("\x00"))


def decode_packet(device_index: int, payload: bytes | bytearray) -> DecodedPacket:
    """Walk a device→host payload and emit typed messages."""
    r = BitReader(payload)
    if r.remaining_bits() < PACKET_TIMESTAMP_BITS:
        raise ValueError("payload too short for packet timestamp")
    packet_timestamp = r.read(PACKET_TIMESTAMP_BITS)

    out: list[object] = []
    while r.remaining_bits() >= MESSAGE_TYPE_BITS:
        mt = r.read(MESSAGE_TYPE_BITS)
        if mt == 0:
            break
        try:
            kind = MessageFromDevice(mt)
        except ValueError:
            out.append(UnknownMessage(message_type=mt, payload_remaining_bits=r.remaining_bits()))
            break
        msg = _dispatch(kind, r, packet_timestamp)
        if msg is None:
            break
        out.append(msg)
    return DecodedPacket(
        device_index=device_index,
        packet_timestamp=packet_timestamp,
        messages=tuple(out),
    )


def _dispatch(kind: MessageFromDevice, r: BitReader, packet_timestamp: int) -> object | None:
    if kind == MessageFromDevice.deviceTopology:
        return _decode_topology(r, is_extension=False)
    if kind == MessageFromDevice.deviceTopologyExtend:
        return _decode_topology(r, is_extension=True)
    if kind == MessageFromDevice.deviceTopologyEnd:
        return TopologyEnd(protocol_version=r.read(PROTOCOL_VERSION_BITS))
    if kind == MessageFromDevice.deviceVersion:
        idx = r.read(TOPOLOGY_INDEX_BITS)
        n = r.read(7)
        return DeviceVersionMessage(topology_index=idx, version=_read_string(r, n))
    if kind == MessageFromDevice.deviceName:
        idx = r.read(TOPOLOGY_INDEX_BITS)
        n = r.read(7)
        return DeviceNameMessage(topology_index=idx, name=_read_string(r, n))
    if kind == MessageFromDevice.touchStart:
        return _decode_touch(r, packet_timestamp, is_start=True, is_end=False, with_velocity=False)
    if kind == MessageFromDevice.touchMove:
        return _decode_touch(r, packet_timestamp, is_start=False, is_end=False, with_velocity=False)
    if kind == MessageFromDevice.touchEnd:
        return _decode_touch(r, packet_timestamp, is_start=False, is_end=True, with_velocity=False)
    if kind == MessageFromDevice.touchStartWithVelocity:
        return _decode_touch(r, packet_timestamp, is_start=True, is_end=False, with_velocity=True)
    if kind == MessageFromDevice.touchMoveWithVelocity:
        return _decode_touch(r, packet_timestamp, is_start=False, is_end=False, with_velocity=True)
    if kind == MessageFromDevice.touchEndWithVelocity:
        return _decode_touch(r, packet_timestamp, is_start=False, is_end=True, with_velocity=True)
    if kind == MessageFromDevice.controlButtonDown:
        return _decode_button(r, packet_timestamp, is_down=True)
    if kind == MessageFromDevice.controlButtonUp:
        return _decode_button(r, packet_timestamp, is_down=False)
    if kind == MessageFromDevice.packetACK:
        return PacketACK(packet_counter=r.read(PACKET_COUNTER_BITS))
    if kind == MessageFromDevice.logMessage:
        return _decode_log_message(r)
    # configMessage / programEventMessage / firmwareUpdateACK consume known sizes;
    # for now, treat as unknown so the decoder stops cleanly.
    return UnknownMessage(message_type=int(kind), payload_remaining_bits=r.remaining_bits())


# =============================================================================
# Host → device payload builders
# =============================================================================


def build_device_command(command: DeviceCommand) -> bytes:
    w = BitWriter()
    w.write(MessageFromHost.deviceCommandMessage, MESSAGE_TYPE_BITS)
    w.write(int(command), DEVICE_COMMAND_BITS)
    w.flush_partial_byte()
    return w.bytes


def build_request_topology() -> bytes:
    return build_device_command(DeviceCommand.requestTopologyMessage)


def build_begin_api_mode() -> bytes:
    return build_device_command(DeviceCommand.beginAPIMode)


def build_end_api_mode() -> bytes:
    return build_device_command(DeviceCommand.endAPIMode)


def build_ping() -> bytes:
    return build_device_command(DeviceCommand.ping)


# =============================================================================
# Shared-data-change builder (for LED grid uploads — see M6)
# =============================================================================


class SharedDataChangeBuilder:
    """Encode a sharedDataChange (0x02) message into a payload byte string."""

    def __init__(self, packet_index: int) -> None:
        if not 0 <= packet_index < (1 << PACKET_INDEX_BITS):
            raise ValueError("packet_index out of range")
        self._w = BitWriter()
        self._w.write(MessageFromHost.sharedDataChange, MESSAGE_TYPE_BITS)
        self._w.write(packet_index, PACKET_INDEX_BITS)

    def skip(self, count: int) -> "SharedDataChangeBuilder":
        if count <= 0:
            return self
        max_few = (1 << BYTE_COUNT_FEW_BITS) - 1  # 15
        max_many = (1 << BYTE_COUNT_MANY_BITS) - 1  # 255
        while count > max_many:
            self._w.write(DataChangeCommand.skipBytesMany, DATA_CHANGE_COMMAND_BITS)
            self._w.write(max_many, BYTE_COUNT_MANY_BITS)
            count -= max_many
        if count > max_few:
            self._w.write(DataChangeCommand.skipBytesMany, DATA_CHANGE_COMMAND_BITS)
            self._w.write(count, BYTE_COUNT_MANY_BITS)
        else:
            self._w.write(DataChangeCommand.skipBytesFew, DATA_CHANGE_COMMAND_BITS)
            self._w.write(count, BYTE_COUNT_FEW_BITS)
        return self

    def write_bytes(self, values: bytes | bytearray) -> "SharedDataChangeBuilder":
        if not values:
            return self
        self._w.write(DataChangeCommand.setSequenceOfBytes, DATA_CHANGE_COMMAND_BITS)
        last = len(values) - 1
        for i, v in enumerate(values):
            self._w.write(v, BYTE_VALUE_BITS)
            self._w.write(0 if i == last else 1, BYTE_SEQUENCE_CONTINUES_BITS)
        return self

    def fill(self, value: int, count: int) -> "SharedDataChangeBuilder":
        if count <= 0:
            return self
        max_few = (1 << BYTE_COUNT_FEW_BITS) - 1
        max_many = (1 << BYTE_COUNT_MANY_BITS) - 1
        while count > max_many:
            self._w.write(DataChangeCommand.setManyBytesWithValue, DATA_CHANGE_COMMAND_BITS)
            self._w.write(max_many, BYTE_COUNT_MANY_BITS)
            self._w.write(value, BYTE_VALUE_BITS)
            count -= max_many
        if count > max_few:
            self._w.write(DataChangeCommand.setManyBytesWithValue, DATA_CHANGE_COMMAND_BITS)
            self._w.write(count, BYTE_COUNT_MANY_BITS)
            self._w.write(value, BYTE_VALUE_BITS)
        else:
            self._w.write(DataChangeCommand.setFewBytesWithValue, DATA_CHANGE_COMMAND_BITS)
            self._w.write(count, BYTE_COUNT_FEW_BITS)
            self._w.write(value, BYTE_VALUE_BITS)
        return self

    def end(self, *, last_change: bool = True) -> bytes:
        self._w.write(
            DataChangeCommand.endOfChanges if last_change else DataChangeCommand.endOfPacket,
            DATA_CHANGE_COMMAND_BITS,
        )
        self._w.flush_partial_byte()
        return self._w.bytes


# =============================================================================
# Convenience: iterate messages from a packet by type
# =============================================================================


def iter_messages_of(packet: DecodedPacket, kind: type) -> Iterator:
    return (m for m in packet.messages if isinstance(m, kind))
