from __future__ import annotations

import struct
from enum import IntEnum
from typing import ClassVar, cast

HEADER_SIZE = 5
MAX_PAYLOAD_SIZE = 10 * 1024 * 1024
CHUNK_SIZE = 4096


class PacketType(IntEnum):
    AUTH = 0x01
    AUTH_ACK = 0x02
    LOG_HIST = 0x03
    LOG_REAL = 0x04
    CMD_DEPLOY = 0x10
    FILE_CHUNK = 0x11
    FILE_ACK = 0x12
    CMD_CTRL = 0x13
    CMD_CTRL_ACK = 0x14
    AGENT_UPDATE = 0x20
    HEARTBEAT = 0xFE
    DISCONNECT = 0xFF


class AuthStatus(IntEnum):
    SUCCESS = 0x00
    FAILED = 0x01
    VERSION_MISMATCH = 0x02


class CtrlAction(IntEnum):
    STOP = 0x00
    START = 0x01
    RESTART = 0x02


class CtrlAckStatus(IntEnum):
    SUCCESS = 0x00
    FAILED = 0x01
    DEPLOY_VERIFIED = 0x10
    DEPLOY_ROLLBACK = 0x11


class DisconnectReason(IntEnum):
    NORMAL = 0x00
    ERROR = 0x01
    HEARTBEAT_TIMEOUT = 0x02
    SERVER_SHUTDOWN = 0x03


def _validate_payload_length(payload_length: int) -> None:
    if payload_length < 0:
        raise ValueError("payload_length cannot be negative")
    if payload_length > MAX_PAYLOAD_SIZE:
        raise ValueError("payload_length exceeds maximum size")


class PacketHeader:
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!BI")

    @classmethod
    def pack(cls, packet_type: PacketType | int, payload_length: int) -> bytes:
        _validate_payload_length(payload_length)
        return cls._STRUCT.pack(PacketType(packet_type), payload_length)

    @classmethod
    def unpack(cls, data: bytes) -> tuple[PacketType, int]:
        if len(data) != HEADER_SIZE:
            raise ValueError(f"header must be exactly {HEADER_SIZE} bytes")
        packet_type_raw, payload_length = cast(
            tuple[int, int], cls._STRUCT.unpack(data)
        )
        _validate_payload_length(payload_length)
        return PacketType(packet_type_raw), payload_length
