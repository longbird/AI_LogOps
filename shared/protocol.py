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
    CMD_LOG = 0x15
    CMD_LOG_ACK = 0x16
    LOG_FILE_LIST = 0x17
    LOG_FILE_SELECT = 0x18
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


class LogAction(IntEnum):
    HIST_REQUEST = 0x01
    REAL_START = 0x02
    REAL_STOP = 0x03


class LogAckStatus(IntEnum):
    SUCCESS = 0x00
    FAILED = 0x01


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
class LogHistPayload:
    """LOG_HIST: [FileName(256B)] [FileSize(4B)] [Data(variable)]."""

    filename: str
    data: bytes

    _NAME_SIZE: ClassVar[int] = 256
    _HEADER_STRUCT: ClassVar[struct.Struct] = struct.Struct("!256sI")
    _HEADER_SIZE: ClassVar[int] = 260

    def pack(self) -> bytes:
        data_len = len(self.data)
        if data_len > 0xFFFFFFFF:
            raise ValueError("data exceeds 4-byte filesize field")
        return (
            self._HEADER_STRUCT.pack(
                _encode_fixed(self.filename, self._NAME_SIZE, "filename"),
                data_len,
            )
            + self.data
        )

    @classmethod
    def unpack(cls, data: bytes) -> LogHistPayload:
        if len(data) < cls._HEADER_SIZE:
            raise ValueError("log history payload is too short")
        filename_raw, file_size = cast(
            tuple[bytes, int], cls._HEADER_STRUCT.unpack(data[: cls._HEADER_SIZE])
        )
        body = data[cls._HEADER_SIZE :]
        if len(body) != file_size:
            raise ValueError("log history payload data size mismatch")
        return cls(filename=_decode_fixed(filename_raw), data=body)


@dataclass(slots=True)
class LogRealPayload:
    """LOG_REAL: [FileName(256B)] [LineLen(2B)] [Line(variable)]."""

    filename: str
    line: str

    _NAME_SIZE: ClassVar[int] = 256
    _HEADER_STRUCT: ClassVar[struct.Struct] = struct.Struct("!256sH")
    _HEADER_SIZE: ClassVar[int] = 258

    def pack(self) -> bytes:
        line_bytes = self.line.encode("utf-8")
        line_len = len(line_bytes)
        if line_len > 0xFFFF:
            raise ValueError("line exceeds 2-byte length field")
        return (
            self._HEADER_STRUCT.pack(
                _encode_fixed(self.filename, self._NAME_SIZE, "filename"),
                line_len,
            )
            + line_bytes
        )

    @classmethod
    def unpack(cls, data: bytes) -> LogRealPayload:
        if len(data) < cls._HEADER_SIZE:
            raise ValueError("log real payload is too short")
        filename_raw, line_len = cast(
            tuple[bytes, int], cls._HEADER_STRUCT.unpack(data[: cls._HEADER_SIZE])
        )
        line_bytes = data[cls._HEADER_SIZE :]
        if len(line_bytes) != line_len:
            raise ValueError("log real payload line size mismatch")
        return cls(
            filename=_decode_fixed(filename_raw), line=line_bytes.decode("utf-8")
        )


@dataclass(slots=True)
class CmdDeployPayload:
    """CMD_DEPLOY: [FileSize(4B)] [SHA256(32B)] [FileName(256B)] = 292B."""

    file_size: int
    sha256: str
    filename: str

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!I32s256s")
    _SIZE: ClassVar[int] = 292

    def pack(self) -> bytes:
        if not 0 <= self.file_size <= 0xFFFFFFFF:
            raise ValueError("file_size must fit in 4 bytes")
        if len(self.sha256) != 64:
            raise ValueError("sha256 must be 64 hex characters")
        try:
            sha256_bytes = bytes.fromhex(self.sha256)
        except ValueError as exc:
            raise ValueError("sha256 must be a valid hex string") from exc
        if len(sha256_bytes) != 32:
            raise ValueError("sha256 must decode to exactly 32 bytes")
        return self._STRUCT.pack(
            self.file_size,
            sha256_bytes,
            _encode_fixed(self.filename, 256, "filename"),
        )

    @classmethod
    def unpack(cls, data: bytes) -> CmdDeployPayload:
        if len(data) != cls._SIZE:
            raise ValueError("cmd deploy payload must be exactly 292 bytes")
        file_size, sha256_raw, filename_raw = cast(
            tuple[int, bytes, bytes], cls._STRUCT.unpack(data)
        )
        return cls(
            file_size=file_size,
            sha256=sha256_raw.hex(),
            filename=_decode_fixed(filename_raw),
        )


@dataclass(slots=True)
class FileChunkPayload:
    """FILE_CHUNK: [SeqNum(4B)] [ChunkSize(2B)] [Data(variable, max 4096B)]."""

    seq_num: int
    data: bytes

    _HEADER_STRUCT: ClassVar[struct.Struct] = struct.Struct("!IH")
    _HEADER_SIZE: ClassVar[int] = 6

    def pack(self) -> bytes:
        data_len = len(self.data)
        if data_len > CHUNK_SIZE:
            raise ValueError(f"chunk data exceeds {CHUNK_SIZE} bytes")
        return self._HEADER_STRUCT.pack(self.seq_num, data_len) + self.data

    @classmethod
    def unpack(cls, data: bytes) -> FileChunkPayload:
        if len(data) < cls._HEADER_SIZE:
            raise ValueError("file chunk payload is too short")
        seq_num, chunk_size = cast(
            tuple[int, int], cls._HEADER_STRUCT.unpack(data[: cls._HEADER_SIZE])
        )
        if chunk_size > CHUNK_SIZE:
            raise ValueError(f"chunk size exceeds {CHUNK_SIZE} bytes")
        chunk_data = data[cls._HEADER_SIZE : cls._HEADER_SIZE + chunk_size]
        if len(chunk_data) != chunk_size:
            raise ValueError("file chunk payload data size mismatch")
        if len(data) != cls._HEADER_SIZE + chunk_size:
            raise ValueError("file chunk payload length mismatch")
        return cls(seq_num=seq_num, data=chunk_data)


@dataclass(slots=True)
class FileAckPayload:
    """FILE_ACK: [SeqNum(4B)] [Status(1B)] = 5B."""

    seq_num: int
    status: int

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!IB")
    _SIZE: ClassVar[int] = 5

    def pack(self) -> bytes:
        if not 0 <= self.status <= 0xFF:
            raise ValueError("status must fit in 1 byte")
        return self._STRUCT.pack(self.seq_num, self.status)

    @classmethod
    def unpack(cls, data: bytes) -> FileAckPayload:
        if len(data) != cls._SIZE:
            raise ValueError("file ack payload must be exactly 5 bytes")
        seq_num, status = cast(tuple[int, int], cls._STRUCT.unpack(data))
        return cls(seq_num=seq_num, status=status)


@dataclass(slots=True)
class CmdCtrlPayload:
    """CMD_CTRL: [Action(1B)] = 1B."""

    action: CtrlAction

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!B")
    _SIZE: ClassVar[int] = 1

    def pack(self) -> bytes:
        return self._STRUCT.pack(CtrlAction(self.action))

    @classmethod
    def unpack(cls, data: bytes) -> CmdCtrlPayload:
        if len(data) != cls._SIZE:
            raise ValueError("cmd_ctrl payload must be exactly 1 byte")
        (action_raw,) = cast(tuple[int], cls._STRUCT.unpack(data))
        return cls(action=CtrlAction(action_raw))


@dataclass(slots=True)
class CmdCtrlAckPayload:
    """CMD_CTRL_ACK: [Action(1B)] [PID(4B)] [Status(1B)] = 6B."""

    action: CtrlAction
    pid: int
    status: CtrlAckStatus

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!BIB")
    _SIZE: ClassVar[int] = 6

    def pack(self) -> bytes:
        return self._STRUCT.pack(
            CtrlAction(self.action),
            self.pid,
            CtrlAckStatus(self.status),
        )

    @classmethod
    def unpack(cls, data: bytes) -> CmdCtrlAckPayload:
        if len(data) != cls._SIZE:
            raise ValueError("cmd ctrl ack payload must be exactly 6 bytes")
        action_raw, pid, status_raw = cast(
            tuple[int, int, int], cls._STRUCT.unpack(data)
        )
        return cls(
            action=CtrlAction(action_raw),
            pid=pid,
            status=CtrlAckStatus(status_raw),
        )


@dataclass(slots=True)
class CmdLogPayload:
    """CMD_LOG: [Action(1B)][Date(8B, null-padded UTF-8 "YYYYMMDD")] = 9B."""

    action: LogAction
    date: str

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!B8s")
    _SIZE: ClassVar[int] = 9

    def pack(self) -> bytes:
        return self._STRUCT.pack(
            LogAction(self.action),
            _encode_fixed(self.date, 8, "date"),
        )

    @classmethod
    def unpack(cls, data: bytes) -> CmdLogPayload:
        if len(data) != cls._SIZE:
            raise ValueError("cmd log payload must be exactly 9 bytes")
        action_raw, date_raw = cast(tuple[int, bytes], cls._STRUCT.unpack(data))
        return cls(action=LogAction(action_raw), date=_decode_fixed(date_raw))


@dataclass(slots=True)
class CmdLogAckPayload:
    """CMD_LOG_ACK: [Action(1B)][Status(1B)][FileCount(2B)] = 4B."""

    action: LogAction
    status: LogAckStatus
    file_count: int

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!BBH")
    _SIZE: ClassVar[int] = 4

    def pack(self) -> bytes:
        return self._STRUCT.pack(
            LogAction(self.action),
            LogAckStatus(self.status),
            self.file_count,
        )

    @classmethod
    def unpack(cls, data: bytes) -> CmdLogAckPayload:
        if len(data) != cls._SIZE:
            raise ValueError("cmd log ack payload must be exactly 4 bytes")
        action_raw, status_raw, file_count = cast(
            tuple[int, int, int], cls._STRUCT.unpack(data)
        )
        return cls(
            action=LogAction(action_raw),
            status=LogAckStatus(status_raw),
            file_count=file_count,
        )


@dataclass(slots=True)
class LogFileEntry:
    """File metadata entry: [Filename(256B)][FileSize(4B)][MD5(16B)] = 276B."""

    filename: str
    file_size: int
    md5: bytes

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!256sI16s")
    _SIZE: ClassVar[int] = 276

    def pack(self) -> bytes:
        if len(self.md5) != 16:
            raise ValueError("md5 must be exactly 16 bytes")
        return self._STRUCT.pack(
            _encode_fixed(self.filename, 256, "filename"),
            self.file_size,
            self.md5,
        )

    @classmethod
    def unpack(cls, data: bytes) -> LogFileEntry:
        if len(data) != cls._SIZE:
            raise ValueError(f"log file entry must be exactly {cls._SIZE} bytes")
        filename_raw, file_size, md5 = cast(
            tuple[bytes, int, bytes], cls._STRUCT.unpack(data)
        )
        return cls(filename=_decode_fixed(filename_raw), file_size=file_size, md5=md5)


@dataclass(slots=True)
class LogFileListPayload:
    """LOG_FILE_LIST: [FileCount(2B)] + N x LogFileEntry(276B)."""

    entries: list[LogFileEntry]

    _COUNT_STRUCT: ClassVar[struct.Struct] = struct.Struct("!H")
    _COUNT_SIZE: ClassVar[int] = 2

    def pack(self) -> bytes:
        parts = [self._COUNT_STRUCT.pack(len(self.entries))]
        for entry in self.entries:
            parts.append(entry.pack())
        return b"".join(parts)

    @classmethod
    def unpack(cls, data: bytes) -> LogFileListPayload:
        if len(data) < cls._COUNT_SIZE:
            raise ValueError("log file list payload is too short")
        (count,) = cast(tuple[int], cls._COUNT_STRUCT.unpack(data[: cls._COUNT_SIZE]))
        expected = cls._COUNT_SIZE + count * LogFileEntry._SIZE
        if len(data) != expected:
            raise ValueError("log file list payload size mismatch")
        entries: list[LogFileEntry] = []
        offset = cls._COUNT_SIZE
        for _ in range(count):
            entry = LogFileEntry.unpack(data[offset : offset + LogFileEntry._SIZE])
            entries.append(entry)
            offset += LogFileEntry._SIZE
        return cls(entries=entries)


@dataclass(slots=True)
class LogFileSelectPayload:
    """LOG_FILE_SELECT: [FileCount(2B)] + N x [Filename(256B)]."""

    filenames: list[str]

    _COUNT_STRUCT: ClassVar[struct.Struct] = struct.Struct("!H")
    _COUNT_SIZE: ClassVar[int] = 2
    _NAME_SIZE: ClassVar[int] = 256

    def pack(self) -> bytes:
        parts = [self._COUNT_STRUCT.pack(len(self.filenames))]
        for name in self.filenames:
            parts.append(_encode_fixed(name, self._NAME_SIZE, "filename"))
        return b"".join(parts)

    @classmethod
    def unpack(cls, data: bytes) -> LogFileSelectPayload:
        if len(data) < cls._COUNT_SIZE:
            raise ValueError("log file select payload is too short")
        (count,) = cast(tuple[int], cls._COUNT_STRUCT.unpack(data[: cls._COUNT_SIZE]))
        expected = cls._COUNT_SIZE + count * cls._NAME_SIZE
        if len(data) != expected:
            raise ValueError("log file select payload size mismatch")
        filenames: list[str] = []
        offset = cls._COUNT_SIZE
        for _ in range(count):
            name = _decode_fixed(data[offset : offset + cls._NAME_SIZE])
            filenames.append(name)
            offset += cls._NAME_SIZE
        return cls(filenames=filenames)


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
