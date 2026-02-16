from __future__ import annotations

import struct
from dataclasses import dataclass
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


def _encode_fixed(value: str, size: int, field_name: str) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) > size:
        raise ValueError(f"{field_name} exceeds {size} bytes")
    return encoded.ljust(size, b"\x00")


def _decode_fixed(value: bytes) -> str:
    return value.rstrip(b"\x00").decode("utf-8")


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


@dataclass(slots=True)
class AuthPayload:
    agent_id: str
    version: str
    token: str

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!32s8s64s")
    _SIZE: ClassVar[int] = 104

    def pack(self) -> bytes:
        if len(self.token.encode("utf-8")) != 64:
            raise ValueError("token must be exactly 64 bytes")
        return self._STRUCT.pack(
            _encode_fixed(self.agent_id, 32, "agent_id"),
            _encode_fixed(self.version, 8, "version"),
            _encode_fixed(self.token, 64, "token"),
        )

    @classmethod
    def unpack(cls, data: bytes) -> AuthPayload:
        if len(data) != cls._SIZE:
            raise ValueError("auth payload must be exactly 104 bytes")
        agent_id, version, token = cast(
            tuple[bytes, bytes, bytes], cls._STRUCT.unpack(data)
        )
        return cls(
            agent_id=_decode_fixed(agent_id),
            version=_decode_fixed(version),
            token=_decode_fixed(token),
        )


@dataclass(slots=True)
class AuthAckPayload:
    status: AuthStatus
    session_id: str

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!B16s")
    _SIZE: ClassVar[int] = 17

    def pack(self) -> bytes:
        return self._STRUCT.pack(
            AuthStatus(self.status),
            _encode_fixed(self.session_id, 16, "session_id"),
        )

    @classmethod
    def unpack(cls, data: bytes) -> AuthAckPayload:
        if len(data) != cls._SIZE:
            raise ValueError("auth ack payload must be exactly 17 bytes")
        status_raw, session_id = cast(tuple[int, bytes], cls._STRUCT.unpack(data))
        return cls(status=AuthStatus(status_raw), session_id=_decode_fixed(session_id))


@dataclass(slots=True)
class HeartbeatPayload:
    timestamp: int
    cpu_percent: int
    mem_percent: int

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!QBB")
    _SIZE: ClassVar[int] = 10

    def pack(self) -> bytes:
        if not 0 <= self.cpu_percent <= 100:
            raise ValueError("cpu_percent must be between 0 and 100")
        if not 0 <= self.mem_percent <= 100:
            raise ValueError("mem_percent must be between 0 and 100")
        return self._STRUCT.pack(self.timestamp, self.cpu_percent, self.mem_percent)

    @classmethod
    def unpack(cls, data: bytes) -> HeartbeatPayload:
        if len(data) != cls._SIZE:
            raise ValueError("heartbeat payload must be exactly 10 bytes")
        timestamp, cpu_percent, mem_percent = cast(
            tuple[int, int, int], cls._STRUCT.unpack(data)
        )
        return cls(
            timestamp=timestamp, cpu_percent=cpu_percent, mem_percent=mem_percent
        )


@dataclass(slots=True)
class DisconnectPayload:
    reason: DisconnectReason

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!B")
    _SIZE: ClassVar[int] = 1

    def pack(self) -> bytes:
        return self._STRUCT.pack(DisconnectReason(self.reason))

    @classmethod
    def unpack(cls, data: bytes) -> DisconnectPayload:
        if len(data) != cls._SIZE:
            raise ValueError("disconnect payload must be exactly 1 byte")
        (reason_raw,) = cast(tuple[int], cls._STRUCT.unpack(data))
        return cls(reason=DisconnectReason(reason_raw))


class Packet:
    @staticmethod
    def build(packet_type: PacketType | int, payload_bytes: bytes) -> bytes:
        header = PacketHeader.pack(
            packet_type=packet_type, payload_length=len(payload_bytes)
        )
        return header + payload_bytes

    @staticmethod
    def parse_header(header_bytes: bytes) -> tuple[PacketType, int]:
        return PacketHeader.unpack(header_bytes)
