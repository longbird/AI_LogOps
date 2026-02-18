# Selective Log Transfer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Change HIST_REQUEST flow from "send all files" to 3-step selective transfer: agent sends file list metadata → server compares and selects → agent sends only selected files.

**Architecture:** Two new PacketTypes (LOG_FILE_LIST=0x17, LOG_FILE_SELECT=0x18) enable a metadata-first handshake before file transfer. Agent collects filename+size+MD5 per file. Server compares with local storage using all three fields. Agent uses asyncio.Event to bridge the two phases.

**Tech Stack:** Python 3.10+, asyncio, struct, hashlib (MD5), dataclasses(slots=True)

**Baseline:** 369 tests passing. Run `python -m pytest tests/ -v --tb=short` to verify.

---

### Task 1: Protocol Types — LogFileEntry, LogFileListPayload, LogFileSelectPayload

**Files:**
- Modify: `shared/protocol.py` (add PacketType entries + 3 dataclasses)
- Test: `tests/test_protocol.py`

**Step 1: Write failing tests for new payload types**

Add to `tests/test_protocol.py`:

```python
from shared.protocol import LogFileEntry, LogFileListPayload, LogFileSelectPayload

def test_log_file_entry_roundtrip() -> None:
    entry = LogFileEntry(filename="20260217_app.txt", file_size=1024, md5=b"\xab" * 16)
    packed = entry.pack()
    assert len(packed) == 276  # 256 + 4 + 16
    unpacked = LogFileEntry.unpack(packed)
    assert unpacked.filename == "20260217_app.txt"
    assert unpacked.file_size == 1024
    assert unpacked.md5 == b"\xab" * 16


def test_log_file_entry_empty_filename() -> None:
    entry = LogFileEntry(filename="", file_size=0, md5=b"\x00" * 16)
    packed = entry.pack()
    unpacked = LogFileEntry.unpack(packed)
    assert unpacked.filename == ""
    assert unpacked.file_size == 0


def test_log_file_entry_invalid_md5_length() -> None:
    with pytest.raises(ValueError, match="md5 must be exactly 16 bytes"):
        LogFileEntry(filename="test.txt", file_size=100, md5=b"\xab" * 10).pack()


def test_log_file_list_payload_roundtrip() -> None:
    entries = [
        LogFileEntry(filename="20260217_app.txt", file_size=1024, md5=b"\xaa" * 16),
        LogFileEntry(filename="20260217_error.txt", file_size=512, md5=b"\xbb" * 16),
    ]
    payload = LogFileListPayload(entries=entries)
    packed = payload.pack()
    unpacked = LogFileListPayload.unpack(packed)
    assert len(unpacked.entries) == 2
    assert unpacked.entries[0].filename == "20260217_app.txt"
    assert unpacked.entries[0].file_size == 1024
    assert unpacked.entries[0].md5 == b"\xaa" * 16
    assert unpacked.entries[1].filename == "20260217_error.txt"
    assert unpacked.entries[1].file_size == 512


def test_log_file_list_payload_empty() -> None:
    payload = LogFileListPayload(entries=[])
    packed = payload.pack()
    assert len(packed) == 2  # just FileCount(2B)
    unpacked = LogFileListPayload.unpack(packed)
    assert len(unpacked.entries) == 0


def test_log_file_select_payload_roundtrip() -> None:
    filenames = ["20260217_app.txt", "20260217_error.txt"]
    payload = LogFileSelectPayload(filenames=filenames)
    packed = payload.pack()
    unpacked = LogFileSelectPayload.unpack(packed)
    assert unpacked.filenames == filenames


def test_log_file_select_payload_empty() -> None:
    payload = LogFileSelectPayload(filenames=[])
    packed = payload.pack()
    assert len(packed) == 2
    unpacked = LogFileSelectPayload.unpack(packed)
    assert len(unpacked.filenames) == 0
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_protocol.py -v --tb=short -k "log_file"`
Expected: ImportError — LogFileEntry, LogFileListPayload, LogFileSelectPayload not defined

**Step 3: Implement protocol types in `shared/protocol.py`**

Add to `PacketType` enum (after `CMD_LOG_ACK = 0x16`):
```python
LOG_FILE_LIST = 0x17
LOG_FILE_SELECT = 0x18
```

Add dataclasses (after `CmdLogAckPayload`):

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_protocol.py -v --tb=short`
Expected: ALL PASS (including new log_file tests)

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 369+ tests passing, 0 failures

**Step 6: Commit**

```bash
git add shared/protocol.py tests/test_protocol.py
git commit -m "feat: add LOG_FILE_LIST/LOG_FILE_SELECT protocol types for selective transfer"
```

---

### Task 2: LogWatcher.get_files_metadata() — File Metadata Collection

**Files:**
- Modify: `agent/core/log_watcher.py` (add `get_files_metadata` method)
- Test: `tests/test_log_watcher.py`

**Step 1: Write failing tests**

Add to `tests/test_log_watcher.py`:

```python
import hashlib
from shared.protocol import LogFileEntry


def test_get_files_metadata_returns_entries(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    f1 = log_dir / "20260217_app.txt"
    f1.write_bytes(b"hello world")
    f2 = log_dir / "20260217_error.txt"
    f2.write_bytes(b"error data here")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=AsyncMock(),
    )

    entries = watcher.get_files_metadata("20260217")

    assert len(entries) == 2
    # Sorted by filename
    assert entries[0].filename == "20260217_app.txt"
    assert entries[0].file_size == 11  # len(b"hello world")
    assert entries[0].md5 == hashlib.md5(b"hello world").digest()
    assert entries[1].filename == "20260217_error.txt"
    assert entries[1].file_size == 15
    assert entries[1].md5 == hashlib.md5(b"error data here").digest()


def test_get_files_metadata_empty_date(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260217_app.txt").write_bytes(b"data")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=AsyncMock(),
    )

    entries = watcher.get_files_metadata("20250101")
    assert entries == []


def test_get_files_metadata_filters_extensions(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260217_app.txt").write_bytes(b"data")
    (log_dir / "20260217_app.csv").write_bytes(b"csv data")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=AsyncMock(),
    )

    entries = watcher.get_files_metadata("20260217")
    assert len(entries) == 1
    assert entries[0].filename == "20260217_app.txt"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_log_watcher.py -v --tb=short -k "metadata"`
Expected: AttributeError — get_files_metadata not defined

**Step 3: Implement `get_files_metadata` in `agent/core/log_watcher.py`**

Add import at top:
```python
import hashlib
from shared.protocol import LogFileEntry
```

Add method to `LogWatcher` class (after `find_files_by_date`):
```python
def get_files_metadata(self, date_str: str) -> list[LogFileEntry]:
    """감시 폴더에서 YYYYMMDD_* 파일의 메타데이터(filename, size, MD5) 수집."""

    files = self.find_files_by_date(date_str)
    entries: list[LogFileEntry] = []
    for filepath in files:
        path = Path(filepath)
        data = path.read_bytes()
        entry = LogFileEntry(
            filename=path.name,
            file_size=len(data),
            md5=hashlib.md5(data).digest(),
        )
        entries.append(entry)
    return entries
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_log_watcher.py -v --tb=short`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 369+ tests, 0 failures

**Step 6: Commit**

```bash
git add agent/core/log_watcher.py tests/test_log_watcher.py
git commit -m "feat: add LogWatcher.get_files_metadata() for file list with MD5"
```

---

### Task 3: StorageManager.get_stored_file_metadata() — Server-Side File Comparison

**Files:**
- Modify: `server/storage/manager.py` (add `get_stored_file_metadata` method)
- Test: `tests/test_storage.py`

**Step 1: Write failing tests**

Add to `tests/test_storage.py`:

```python
import hashlib


def test_get_stored_file_metadata(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path))
    manager.save_log_history("agent-a", "20260217_app.txt", b"hello world")
    manager.save_log_history("agent-a", "20260217_error.txt", b"error data")

    metadata = manager.get_stored_file_metadata("agent-a")

    assert "20260217_app.txt" in metadata
    size, md5 = metadata["20260217_app.txt"]
    assert size == 11
    assert md5 == hashlib.md5(b"hello world").digest()

    assert "20260217_error.txt" in metadata
    size2, md52 = metadata["20260217_error.txt"]
    assert size2 == 10
    assert md52 == hashlib.md5(b"error data").digest()


def test_get_stored_file_metadata_empty(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path))
    metadata = manager.get_stored_file_metadata("agent-nonexistent")
    assert metadata == {}


def test_get_stored_file_metadata_excludes_realtime(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path))
    manager.save_log_history("agent-a", "app.txt", b"data")
    manager.append_realtime_log("agent-a", "realtime.log", "line")

    metadata = manager.get_stored_file_metadata("agent-a")
    # Only direct log files, not realtime subdirectory
    assert "app.txt" in metadata
    assert "realtime.log" not in metadata
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_storage.py -v --tb=short -k "stored_file_metadata"`
Expected: AttributeError — get_stored_file_metadata not defined

**Step 3: Implement in `server/storage/manager.py`**

Add import at top:
```python
import hashlib
```

Add method to `StorageManager` class (after `get_agent_log_content`):
```python
def get_stored_file_metadata(self, agent_id: str) -> dict[str, tuple[int, bytes]]:
    """에이전트 로그 디렉토리의 파일별 (size, md5) 반환. realtime/ 제외.

    Returns: {filename: (file_size, md5_digest)}
    """
    root = self.base_dir / "logs" / agent_id
    if not root.exists():
        return {}

    result: dict[str, tuple[int, bytes]] = {}
    for path in root.iterdir():
        if not path.is_file():
            continue
        data = path.read_bytes()
        result[path.name] = (len(data), hashlib.md5(data).digest())
    return result
```

**Note:** `root.iterdir()` only yields direct children, so `realtime/` subdirectory files are automatically excluded. Only files directly under `logs/{agent_id}/` are included.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_storage.py -v --tb=short`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 369+ tests, 0 failures

**Step 6: Commit**

```bash
git add server/storage/manager.py tests/test_storage.py
git commit -m "feat: add StorageManager.get_stored_file_metadata() for file comparison"
```

---

### Task 4: TCPClient — send_log_file_list + on_log_file_select callback

**Files:**
- Modify: `agent/core/tcp_client.py` (new method + callback + _recv_loop entry)
- Test: `tests/test_tcp_client.py`

**Step 1: Write failing tests**

Add to `tests/test_tcp_client.py`:

```python
from shared.protocol import LogFileEntry, LogFileListPayload, LogFileSelectPayload


class TestTCPClientLogFileList:
    async def test_send_log_file_list_builds_correct_packet(self):
        client = TCPClient(agent_id="test", version="1.0.0", token="tok")
        client._connected = True
        mock_writer = AsyncMock()
        mock_writer.is_closing.return_value = False
        client._writer = mock_writer

        entries = [
            LogFileEntry(filename="20260217_app.txt", file_size=1024, md5=b"\xaa" * 16),
        ]
        await client.send_log_file_list(entries)

        mock_writer.write.assert_called_once()
        mock_writer.drain.assert_called_once()
        # Verify packet type in header
        written_data = mock_writer.write.call_args[0][0]
        ptype, plen = PacketHeader.unpack(written_data[:HEADER_SIZE])
        assert ptype == PacketType.LOG_FILE_LIST

    async def test_on_log_file_select_callback_invoked(self):
        client = TCPClient(agent_id="test", version="1.0.0", token="tok")
        callback = AsyncMock()
        client.on_log_file_select = callback

        # Simulate LOG_FILE_SELECT packet in recv loop
        select_payload = LogFileSelectPayload(filenames=["20260217_app.txt"])
        packet = Packet.build(PacketType.LOG_FILE_SELECT, select_payload.pack())

        reader = AsyncMock()
        reader.readexactly = AsyncMock(side_effect=[
            packet[:HEADER_SIZE],
            packet[HEADER_SIZE:],
            asyncio.IncompleteReadError(b"", 5),  # end loop
        ])
        client._reader = reader
        client._connected = True
        client._writer = AsyncMock()

        await client._recv_loop()

        callback.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tcp_client.py -v --tb=short -k "log_file"`
Expected: AttributeError — send_log_file_list / on_log_file_select not defined

**Step 3: Implement in `agent/core/tcp_client.py`**

Add imports:
```python
from shared.protocol import (
    ...  # existing imports
    LogFileEntry,
    LogFileListPayload,
    LogFileSelectPayload,
)
```

Add to `__init__` (after `self.on_cmd_log`):
```python
self.on_log_file_select: PacketCallback | None = None
```

Add method (after `send_log_line`):
```python
async def send_log_file_list(self, entries: list[LogFileEntry]) -> None:
    """LOG_FILE_LIST 패킷으로 파일 메타데이터 목록 전송."""

    payload = LogFileListPayload(entries=entries)
    await self.send_packet(PacketType.LOG_FILE_LIST, payload.pack())
```

Add to `_recv_loop` (after CMD_LOG handler block, before DISCONNECT):
```python
if packet_type == PacketType.LOG_FILE_SELECT:
    if self.on_log_file_select is not None:
        await self.on_log_file_select(payload)
    continue
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tcp_client.py -v --tb=short`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 369+ tests, 0 failures

**Step 6: Commit**

```bash
git add agent/core/tcp_client.py tests/test_tcp_client.py
git commit -m "feat: add TCPClient.send_log_file_list() and on_log_file_select callback"
```

---

### Task 5: LogCmdHandler — 2-Phase HIST_REQUEST + handle_file_select

**Files:**
- Modify: `agent/core/log_cmd_handler.py` (redesign _handle_hist_request + add handle_file_select)
- Test: `tests/test_log_cmd_handler.py` (update existing + add new tests)

**Step 1: Write failing tests**

Replace existing `TestLogCmdHandlerHistRequest` and add new tests in `tests/test_log_cmd_handler.py`:

```python
import asyncio
import hashlib
from shared.protocol import (
    CmdLogPayload,
    CmdLogAckPayload,
    LogAction,
    LogAckStatus,
    LogFileEntry,
    LogFileListPayload,
    LogFileSelectPayload,
    PacketType,
)


class TestLogCmdHandlerHistRequest:
    """Updated tests for 2-phase selective transfer."""

    async def test_hist_request_sends_file_list(self):
        """Phase 1: HIST_REQUEST → agent sends LOG_FILE_LIST with metadata."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="20260217_app.txt", file_size=1024, md5=b"\xaa" * 16),
            LogFileEntry(filename="20260217_error.txt", file_size=512, md5=b"\xbb" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        # Start hist request in background (it will wait for file_select)
        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))

        # Give the task time to send file list
        await asyncio.sleep(0.05)

        # Verify file list was sent
        mock_client.send_log_file_list.assert_called_once_with(entries)
        # Task should still be waiting for file select
        assert not task.done()

        # Simulate file select response (select 1 file)
        select_payload = LogFileSelectPayload(filenames=["20260217_app.txt"]).pack()
        await handler.handle_file_select(select_payload)

        # Wait for task completion
        await asyncio.wait_for(task, timeout=2.0)

        # Verify only selected file was sent
        assert mock_client.send_log_history.call_count == 1
        call_args = mock_client.send_log_history.call_args
        assert call_args[0][0] == "20260217_app.txt"

    async def test_hist_request_no_files_sends_empty_list(self):
        """HIST_REQUEST with no matching files sends empty LOG_FILE_LIST."""
        mock_watcher = MagicMock()
        mock_watcher.get_files_metadata.return_value = []

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        # Empty file list sent
        mock_client.send_log_file_list.assert_called_once_with([])
        # No LOG_HIST sent (no files to send, no file_select expected)
        mock_client.send_log_history.assert_not_called()

    async def test_hist_request_all_files_selected(self):
        """Server selects all files → agent sends all."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
            LogFileEntry(filename="f2.txt", file_size=200, md5=b"\xbb" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries
        mock_watcher.find_files_by_date.return_value = ["/logs/f1.txt", "/logs/f2.txt"]
        mock_watcher.read_history.side_effect = [b"data1", b"data2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        select = LogFileSelectPayload(filenames=["f1.txt", "f2.txt"]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        assert mock_client.send_log_history.call_count == 2

    async def test_hist_request_timeout(self):
        """If server doesn't respond with file_select within timeout, abort."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries

        mock_client = AsyncMock()
        handler = LogCmdHandler(
            log_watcher=mock_watcher, tcp_client=mock_client, file_select_timeout=0.1
        )

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        # Timed out, no files sent
        mock_client.send_log_history.assert_not_called()

    async def test_hist_request_file_read_error_continues(self):
        """If one selected file fails to read, continue with others."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
            LogFileEntry(filename="f2.txt", file_size=200, md5=b"\xbb" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries
        mock_watcher.find_files_by_date.return_value = ["/logs/f1.txt", "/logs/f2.txt"]
        mock_watcher.read_history.side_effect = [OSError("read failed"), b"data2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        select = LogFileSelectPayload(filenames=["f1.txt", "f2.txt"]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        # Only 1 file sent (f2.txt succeeded)
        assert mock_client.send_log_history.call_count == 1
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_log_cmd_handler.py -v --tb=short`
Expected: Various failures — handle_file_select not defined, send_log_file_list not called, etc.

**Step 3: Implement redesigned LogCmdHandler**

Rewrite `agent/core/log_cmd_handler.py`:

```python
"""로그 전송 명령 핸들러. CMD_LOG 수신 시 처리."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from shared.protocol import (
    CmdLogAckPayload,
    CmdLogPayload,
    LogAckStatus,
    LogAction,
    LogFileSelectPayload,
    PacketType,
)
from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.log_watcher import LogWatcher
    from agent.core.tcp_client import TCPClient

logger = setup_logging("log_cmd_handler")


class LogCmdHandler:
    """CMD_LOG 패킷 처리: HIST_REQUEST / REAL_START / REAL_STOP."""

    def __init__(
        self,
        log_watcher: LogWatcher,
        tcp_client: TCPClient,
        history_max_mb: int = 10,
        file_select_timeout: float = 30.0,
    ):
        self._watcher = log_watcher
        self._client = tcp_client
        self._history_max_mb = history_max_mb
        self._file_select_timeout = file_select_timeout
        self._realtime_active = False

        # Phase 2 state: file selection event
        self._file_select_event: asyncio.Event = asyncio.Event()
        self._selected_filenames: list[str] = []
        # Map filename → absolute path for selected file lookup
        self._pending_file_map: dict[str, str] = {}

    @property
    def is_realtime_active(self) -> bool:
        return self._realtime_active

    async def handle_cmd_log(self, payload_data: bytes) -> None:
        """CMD_LOG 패킷 수신 콜백. tcp_client.on_cmd_log에 바인딩."""
        try:
            cmd = CmdLogPayload.unpack(payload_data)
        except ValueError:
            logger.warning("invalid CMD_LOG payload")
            return

        if cmd.action == LogAction.HIST_REQUEST:
            await self._handle_hist_request(cmd.date)
        elif cmd.action == LogAction.REAL_START:
            await self._handle_real_start()
        elif cmd.action == LogAction.REAL_STOP:
            await self._handle_real_stop()
        else:
            logger.warning("unknown log action: %s", cmd.action)

    async def handle_file_select(self, payload_data: bytes) -> None:
        """LOG_FILE_SELECT 수신 콜백. tcp_client.on_log_file_select에 바인딩."""
        try:
            select = LogFileSelectPayload.unpack(payload_data)
        except ValueError:
            logger.warning("invalid LOG_FILE_SELECT payload")
            return

        self._selected_filenames = select.filenames
        self._file_select_event.set()

    async def _send_ack(
        self, action: LogAction, status: LogAckStatus, file_count: int = 0
    ) -> None:
        ack = CmdLogAckPayload(action=action, status=status, file_count=file_count)
        await self._client.send_packet(PacketType.CMD_LOG_ACK, ack.pack())

    async def _handle_hist_request(self, date_str: str) -> None:
        """2-Phase HIST_REQUEST:
        Phase 1: 파일 메타데이터 수집 → LOG_FILE_LIST 전송
        Phase 2: LOG_FILE_SELECT 대기 → 선택된 파일만 LOG_HIST 전송
        """
        # Phase 1: Collect metadata and send file list
        entries = self._watcher.get_files_metadata(date_str)
        await self._client.send_log_file_list(entries)

        if not entries:
            logger.info("no files found for date=%s", date_str)
            return

        # Build filename → filepath map for Phase 2
        files = self._watcher.find_files_by_date(date_str)
        self._pending_file_map = {Path(f).name: f for f in files}

        # Phase 2: Wait for server file selection
        self._file_select_event.clear()
        self._selected_filenames = []

        try:
            await asyncio.wait_for(
                self._file_select_event.wait(),
                timeout=self._file_select_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("file select timeout for date=%s", date_str)
            self._pending_file_map.clear()
            return

        # Send only selected files
        sent_count = 0
        for filename in self._selected_filenames:
            filepath = self._pending_file_map.get(filename)
            if filepath is None:
                logger.warning("selected file not found: %s", filename)
                continue
            try:
                data = self._watcher.read_history(filepath, max_mb=self._history_max_mb)
                await self._client.send_log_history(filename, data)
                sent_count += 1
                logger.info("sent log history: %s (%d bytes)", filename, len(data))
            except Exception:
                logger.exception("failed to send log history: %s", filepath)

        await self._send_ack(LogAction.HIST_REQUEST, LogAckStatus.SUCCESS, sent_count)
        self._pending_file_map.clear()

    async def _handle_real_start(self) -> None:
        """실시간 로그 전송 시작."""
        self._realtime_active = True
        logger.info("realtime log transmission started")
        await self._send_ack(LogAction.REAL_START, LogAckStatus.SUCCESS, 0)

    async def _handle_real_stop(self) -> None:
        """실시간 로그 전송 중지."""
        self._realtime_active = False
        logger.info("realtime log transmission stopped")
        await self._send_ack(LogAction.REAL_STOP, LogAckStatus.SUCCESS, 0)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_log_cmd_handler.py -v --tb=short`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass. Some existing integration tests in `test_integration_features.py` may need updating — if they fail due to the new 2-phase flow, update them to mock `get_files_metadata` and simulate `handle_file_select`.

**Step 6: Update integration tests if needed**

In `tests/test_integration_features.py`, update `TestFeatureA_LogTransmission`:
- `test_hist_request_end_to_end`: Now needs to simulate the Phase 2 file_select response
- `test_hist_request_no_matching_date`: Should work as-is (empty file list → no wait)

**Step 7: Commit**

```bash
git add agent/core/log_cmd_handler.py tests/test_log_cmd_handler.py tests/test_integration_features.py
git commit -m "feat: redesign LogCmdHandler to 2-phase selective transfer"
```

---

### Task 6: TCPServer — _handle_log_file_list + _send_log_file_select

**Files:**
- Modify: `server/core/tcp_server.py` (new handler + send method + recv_loop entry)
- Test: `tests/test_tcp_server.py` (or add to existing test file for tcp_server)

**Step 1: Write failing tests**

Add tests (in appropriate test file, e.g. `tests/test_tcp_server.py` or `tests/test_integration_features.py`):

```python
import hashlib
from shared.protocol import (
    LogFileEntry,
    LogFileListPayload,
    LogFileSelectPayload,
    PacketType,
    HEADER_SIZE,
    PacketHeader,
)


class TestTCPServerFileListHandling:
    async def test_handle_log_file_list_selects_new_files(self):
        """Server receives file list, compares with storage, sends selection."""
        mock_storage = MagicMock()
        # Server has file1 already (same size+md5), file2 is new
        mock_storage.get_stored_file_metadata.return_value = {
            "f1.txt": (100, b"\xaa" * 16),  # matches agent's f1
        }

        mock_session_mgr = MagicMock()
        mock_writer = AsyncMock()
        mock_writer.is_closing.return_value = False
        session = MagicMock()
        session.writer = mock_writer
        mock_session_mgr.get_session.return_value = session

        server = TCPServer(
            host="0.0.0.0",
            port=0,
            session_mgr=mock_session_mgr,
            auth_token="tok",
            storage_mgr=mock_storage,
        )

        # Agent sends file list with 2 files
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
            LogFileEntry(filename="f2.txt", file_size=200, md5=b"\xbb" * 16),
        ]
        file_list_payload = LogFileListPayload(entries=entries).pack()
        await server._handle_log_file_list("agent-a", file_list_payload, mock_writer)

        # Verify: LOG_FILE_SELECT sent with only f2.txt (f1 already exists)
        mock_writer.write.assert_called_once()
        written_data = mock_writer.write.call_args[0][0]
        ptype, plen = PacketHeader.unpack(written_data[:HEADER_SIZE])
        assert ptype == PacketType.LOG_FILE_SELECT
        select = LogFileSelectPayload.unpack(written_data[HEADER_SIZE:])
        assert select.filenames == ["f2.txt"]

    async def test_handle_log_file_list_all_new(self):
        """All files are new → selects all."""
        mock_storage = MagicMock()
        mock_storage.get_stored_file_metadata.return_value = {}

        mock_writer = AsyncMock()
        mock_writer.is_closing.return_value = False
        mock_session_mgr = MagicMock()

        server = TCPServer(
            host="0.0.0.0", port=0, session_mgr=mock_session_mgr,
            auth_token="tok", storage_mgr=mock_storage,
        )

        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
            LogFileEntry(filename="f2.txt", file_size=200, md5=b"\xbb" * 16),
        ]
        file_list_payload = LogFileListPayload(entries=entries).pack()
        await server._handle_log_file_list("agent-a", file_list_payload, mock_writer)

        written_data = mock_writer.write.call_args[0][0]
        select = LogFileSelectPayload.unpack(written_data[HEADER_SIZE:])
        assert set(select.filenames) == {"f1.txt", "f2.txt"}

    async def test_handle_log_file_list_all_existing(self):
        """All files match → empty selection."""
        mock_storage = MagicMock()
        mock_storage.get_stored_file_metadata.return_value = {
            "f1.txt": (100, b"\xaa" * 16),
        }

        mock_writer = AsyncMock()
        mock_writer.is_closing.return_value = False
        mock_session_mgr = MagicMock()

        server = TCPServer(
            host="0.0.0.0", port=0, session_mgr=mock_session_mgr,
            auth_token="tok", storage_mgr=mock_storage,
        )

        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
        ]
        file_list_payload = LogFileListPayload(entries=entries).pack()
        await server._handle_log_file_list("agent-a", file_list_payload, mock_writer)

        written_data = mock_writer.write.call_args[0][0]
        select = LogFileSelectPayload.unpack(written_data[HEADER_SIZE:])
        assert select.filenames == []

    async def test_handle_log_file_list_size_mismatch_selects(self):
        """Same filename but different size → select for transfer."""
        mock_storage = MagicMock()
        mock_storage.get_stored_file_metadata.return_value = {
            "f1.txt": (50, b"\xcc" * 16),  # different size AND md5
        }

        mock_writer = AsyncMock()
        mock_writer.is_closing.return_value = False
        mock_session_mgr = MagicMock()

        server = TCPServer(
            host="0.0.0.0", port=0, session_mgr=mock_session_mgr,
            auth_token="tok", storage_mgr=mock_storage,
        )

        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
        ]
        file_list_payload = LogFileListPayload(entries=entries).pack()
        await server._handle_log_file_list("agent-a", file_list_payload, mock_writer)

        written_data = mock_writer.write.call_args[0][0]
        select = LogFileSelectPayload.unpack(written_data[HEADER_SIZE:])
        assert select.filenames == ["f1.txt"]

    async def test_handle_log_file_list_empty(self):
        """Empty file list → send empty selection."""
        mock_storage = MagicMock()
        mock_storage.get_stored_file_metadata.return_value = {}

        mock_writer = AsyncMock()
        mock_writer.is_closing.return_value = False
        mock_session_mgr = MagicMock()

        server = TCPServer(
            host="0.0.0.0", port=0, session_mgr=mock_session_mgr,
            auth_token="tok", storage_mgr=mock_storage,
        )

        file_list_payload = LogFileListPayload(entries=[]).pack()
        await server._handle_log_file_list("agent-a", file_list_payload, mock_writer)

        written_data = mock_writer.write.call_args[0][0]
        select = LogFileSelectPayload.unpack(written_data[HEADER_SIZE:])
        assert select.filenames == []
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tcp_server.py -v --tb=short -k "file_list"`
Expected: AttributeError — _handle_log_file_list not defined

**Step 3: Implement in `server/core/tcp_server.py`**

Add imports:
```python
from shared.protocol import (
    ...  # existing
    LogFileEntry,
    LogFileListPayload,
    LogFileSelectPayload,
)
```

Add to `_recv_loop` (before the CMD_LOG_ACK handler):
```python
if packet_type == PacketType.LOG_FILE_LIST:
    await self._handle_log_file_list(agent_id, payload, writer)
    continue
```

Add methods:
```python
async def _handle_log_file_list(
    self, agent_id: str, payload: bytes, writer: _WriterLike
) -> None:
    """LOG_FILE_LIST 수신: 서버 저장소와 비교 후 LOG_FILE_SELECT 전송."""
    if self.storage_mgr is None:
        return

    try:
        file_list = LogFileListPayload.unpack(payload)
    except ValueError:
        self._logger.warning("invalid LOG_FILE_LIST payload: agent_id=%s", agent_id)
        return

    # Compare with stored files
    stored = self.storage_mgr.get_stored_file_metadata(agent_id)
    selected: list[str] = []

    for entry in file_list.entries:
        local = stored.get(entry.filename)
        if local is None:
            selected.append(entry.filename)  # new file
        else:
            local_size, local_md5 = local
            if local_size != entry.file_size or local_md5 != entry.md5:
                selected.append(entry.filename)  # changed file

    self._logger.info(
        "file list comparison: agent_id=%s total=%d selected=%d",
        agent_id,
        len(file_list.entries),
        len(selected),
    )

    # Send selection back to agent
    select = LogFileSelectPayload(filenames=selected)
    writer.write(Packet.build(PacketType.LOG_FILE_SELECT, select.pack()))
    await writer.drain()
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tcp_server.py -v --tb=short`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add server/core/tcp_server.py tests/test_tcp_server.py
git commit -m "feat: add TCPServer LOG_FILE_LIST handler with storage comparison"
```

---

### Task 7: Wire LogCmdHandler.handle_file_select to TCPClient + End-to-End Integration Tests

**Files:**
- Modify: `agent/service/win_service.py` (wire on_log_file_select callback)
- Create or modify: `tests/test_integration_features.py` (add selective transfer e2e test)
- Update: any failing existing tests

**Step 1: Write failing integration test**

Add to `tests/test_integration_features.py`:

```python
class TestFeatureA_SelectiveLogTransfer:
    """Feature A v2: Selective 3-step log transfer."""

    async def test_selective_transfer_end_to_end(self, tmp_path: Path) -> None:
        """Full flow: HIST_REQUEST → FILE_LIST → FILE_SELECT → LOG_HIST (selected only)."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "20260217_app.txt").write_bytes(b"app log data")
        (log_dir / "20260217_error.txt").write_bytes(b"error log data")
        (log_dir / "20260217_debug.txt").write_bytes(b"debug log data")

        watcher = LogWatcher(
            watch_dirs=[str(log_dir)],
            extensions=[".txt"],
            on_new_line=AsyncMock(),
        )

        mock_client = AsyncMock()
        handler = LogCmdHandler(
            log_watcher=watcher,
            tcp_client=mock_client,
            history_max_mb=10,
        )

        # Phase 1: Send HIST_REQUEST
        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        # Verify: LOG_FILE_LIST sent with 3 entries
        mock_client.send_log_file_list.assert_called_once()
        entries = mock_client.send_log_file_list.call_args[0][0]
        assert len(entries) == 3

        # Phase 2: Server selects only 2 files
        select = LogFileSelectPayload(
            filenames=["20260217_app.txt", "20260217_debug.txt"]
        ).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        # Verify: Only 2 files sent via LOG_HIST
        assert mock_client.send_log_history.call_count == 2
        sent_filenames = {
            call.args[0] for call in mock_client.send_log_history.call_args_list
        }
        assert sent_filenames == {"20260217_app.txt", "20260217_debug.txt"}

        # ACK sent with correct count
        ack_calls = [
            c for c in mock_client.send_packet.call_args_list
            if c[0][0] == PacketType.CMD_LOG_ACK
        ]
        assert len(ack_calls) == 1
        ack = CmdLogAckPayload.unpack(ack_calls[0][0][1])
        assert ack.file_count == 2

    async def test_selective_transfer_nothing_new(self, tmp_path: Path) -> None:
        """Server selects 0 files → no LOG_HIST sent."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "20260217_app.txt").write_bytes(b"data")

        watcher = LogWatcher(
            watch_dirs=[str(log_dir)],
            extensions=[".txt"],
            on_new_line=AsyncMock(),
        )

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        # Server selects nothing
        select = LogFileSelectPayload(filenames=[]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        mock_client.send_log_history.assert_not_called()
```

**Step 2: Wire in win_service.py**

In `agent/service/win_service.py`, find where `tcp_client.on_cmd_log = log_cmd_handler.handle_cmd_log` is set, and add below it:
```python
tcp_client.on_log_file_select = log_cmd_handler.handle_file_select
```

**Step 3: Update existing integration tests**

Update `test_hist_request_end_to_end` in `TestFeatureA_LogTransmission` to use the new 2-phase flow. The test should now:
1. Send HIST_REQUEST
2. Wait briefly
3. Simulate file_select response
4. Verify selected files were sent

**Step 4: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: ALL PASS (369+ tests, with new tests added)

**Step 5: Commit**

```bash
git add agent/service/win_service.py tests/test_integration_features.py
git commit -m "feat: wire selective transfer and add e2e integration tests"
```

---

### Task 8: Update Server Telegram Log Commands

**Files:**
- Modify: `server/telegram/log_commands.py` (update response message to show selective transfer info)
- Test: `tests/test_log_commands.py`

**Step 1: Update telegram response**

In `server/telegram/log_commands.py`, update the HIST_REQUEST response to inform users about the selective transfer:
- After sending CMD_LOG, note that the agent will first send a file list
- The server will automatically compare and request only new/changed files

This is a minor text change — the actual protocol handling happens in tcp_server.py automatically.

**Step 2: Run tests**

Run: `python -m pytest tests/test_log_commands.py -v --tb=short`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add server/telegram/log_commands.py tests/test_log_commands.py
git commit -m "feat: update telegram log commands for selective transfer feedback"
```

---

## Summary

| Task | Component | Description |
|------|-----------|-------------|
| 1 | shared/protocol.py | LogFileEntry + LogFileListPayload + LogFileSelectPayload |
| 2 | agent/log_watcher.py | get_files_metadata(date_str) with MD5 |
| 3 | server/storage/manager.py | get_stored_file_metadata(agent_id) |
| 4 | agent/tcp_client.py | send_log_file_list + on_log_file_select |
| 5 | agent/log_cmd_handler.py | 2-phase _handle_hist_request + handle_file_select |
| 6 | server/tcp_server.py | _handle_log_file_list + comparison + _send_log_file_select |
| 7 | win_service.py + tests | Wire callbacks + e2e integration tests |
| 8 | telegram/log_commands.py | Update user-facing messages |

**Dependencies:** Task 1 → Task 2,3,4 (can be parallel) → Task 5,6 (can be parallel) → Task 7 → Task 8
