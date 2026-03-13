from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, ClassVar, cast

HEADER_SIZE = 5
MAX_PAYLOAD_SIZE = 10 * 1024 * 1024
CHUNK_SIZE = 65535  # max value for 2-byte (H) chunk-size field


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
    CMD_REC = 0x28
    CMD_REC_ACK = 0x29
    REC_ANALYSIS_RESULT = 0x30
    REC_UPLOAD_REQ = 0x31
    REC_UPLOAD_ACK = 0x32
    STT_RESULT = 0x35
    REC_DATA_REQ = 0x36
    REC_DATA_RESP = 0x37
    CMD_CONFIG = 0x40
    CMD_CONFIG_ACK = 0x41
    CMD_EXEC = 0x50
    CMD_EXEC_ACK = 0x51
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


class DeployTarget(IntEnum):
    AGENT = 0x00
    PROCESS = 0x01
    REC_CLIENT = 0x02


class ConfigAction(IntEnum):
    GET = 1
    UPDATE = 2


class RecAction(IntEnum):
    START = 0x01
    STOP = 0x02
    NEXT = 0x03  # 서버 → 에이전트: 다음 1건 분석 진행


class RecAckStatus(IntEnum):
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
    """LOG_REAL: [FolderIndex(1B)] [FileName(256B)] [LineLen(2B)] [Line(variable)]."""

    filename: str
    line: str
    folder_index: int = 0  # 0-based index into agent's watch_dirs list
    _NAME_SIZE: ClassVar[int] = 256
    _HEADER_STRUCT: ClassVar[struct.Struct] = struct.Struct("!B256sH")
    _HEADER_SIZE: ClassVar[int] = 259

    def pack(self) -> bytes:
        line_bytes = self.line.encode("utf-8")
        line_len = len(line_bytes)
        if line_len > 0xFFFF:
            raise ValueError("line exceeds 2-byte length field")
        if not 0 <= self.folder_index <= 0xFF:
            raise ValueError("folder_index must fit in 1 byte")
        return (
            self._HEADER_STRUCT.pack(
                self.folder_index,
                _encode_fixed(self.filename, self._NAME_SIZE, "filename"),
                line_len,
            )
            + line_bytes
        )

    # Legacy format (no folder_index): [FileName(256B)] [LineLen(2B)] [Line(variable)]
    _LEGACY_STRUCT: ClassVar[struct.Struct] = struct.Struct("!256sH")
    _LEGACY_HEADER_SIZE: ClassVar[int] = 258

    @classmethod
    def unpack(cls, data: bytes) -> LogRealPayload:
        # Try new format first: [FolderIndex(1B)] [FileName(256B)] [LineLen(2B)] [Line]
        if len(data) >= cls._HEADER_SIZE:
            folder_index, filename_raw, line_len = cast(
                tuple[int, bytes, int],
                cls._HEADER_STRUCT.unpack(data[: cls._HEADER_SIZE]),
            )
            line_bytes = data[cls._HEADER_SIZE :]
            if len(line_bytes) == line_len:
                return cls(
                    filename=_decode_fixed(filename_raw),
                    line=line_bytes.decode("utf-8"),
                    folder_index=folder_index,
                )
        # Fallback: legacy format without folder_index
        if len(data) >= cls._LEGACY_HEADER_SIZE:
            filename_raw, line_len = cast(
                tuple[bytes, int],
                cls._LEGACY_STRUCT.unpack(data[: cls._LEGACY_HEADER_SIZE]),
            )
            line_bytes = data[cls._LEGACY_HEADER_SIZE :]
            if len(line_bytes) == line_len:
                return cls(
                    filename=_decode_fixed(filename_raw),
                    line=line_bytes.decode("utf-8"),
                    folder_index=0,
                )
        raise ValueError("log real payload: neither new nor legacy format matched")


@dataclass(slots=True)
class CmdDeployPayload:
    """CMD_DEPLOY: [FileSize(4B)] [SHA256(32B)] [FileName(256B)] [DeployTarget(1B)] = 293B.

    Optional extension: [PathLen(2B)][PathBytes(variable)] appended when deploy_path is set.
    """

    file_size: int
    sha256: str
    filename: str
    deploy_target: DeployTarget = DeployTarget.AGENT
    deploy_path: str = ""

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!I32s256sB")
    _SIZE: ClassVar[int] = 293

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
        base = self._STRUCT.pack(
            self.file_size,
            sha256_bytes,
            _encode_fixed(self.filename, 256, "filename"),
            DeployTarget(self.deploy_target),
        )
        if self.deploy_path:
            path_bytes = self.deploy_path.encode("utf-8")
            base += struct.pack("!H", len(path_bytes)) + path_bytes
        return base

    @classmethod
    def unpack(cls, data: bytes) -> CmdDeployPayload:
        if len(data) < cls._SIZE:
            raise ValueError("cmd deploy payload must be at least 293 bytes")
        file_size, sha256_raw, filename_raw, deploy_target_raw = cast(
            tuple[int, bytes, bytes, int], cls._STRUCT.unpack(data[: cls._SIZE])
        )
        deploy_path = ""
        if len(data) > cls._SIZE:
            path_len_struct = struct.Struct("!H")
            (path_len,) = path_len_struct.unpack(data[cls._SIZE : cls._SIZE + 2])
            deploy_path = data[cls._SIZE + 2 : cls._SIZE + 2 + path_len].decode("utf-8")
        return cls(
            file_size=file_size,
            sha256=sha256_raw.hex(),
            filename=_decode_fixed(filename_raw),
            deploy_target=DeployTarget(deploy_target_raw),
            deploy_path=deploy_path,
        )


@dataclass(slots=True)
class FileChunkPayload:
    """FILE_CHUNK: [SeqNum(4B)] [ChunkSize(2B)] [Data(variable, max 65535B)]."""

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
class RecAnalysisPayload:
    filename: str
    status: str
    left_rms_db: float
    right_rms_db: float
    left_silence_ratio: float
    right_silence_ratio: float
    dropout_count: int
    duration_wav: float
    duration_smdr: float
    is_stereo: bool
    in_out: int = 0  # 1=수신, 2=발신, 0=알수없음

    def pack(self) -> bytes:
        data = {
            "filename": self.filename,
            "status": self.status,
            "left_rms_db": self.left_rms_db,
            "right_rms_db": self.right_rms_db,
            "left_silence_ratio": self.left_silence_ratio,
            "right_silence_ratio": self.right_silence_ratio,
            "dropout_count": self.dropout_count,
            "duration_wav": self.duration_wav,
            "duration_smdr": self.duration_smdr,
            "is_stereo": self.is_stereo,
            "in_out": self.in_out,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> RecAnalysisPayload:
        decoded_raw = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        if not isinstance(decoded_raw, dict):
            raise ValueError("rec analysis payload JSON must be an object")
        return cls(
            filename=str(decoded_raw["filename"]),
            status=str(decoded_raw["status"]),
            left_rms_db=float(decoded_raw["left_rms_db"]),
            right_rms_db=float(decoded_raw["right_rms_db"]),
            left_silence_ratio=float(decoded_raw["left_silence_ratio"]),
            right_silence_ratio=float(decoded_raw["right_silence_ratio"]),
            dropout_count=int(decoded_raw["dropout_count"]),
            duration_wav=float(decoded_raw["duration_wav"]),
            duration_smdr=float(decoded_raw["duration_smdr"]),
            is_stereo=bool(decoded_raw["is_stereo"]),
            in_out=int(decoded_raw.get("in_out", 0)),
        )


@dataclass(slots=True)
class RecUploadReqPayload:
    filename: str
    upload_url: str

    def pack(self) -> bytes:
        data = {
            "filename": self.filename,
            "upload_url": self.upload_url,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> RecUploadReqPayload:
        decoded_raw = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        if not isinstance(decoded_raw, dict):
            raise ValueError("rec upload req payload JSON must be an object")
        return cls(
            filename=str(decoded_raw["filename"]),
            upload_url=str(decoded_raw["upload_url"]),
        )


@dataclass(slots=True)
class RecUploadAckPayload:
    filename: str
    status: int
    file_size: int

    def pack(self) -> bytes:
        data = {
            "filename": self.filename,
            "status": self.status,
            "file_size": self.file_size,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> RecUploadAckPayload:
        decoded_raw = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        if not isinstance(decoded_raw, dict):
            raise ValueError("rec upload ack payload JSON must be an object")
        return cls(
            filename=str(decoded_raw["filename"]),
            status=int(decoded_raw["status"]),
            file_size=int(decoded_raw["file_size"]),
        )


@dataclass(slots=True)
class CmdCtrlPayload:
    """CMD_CTRL: [Action(1B)] [Target(1B)] = 2B.

    target: DeployTarget 값 (0=AGENT, 1=PROCESS, 2=REC_CLIENT).
    레거시(1B) 수신 시 target=1(PROCESS) 기본값.
    """

    action: CtrlAction
    target: int = 1  # DeployTarget.PROCESS

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!BB")
    _SIZE: ClassVar[int] = 2
    _LEGACY_SIZE: ClassVar[int] = 1

    def pack(self) -> bytes:
        return self._STRUCT.pack(CtrlAction(self.action), self.target)

    @classmethod
    def unpack(cls, data: bytes) -> CmdCtrlPayload:
        if len(data) == cls._SIZE:
            action_raw, target_raw = cast(tuple[int, int], cls._STRUCT.unpack(data))
            return cls(action=CtrlAction(action_raw), target=target_raw)
        if len(data) == cls._LEGACY_SIZE:
            (action_raw,) = cast(tuple[int], struct.unpack("!B", data))
            return cls(action=CtrlAction(action_raw), target=1)
        raise ValueError(
            f"cmd_ctrl payload: expected {cls._SIZE} or {cls._LEGACY_SIZE} bytes, got {len(data)}"
        )


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
    """CMD_LOG: [Action(1B)][Date(8B)][FolderIndex(1B, signed)] = 10B."""

    action: LogAction
    date: str
    folder_index: int = -1  # -1=전체, 0+=특정 폴더

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!B8sb")
    _SIZE: ClassVar[int] = 10

    # Legacy format (no folder_index)
    _LEGACY_STRUCT: ClassVar[struct.Struct] = struct.Struct("!B8s")
    _LEGACY_SIZE: ClassVar[int] = 9

    def pack(self) -> bytes:
        return self._STRUCT.pack(
            LogAction(self.action),
            _encode_fixed(self.date, 8, "date"),
            self.folder_index,
        )

    @classmethod
    def unpack(cls, data: bytes) -> CmdLogPayload:
        if len(data) == cls._SIZE:
            action_raw, date_raw, folder_index = cast(
                tuple[int, bytes, int], cls._STRUCT.unpack(data)
            )
            return cls(
                action=LogAction(action_raw),
                date=_decode_fixed(date_raw),
                folder_index=folder_index,
            )
        if len(data) == cls._LEGACY_SIZE:
            action_raw, date_raw = cast(
                tuple[int, bytes], cls._LEGACY_STRUCT.unpack(data)
            )
            return cls(action=LogAction(action_raw), date=_decode_fixed(date_raw))
        raise ValueError("cmd log payload must be 9 or 10 bytes")


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
class CmdRecPayload:
    """CMD_REC: [Action(1B)][Date(8B, null-padded UTF-8 "YYYYMMDD")] = 9B."""

    action: RecAction
    date: str

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!B8s")
    _SIZE: ClassVar[int] = 9

    def pack(self) -> bytes:
        return self._STRUCT.pack(
            RecAction(self.action),
            _encode_fixed(self.date, 8, "date"),
        )

    @classmethod
    def unpack(cls, data: bytes) -> CmdRecPayload:
        if len(data) != cls._SIZE:
            raise ValueError("cmd rec payload must be exactly 9 bytes")
        action_raw, date_raw = cast(tuple[int, bytes], cls._STRUCT.unpack(data))
        return cls(action=RecAction(action_raw), date=_decode_fixed(date_raw))


@dataclass(slots=True)
class CmdRecAckPayload:
    """CMD_REC_ACK: [Action(1B)][Status(1B)] = 2B."""

    action: RecAction
    status: RecAckStatus

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!BB")
    _SIZE: ClassVar[int] = 2

    def pack(self) -> bytes:
        return self._STRUCT.pack(
            RecAction(self.action),
            RecAckStatus(self.status),
        )

    @classmethod
    def unpack(cls, data: bytes) -> CmdRecAckPayload:
        if len(data) != cls._SIZE:
            raise ValueError("cmd rec ack payload must be exactly 2 bytes")
        action_raw, status_raw = cast(tuple[int, int], cls._STRUCT.unpack(data))
        return cls(
            action=RecAction(action_raw),
            status=RecAckStatus(status_raw),
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


class ProcessStatus(IntEnum):
    """프로세스 모니터링 상태 (heartbeat에 포함)."""

    NOT_MONITORED = 0  # target_process 미설정
    RUNNING = 1  # 프로세스 실행 중
    DOWN = 2  # 프로세스 종료됨


@dataclass(slots=True)
class HeartbeatPayload:
    timestamp: int
    cpu_percent: int
    mem_percent: int
    process_status: int = 0  # ProcessStatus (0=미설정, 1=실행중, 2=다운)

    _STRUCT_V1: ClassVar[struct.Struct] = struct.Struct("!QBB")
    _STRUCT_V2: ClassVar[struct.Struct] = struct.Struct("!QBBB")
    _SIZE_V1: ClassVar[int] = 10
    _SIZE_V2: ClassVar[int] = 11

    def pack(self) -> bytes:
        if not 0 <= self.cpu_percent <= 100:
            raise ValueError("cpu_percent must be between 0 and 100")
        if not 0 <= self.mem_percent <= 100:
            raise ValueError("mem_percent must be between 0 and 100")
        return self._STRUCT_V2.pack(
            self.timestamp, self.cpu_percent, self.mem_percent, self.process_status
        )

    @classmethod
    def unpack(cls, data: bytes) -> HeartbeatPayload:
        if len(data) == cls._SIZE_V1:
            # 구버전 에이전트 (process_status 없음)
            timestamp, cpu_percent, mem_percent = cast(
                tuple[int, int, int], cls._STRUCT_V1.unpack(data)
            )
            return cls(
                timestamp=timestamp,
                cpu_percent=cpu_percent,
                mem_percent=mem_percent,
                process_status=ProcessStatus.NOT_MONITORED,
            )
        if len(data) == cls._SIZE_V2:
            timestamp, cpu_percent, mem_percent, process_status = cast(
                tuple[int, int, int, int], cls._STRUCT_V2.unpack(data)
            )
            return cls(
                timestamp=timestamp,
                cpu_percent=cpu_percent,
                mem_percent=mem_percent,
                process_status=process_status,
            )
        raise ValueError(
            f"heartbeat payload must be {cls._SIZE_V1} or {cls._SIZE_V2} bytes, "
            f"got {len(data)}"
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


# ---------------------------------------------------------------------------
# STT 결과 + 녹취 데이터 조회 (JSON 기반 가변 길이)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SttResultPayload:
    """STT_RESULT: 서버 → 에이전트. STT + 통화품질 분석 결과."""

    filename: str
    agent_id: str
    full_text: str
    agent_text: str
    customer_text: str
    segments_json: str
    duration_sec: float
    word_count: int
    score_total: float
    score_response: float
    score_phrase: float
    score_silence: float
    # 통화품질 상세
    first_response_sec: float = 0.0
    agent_talk_ratio: float = 0.0
    customer_talk_ratio: float = 0.0
    silence_ratio: float = 0.0
    required_phrase_hit: bool = False
    required_phrase_list: str = ""
    forbidden_word_hit: bool = False
    forbidden_word_list: str = ""
    # STT 엔진 정보
    stt_model: str = "faster-whisper-medium"
    # 음질 분석 결과 (서버에서 분석 후 함께 전송)
    aq_status: str = ""
    aq_left_rms_db: float = 0.0
    aq_right_rms_db: float = 0.0
    aq_left_silence: float = 0.0
    aq_right_silence: float = 0.0
    aq_dropout_count: int = 0
    aq_duration_wav: float = 0.0
    aq_is_stereo: bool = False

    def pack(self) -> bytes:
        data: dict[str, object] = {
            "filename": self.filename,
            "agent_id": self.agent_id,
            "full_text": self.full_text,
            "agent_text": self.agent_text,
            "customer_text": self.customer_text,
            "segments_json": self.segments_json,
            "duration_sec": self.duration_sec,
            "word_count": self.word_count,
            "score_total": self.score_total,
            "score_response": self.score_response,
            "score_phrase": self.score_phrase,
            "score_silence": self.score_silence,
            "first_response_sec": self.first_response_sec,
            "agent_talk_ratio": self.agent_talk_ratio,
            "customer_talk_ratio": self.customer_talk_ratio,
            "silence_ratio": self.silence_ratio,
            "required_phrase_hit": self.required_phrase_hit,
            "required_phrase_list": self.required_phrase_list,
            "forbidden_word_hit": self.forbidden_word_hit,
            "forbidden_word_list": self.forbidden_word_list,
            "aq_status": self.aq_status,
            "aq_left_rms_db": self.aq_left_rms_db,
            "aq_right_rms_db": self.aq_right_rms_db,
            "aq_left_silence": self.aq_left_silence,
            "aq_right_silence": self.aq_right_silence,
            "aq_dropout_count": self.aq_dropout_count,
            "aq_duration_wav": self.aq_duration_wav,
            "aq_is_stereo": self.aq_is_stereo,
            "stt_model": self.stt_model,
        }
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )

    @classmethod
    def unpack(cls, data: bytes) -> SttResultPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            filename=str(d["filename"]),
            agent_id=str(d["agent_id"]),
            full_text=str(d.get("full_text", "")),
            agent_text=str(d.get("agent_text", "")),
            customer_text=str(d.get("customer_text", "")),
            segments_json=str(d.get("segments_json", "[]")),
            duration_sec=float(d.get("duration_sec", 0.0)),
            word_count=int(d.get("word_count", 0)),
            score_total=float(d.get("score_total", 0.0)),
            score_response=float(d.get("score_response", 0.0)),
            score_phrase=float(d.get("score_phrase", 0.0)),
            score_silence=float(d.get("score_silence", 0.0)),
            first_response_sec=float(d.get("first_response_sec", 0.0)),
            agent_talk_ratio=float(d.get("agent_talk_ratio", 0.0)),
            customer_talk_ratio=float(d.get("customer_talk_ratio", 0.0)),
            silence_ratio=float(d.get("silence_ratio", 0.0)),
            required_phrase_hit=bool(d.get("required_phrase_hit", False)),
            required_phrase_list=str(d.get("required_phrase_list", "")),
            forbidden_word_hit=bool(d.get("forbidden_word_hit", False)),
            forbidden_word_list=str(d.get("forbidden_word_list", "")),
            aq_status=str(d.get("aq_status", "")),
            aq_left_rms_db=float(d.get("aq_left_rms_db", 0.0)),
            aq_right_rms_db=float(d.get("aq_right_rms_db", 0.0)),
            aq_left_silence=float(d.get("aq_left_silence", 0.0)),
            aq_right_silence=float(d.get("aq_right_silence", 0.0)),
            aq_dropout_count=int(d.get("aq_dropout_count", 0)),
            aq_duration_wav=float(d.get("aq_duration_wav", 0.0)),
            aq_is_stereo=bool(d.get("aq_is_stereo", False)),
            stt_model=str(d.get("stt_model", "faster-whisper-medium")),
        )


@dataclass(slots=True)
class RecDataReqPayload:
    """REC_DATA_REQ: 서버 → 에이전트. 녹취 데이터 조회 요청."""

    query_type: str  # "list" | "detail" | "file_search" | "wav_file"
    date_str: str  # YYYYMMDD (list 필터)
    filename: str = ""  # detail / wav_file 조회 시
    search: str = ""  # file_search: 전화번호/파일명 검색어

    def pack(self) -> bytes:
        data: dict[str, object] = {
            "query_type": self.query_type,
            "date_str": self.date_str,
            "filename": self.filename,
            "search": self.search,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> RecDataReqPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            query_type=str(d.get("query_type", "list")),
            date_str=str(d.get("date_str", "")),
            filename=str(d.get("filename", "")),
            search=str(d.get("search", "")),
        )


@dataclass(slots=True)
class RecDataRespPayload:
    """REC_DATA_RESP: 에이전트 → 서버. 녹취 데이터 조회 결과."""

    query_type: str
    records: list[dict[str, Any]]

    def pack(self) -> bytes:
        data: dict[str, object] = {
            "query_type": self.query_type,
            "records": self.records,
        }
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )

    @classmethod
    def unpack(cls, data: bytes) -> RecDataRespPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            query_type=str(d.get("query_type", "list")),
            records=list(d.get("records", [])),
        )


class CmdConfigPayload:
    """설정 명령 페이로드."""
    def __init__(self, action: ConfigAction, config_data: str = ""):
        self.action = action
        self.config_data = config_data  # JSON-encoded config string

    def pack(self) -> bytes:
        data = json.dumps({
            "action": self.action.value,
            "config_data": self.config_data,
        }).encode("utf-8")
        return data

    @classmethod
    def unpack(cls, data: bytes) -> "CmdConfigPayload":
        obj = json.loads(data.decode("utf-8"))
        return cls(
            action=ConfigAction(obj["action"]),
            config_data=obj.get("config_data", ""),
        )


@dataclass(slots=True)
class CmdExecPayload:
    """CMD_EXEC: 서버 → 에이전트. 원격 커맨드 실행 요청."""

    command_name: str

    def pack(self) -> bytes:
        data = {"command_name": self.command_name}
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdExecPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(command_name=str(d["command_name"]))


@dataclass(slots=True)
class CmdExecAckPayload:
    """CMD_EXEC_ACK: 에이전트 → 서버. 커맨드 실행 결과."""

    command_name: str
    success: bool
    exit_code: int = 0
    output: str = ""
    error: str = ""

    def pack(self) -> bytes:
        data: dict[str, object] = {
            "command_name": self.command_name,
            "success": self.success,
            "exit_code": self.exit_code,
            "output": self.output,
            "error": self.error,
        }
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdExecAckPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            command_name=str(d["command_name"]),
            success=bool(d["success"]),
            exit_code=int(d.get("exit_code", 0)),
            output=str(d.get("output", "")),
            error=str(d.get("error", "")),
        )


class Packet:
    _TYPE_LABELS: ClassVar[dict[int, str]] = {
        PacketType.AUTH: "AUTH",
        PacketType.AUTH_ACK: "AUTH_ACK",
        PacketType.LOG_HIST: "LOG_HIST",
        PacketType.LOG_REAL: "LOG_REAL",
        PacketType.CMD_DEPLOY: "CMD_DEPLOY",
        PacketType.FILE_CHUNK: "FILE_CHUNK",
        PacketType.FILE_ACK: "FILE_ACK",
        PacketType.CMD_CTRL: "CMD_CTRL",
        PacketType.CMD_CTRL_ACK: "CMD_CTRL_ACK",
        PacketType.CMD_LOG: "CMD_LOG",
        PacketType.CMD_LOG_ACK: "CMD_LOG_ACK",
        PacketType.LOG_FILE_LIST: "LOG_FILE_LIST",
        PacketType.LOG_FILE_SELECT: "LOG_FILE_SELECT",
        PacketType.AGENT_UPDATE: "AGENT_UPDATE",
        PacketType.CMD_REC: "CMD_REC",
        PacketType.CMD_REC_ACK: "CMD_REC_ACK",
        PacketType.REC_ANALYSIS_RESULT: "REC_ANALYSIS_RESULT",
        PacketType.REC_UPLOAD_REQ: "REC_UPLOAD_REQ",
        PacketType.REC_UPLOAD_ACK: "REC_UPLOAD_ACK",
        PacketType.STT_RESULT: "STT_RESULT",
        PacketType.REC_DATA_REQ: "REC_DATA_REQ",
        PacketType.REC_DATA_RESP: "REC_DATA_RESP",
        PacketType.CMD_CONFIG: "CMD_CONFIG",
        PacketType.CMD_CONFIG_ACK: "CMD_CONFIG_ACK",
        PacketType.CMD_EXEC: "CMD_EXEC",
        PacketType.CMD_EXEC_ACK: "CMD_EXEC_ACK",
        PacketType.HEARTBEAT: "HEARTBEAT",
        PacketType.DISCONNECT: "DISCONNECT",
    }

    @staticmethod
    def _type_label(packet_type: PacketType | int) -> str:
        return Packet._TYPE_LABELS.get(int(packet_type), f"UNKNOWN(0x{int(packet_type):02X})")

    @staticmethod
    def build(packet_type: PacketType | int, payload_bytes: bytes) -> bytes:
        header = PacketHeader.pack(
            packet_type=packet_type, payload_length=len(payload_bytes)
        )
        return header + payload_bytes

    @staticmethod
    def parse_header(header_bytes: bytes) -> tuple[PacketType, int]:
        return PacketHeader.unpack(header_bytes)
