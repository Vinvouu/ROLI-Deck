"""Enums and bit-width constants from `roli_BlocksProtocolDefinitions.h`."""

from __future__ import annotations

from enum import IntEnum
from typing import Final


CURRENT_PROTOCOL_VERSION: Final = 1


class MessageFromDevice(IntEnum):
    deviceTopology = 0x01
    packetACK = 0x02
    firmwareUpdateACK = 0x03
    deviceTopologyExtend = 0x04
    deviceTopologyEnd = 0x05
    deviceVersion = 0x06
    deviceName = 0x07
    touchStart = 0x10
    touchMove = 0x11
    touchEnd = 0x12
    touchStartWithVelocity = 0x13
    touchMoveWithVelocity = 0x14
    touchEndWithVelocity = 0x15
    configMessage = 0x18
    controlButtonDown = 0x20
    controlButtonUp = 0x21
    programEventMessage = 0x28
    logMessage = 0x30


class MessageFromHost(IntEnum):
    deviceCommandMessage = 0x01
    sharedDataChange = 0x02
    programEventMessage = 0x03
    firmwareUpdatePacket = 0x04
    configMessage = 0x10
    factoryReset = 0x11
    blockReset = 0x12
    setName = 0x20


class DeviceCommand(IntEnum):
    beginAPIMode = 0x00
    requestTopologyMessage = 0x01
    endAPIMode = 0x02
    ping = 0x03
    debugMode = 0x04
    saveProgramAsDefault = 0x05


class DataChangeCommand(IntEnum):
    """3-bit subcommands inside a sharedDataChange message."""

    endOfPacket = 0
    endOfChanges = 1
    skipBytesFew = 2
    skipBytesMany = 3
    setSequenceOfBytes = 4
    setFewBytesWithValue = 5
    setFewBytesWithLastValue = 6
    setManyBytesWithValue = 7


# bit widths
MESSAGE_TYPE_BITS: Final = 7
PROTOCOL_VERSION_BITS: Final = 8
PACKET_TIMESTAMP_BITS: Final = 32
PACKET_TIMESTAMP_OFFSET_BITS: Final = 5
DEVICE_COUNT_BITS: Final = 7
CONNECTION_COUNT_BITS: Final = 8
TOPOLOGY_INDEX_BITS: Final = 7
CONNECTOR_PORT_BITS: Final = 5
BATTERY_LEVEL_BITS: Final = 5
BATTERY_CHARGING_BITS: Final = 1
TOUCH_INDEX_BITS: Final = 5
TOUCH_X_BITS: Final = 12
TOUCH_Y_BITS: Final = 12
TOUCH_Z_BITS: Final = 8
TOUCH_VEL_BITS: Final = 8
DEVICE_COMMAND_BITS: Final = 9
PACKET_INDEX_BITS: Final = 16
PACKET_COUNTER_BITS: Final = 10
DATA_CHANGE_COMMAND_BITS: Final = 3
BYTE_COUNT_FEW_BITS: Final = 4
BYTE_COUNT_MANY_BITS: Final = 8
BYTE_VALUE_BITS: Final = 8
BYTE_SEQUENCE_CONTINUES_BITS: Final = 1
CONTROL_BUTTON_ID_BITS: Final = 12
SERIAL_LENGTH: Final = 16

MAX_BLOCKS_IN_TOPOLOGY_PACKET: Final = 6
MAX_CONNECTIONS_IN_TOPOLOGY_PACKET: Final = 24

# ping intervals
MASTER_PING_INTERVAL_MS: Final = 400
DNA_PING_INTERVAL_MS: Final = 1666

# raw special-case host->device message
RESET_MASTER_SYSEX: Final = bytes([0xF0, 0x00, 0x21, 0x10, 0x49, 0xF7])


# Serial-prefix to model classification
SERIAL_PREFIXES: Final = {
    "LPB": "lightpad",
    "LPM": "lightpad",
    "LIC": "live",
    "LOC": "loop",
    "DCB": "devCtrl",
    "TCB": "touch",
    "SBB": "seaboard",
    "LKB": "lumiKeys",
}


def block_type_for_serial(serial: str) -> str:
    return SERIAL_PREFIXES.get(serial[:3], "unknown")
