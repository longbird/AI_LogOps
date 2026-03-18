# Dual-Pane File Manager Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Total Commander-style dual-pane file manager to the GUI server for bidirectional file transfer between server and connected agents over the existing TCP protocol.

**Architecture:** Extend the TCP binary protocol with 6 new packet types (0x60-0x65) for directory listing, file upload (server→agent), and file download (agent→server). Reuse existing FILE_CHUNK/FILE_ACK mechanism with single-transfer-per-agent locking. GUI is a new tkinter tab with server (local) and agent (remote) file panels.

**Tech Stack:** Python 3.13, tkinter/ttk, asyncio, existing TCP binary protocol (`shared/protocol.py`)

**Spec:** `docs/superpowers/specs/2026-03-18-file-manager-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `shared/protocol.py` | 6 new PacketType + 6 Payload classes |
| Modify | `agent/core/file_transfer.py` | Refactor `start_receive()` signature |
| Modify | `agent/core/deploy_handler.py` | Adapt to new `start_receive()` signature |
| Create | `agent/core/file_handler.py` | Agent-side file command handler |
| Modify | `agent/core/tcp_client.py` | New callback slots + dispatch in recv_loop |
| Modify | `agent/core/agent_runtime.py` | Wire FileHandler callbacks |
| Modify | `server/core/tcp_server.py` | Server-side send/receive + transfer lock |
| Create | `server/gui/tabs/file_manager_tab.py` | Dual-pane GUI tab |
| Modify | `server/gui/app.py` | Register new tab |

---

## Chunk 1: Protocol + FileTransferReceiver Refactoring

### Task 1: Add 6 PacketType values to protocol

**Files:**
- Modify: `shared/protocol.py:14-42` (PacketType enum)

- [ ] **Step 1: Add PacketType values**

In `shared/protocol.py`, add after `CMD_EXEC_ACK = 0x51` (line 40):

```python
    # ── File Manager ──
    CMD_FILE_LIST = 0x60
    CMD_FILE_LIST_ACK = 0x61
    CMD_FILE_GET = 0x62
    CMD_FILE_GET_ACK = 0x63
    CMD_FILE_PUT = 0x64
    CMD_FILE_PUT_ACK = 0x65
```

- [ ] **Step 2: Update `_TYPE_LABELS`**

Find `_TYPE_LABELS` dict in `Packet` class and add:

```python
        PacketType.CMD_FILE_LIST: "CMD_FILE_LIST",
        PacketType.CMD_FILE_LIST_ACK: "CMD_FILE_LIST_ACK",
        PacketType.CMD_FILE_GET: "CMD_FILE_GET",
        PacketType.CMD_FILE_GET_ACK: "CMD_FILE_GET_ACK",
        PacketType.CMD_FILE_PUT: "CMD_FILE_PUT",
        PacketType.CMD_FILE_PUT_ACK: "CMD_FILE_PUT_ACK",
```

- [ ] **Step 3: Commit**

```bash
git add shared/protocol.py
git commit -m "feat: add 6 file manager packet types to protocol"
```

### Task 2: Add 6 Payload dataclasses

**Files:**
- Modify: `shared/protocol.py` (append after last payload class)

- [ ] **Step 1: Add all 6 payload classes**

Append at end of `shared/protocol.py` before any trailing code:

```python
# ── File Manager Payloads (JSON serialization) ──


@dataclass(slots=True)
class CmdFileListPayload:
    """CMD_FILE_LIST: 서버 → 에이전트. 디렉토리 목록 요청."""

    path: str
    request_id: str

    def pack(self) -> bytes:
        return json.dumps(
            {"path": self.path, "request_id": self.request_id},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdFileListPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(path=str(d["path"]), request_id=str(d["request_id"]))


@dataclass(slots=True)
class CmdFileListAckPayload:
    """CMD_FILE_LIST_ACK: 에이전트 → 서버. 디렉토리 목록 응답."""

    request_id: str
    success: bool
    error: str
    current_path: str
    entries: list[dict[str, Any]]  # [{name, is_dir, size, modified}]
    truncated: bool = False

    def pack(self) -> bytes:
        return json.dumps(
            {
                "request_id": self.request_id,
                "success": self.success,
                "error": self.error,
                "current_path": self.current_path,
                "entries": self.entries,
                "truncated": self.truncated,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdFileListAckPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            request_id=str(d["request_id"]),
            success=bool(d["success"]),
            error=str(d.get("error", "")),
            current_path=str(d.get("current_path", "")),
            entries=list(d.get("entries", [])),
            truncated=bool(d.get("truncated", False)),
        )


@dataclass(slots=True)
class CmdFileGetPayload:
    """CMD_FILE_GET: 서버 → 에이전트. 파일 다운로드 요청."""

    remote_path: str
    request_id: str

    def pack(self) -> bytes:
        return json.dumps(
            {"remote_path": self.remote_path, "request_id": self.request_id},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdFileGetPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            remote_path=str(d["remote_path"]),
            request_id=str(d["request_id"]),
        )


@dataclass(slots=True)
class CmdFileGetAckPayload:
    """CMD_FILE_GET_ACK: 에이전트 → 서버. 파일 메타 + 청크 전송 시작 신호."""

    request_id: str
    success: bool
    error: str
    file_size: int = 0
    sha256: str = ""
    filename: str = ""

    def pack(self) -> bytes:
        return json.dumps(
            {
                "request_id": self.request_id,
                "success": self.success,
                "error": self.error,
                "file_size": self.file_size,
                "sha256": self.sha256,
                "filename": self.filename,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdFileGetAckPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            request_id=str(d["request_id"]),
            success=bool(d["success"]),
            error=str(d.get("error", "")),
            file_size=int(d.get("file_size", 0)),
            sha256=str(d.get("sha256", "")),
            filename=str(d.get("filename", "")),
        )


@dataclass(slots=True)
class CmdFilePutPayload:
    """CMD_FILE_PUT: 서버 → 에이전트. 파일 업로드 시작."""

    remote_path: str
    file_size: int
    sha256: str
    filename: str
    request_id: str

    def pack(self) -> bytes:
        return json.dumps(
            {
                "remote_path": self.remote_path,
                "file_size": self.file_size,
                "sha256": self.sha256,
                "filename": self.filename,
                "request_id": self.request_id,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdFilePutPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            remote_path=str(d["remote_path"]),
            file_size=int(d["file_size"]),
            sha256=str(d["sha256"]),
            filename=str(d["filename"]),
            request_id=str(d["request_id"]),
        )


@dataclass(slots=True)
class CmdFilePutAckPayload:
    """CMD_FILE_PUT_ACK: 에이전트 → 서버. 업로드 완료/실패 응답."""

    request_id: str
    success: bool
    error: str

    def pack(self) -> bytes:
        return json.dumps(
            {
                "request_id": self.request_id,
                "success": self.success,
                "error": self.error,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> CmdFilePutAckPayload:
        d = cast(dict[str, Any], json.loads(data.decode("utf-8")))
        return cls(
            request_id=str(d["request_id"]),
            success=bool(d["success"]),
            error=str(d.get("error", "")),
        )
```

- [ ] **Step 2: Commit**

```bash
git add shared/protocol.py
git commit -m "feat: add 6 file manager payload classes with JSON serialization"
```

### Task 3: Refactor FileTransferReceiver

**Files:**
- Modify: `agent/core/file_transfer.py:20-26`
- Modify: `agent/core/deploy_handler.py:59`

- [ ] **Step 1: Change `start_receive` signature**

In `agent/core/file_transfer.py`, change:

```python
    def start_receive(self, cmd_deploy: CmdDeployPayload) -> None:
        """CMD_DEPLOY 수신 시 호출. 수신 상태 초기화."""
        self._chunks.clear()
        self._expected_size = cmd_deploy.file_size
        self._expected_sha256 = cmd_deploy.sha256
        self._filename = cmd_deploy.filename
        self._is_receiving = True
```

To:

```python
    def start_receive(self, file_size: int, sha256: str, filename: str) -> None:
        """파일 수신 시작. 수신 상태 초기화."""
        self._chunks.clear()
        self._expected_size = file_size
        self._expected_sha256 = sha256
        self._filename = filename
        self._is_receiving = True
```

- [ ] **Step 2: Remove unused import**

In `agent/core/file_transfer.py`, change import line:

```python
from shared.protocol import CmdDeployPayload, FileChunkPayload
```

To:

```python
from shared.protocol import FileChunkPayload
```

- [ ] **Step 3: Update DeployHandler call site**

In `agent/core/deploy_handler.py` line 59, change:

```python
        self.receiver.start_receive(cmd)
```

To:

```python
        self.receiver.start_receive(cmd.file_size, cmd.sha256, cmd.filename)
```

- [ ] **Step 4: Verify existing deploy still works**

Run the server and agent locally if possible, or verify imports work:

```bash
cd D:/Work/AI_Projects/AI-LogOps
python -c "from agent.core.deploy_handler import DeployHandler; print('OK')"
python -c "from agent.core.file_transfer import FileTransferReceiver; print('OK')"
```

Expected: Both print "OK" with no import errors.

- [ ] **Step 5: Commit**

```bash
git add agent/core/file_transfer.py agent/core/deploy_handler.py
git commit -m "refactor: generalize FileTransferReceiver.start_receive signature"
```

---

## Chunk 2: Agent-Side File Handler

### Task 4: Create agent FileHandler

**Files:**
- Create: `agent/core/file_handler.py`

- [ ] **Step 1: Create FileHandler class**

Create `agent/core/file_handler.py`:

```python
from __future__ import annotations

import os
import time
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING

from agent.core.file_transfer import FileTransferReceiver
from shared.protocol import (
    CHUNK_SIZE,
    CmdFileGetAckPayload,
    CmdFileGetPayload,
    CmdFileListAckPayload,
    CmdFileListPayload,
    CmdFilePutAckPayload,
    CmdFilePutPayload,
    FileAckPayload,
    FileChunkPayload,
    PacketType,
)
from shared.utils import compute_sha256, setup_logging

if TYPE_CHECKING:
    from agent.core.config_view import ConfigView
    from agent.core.tcp_client import TCPClient

MAX_DIR_ENTRIES = 5000


class FileHandler:
    """에이전트 측 파일 관리 명령 처리기.

    보안 검증 (경로 정규화, 쓰기 금지, 크기 제한) 후 파일 작업을 수행한다.
    """

    def __init__(self, tcp_client: TCPClient, config: ConfigView) -> None:
        self.tcp_client = tcp_client
        self._logger: Logger = setup_logging("file_handler")
        self._receiver: FileTransferReceiver | None = None
        self._put_request_id: str = ""
        self._put_remote_path: str = ""

        # config에서 설정 읽기
        fm_cfg = config.sub("file_manager")
        self._enabled: bool = fm_cfg.b("enabled", False)
        self._write_deny_paths: list[str] = [
            p.lower().replace("\\", "/") for p in fm_cfg.ls("write_deny_paths", [])
        ]
        self._max_file_size: int = fm_cfg.i("max_file_size_mb", 100) * 1024 * 1024

    def _is_enabled(self) -> tuple[bool, str]:
        """파일 관리 활성화 여부. (enabled, error_msg)."""
        if not self._enabled:
            return False, "파일 관리가 비활성화되어 있습니다"
        return True, ""

    def _validate_write_path(self, path: str) -> tuple[bool, str]:
        """쓰기 경로 검증. Path.resolve()로 symlink 해석 후 deny 목록 확인."""
        try:
            resolved = str(Path(path).resolve()).lower().replace("\\", "/")
        except (OSError, ValueError) as exc:
            return False, f"경로 해석 실패: {exc}"

        for deny in self._write_deny_paths:
            if resolved.startswith(deny):
                return False, f"쓰기 금지 경로: {deny}"
        return True, ""

    async def handle_cmd_file_list(self, payload_data: bytes) -> None:
        """CMD_FILE_LIST: 디렉토리 목록 요청 처리."""
        cmd = CmdFileListPayload.unpack(payload_data)
        self._logger.info("file_list request: path=%s", cmd.path)

        ok, err = self._is_enabled()
        if not ok:
            await self._send_list_ack(cmd.request_id, False, err, "", [])
            return

        try:
            target = Path(cmd.path).resolve()
        except (OSError, ValueError) as exc:
            await self._send_list_ack(cmd.request_id, False, str(exc), "", [])
            return

        if not target.is_dir():
            await self._send_list_ack(
                cmd.request_id, False, "디렉토리가 아니거나 존재하지 않습니다", "", []
            )
            return

        entries: list[dict] = []
        truncated = False
        try:
            with os.scandir(str(target)) as it:
                for entry in it:
                    if len(entries) >= MAX_DIR_ENTRIES:
                        truncated = True
                        break
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if not entry.is_dir() else 0,
                            "modified": stat.st_mtime,
                        })
                    except (PermissionError, OSError):
                        continue  # 접근 불가 항목 건너뛰기
        except PermissionError:
            await self._send_list_ack(
                cmd.request_id, False, "접근 권한이 없습니다", "", []
            )
            return
        except OSError as exc:
            await self._send_list_ack(cmd.request_id, False, str(exc), "", [])
            return

        await self._send_list_ack(
            cmd.request_id, True, "", str(target), entries, truncated
        )

    async def _send_list_ack(
        self,
        request_id: str,
        success: bool,
        error: str,
        current_path: str,
        entries: list[dict],
        truncated: bool = False,
    ) -> None:
        ack = CmdFileListAckPayload(
            request_id=request_id,
            success=success,
            error=error,
            current_path=current_path,
            entries=entries,
            truncated=truncated,
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_LIST_ACK, ack.pack())

    async def handle_cmd_file_get(self, payload_data: bytes) -> None:
        """CMD_FILE_GET: 파일 다운로드 요청. 메타 ACK + FILE_CHUNK 스트리밍."""
        cmd = CmdFileGetPayload.unpack(payload_data)
        self._logger.info("file_get request: path=%s", cmd.remote_path)

        ok, err = self._is_enabled()
        if not ok:
            await self._send_get_ack(cmd.request_id, False, err)
            return

        try:
            file_path = Path(cmd.remote_path).resolve()
        except (OSError, ValueError) as exc:
            await self._send_get_ack(cmd.request_id, False, str(exc))
            return

        if not file_path.is_file():
            await self._send_get_ack(
                cmd.request_id, False, "파일이 존재하지 않습니다"
            )
            return

        file_size = file_path.stat().st_size
        if file_size > self._max_file_size:
            max_mb = self._max_file_size // (1024 * 1024)
            await self._send_get_ack(
                cmd.request_id, False, f"파일 크기 제한 초과 ({max_mb}MB)"
            )
            return

        sha256 = compute_sha256(str(file_path))

        # 메타 ACK 전송
        ack = CmdFileGetAckPayload(
            request_id=cmd.request_id,
            success=True,
            error="",
            file_size=file_size,
            sha256=sha256,
            filename=file_path.name,
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_GET_ACK, ack.pack())

        # FILE_CHUNK 스트리밍
        data = file_path.read_bytes()
        seq = 0
        for offset in range(0, len(data), CHUNK_SIZE):
            chunk_data = data[offset : offset + CHUNK_SIZE]
            chunk = FileChunkPayload(seq_num=seq, data=chunk_data)
            await self.tcp_client.send_packet(PacketType.FILE_CHUNK, chunk.pack())
            seq += 1
        self._logger.info(
            "file_get complete: %s (%d bytes, %d chunks)", file_path.name, file_size, seq
        )

    async def _send_get_ack(
        self, request_id: str, success: bool, error: str
    ) -> None:
        ack = CmdFileGetAckPayload(
            request_id=request_id, success=success, error=error
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_GET_ACK, ack.pack())

    async def handle_cmd_file_put(self, payload_data: bytes) -> None:
        """CMD_FILE_PUT: 파일 업로드 시작. FileTransferReceiver 초기화."""
        cmd = CmdFilePutPayload.unpack(payload_data)
        self._logger.info(
            "file_put request: path=%s size=%d", cmd.remote_path, cmd.file_size
        )

        ok, err = self._is_enabled()
        if not ok:
            await self._send_put_ack(cmd.request_id, False, err)
            return

        # 쓰기 경로 검증
        valid, msg = self._validate_write_path(cmd.remote_path)
        if not valid:
            await self._send_put_ack(cmd.request_id, False, msg)
            return

        # 크기 제한 검증
        if cmd.file_size > self._max_file_size:
            max_mb = self._max_file_size // (1024 * 1024)
            await self._send_put_ack(
                cmd.request_id, False, f"파일 크기 제한 초과 ({max_mb}MB)"
            )
            return

        # 수신 준비
        dest = Path(cmd.remote_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        self._receiver = FileTransferReceiver(target_dir=str(dest.parent))
        self._receiver.start_receive(cmd.file_size, cmd.sha256, cmd.filename)
        self._put_request_id = cmd.request_id
        self._put_remote_path = cmd.remote_path
        self._logger.info("file_put receiver initialized: %s", dest)

    async def handle_file_chunk_for_put(self, payload_data: bytes) -> None:
        """FILE_CHUNK for file_put: 청크 수신 + ACK. 완료 시 PUT_ACK."""
        if self._receiver is None:
            return

        chunk = FileChunkPayload.unpack(payload_data)
        success = self._receiver.receive_chunk(chunk)
        ack = FileAckPayload(seq_num=chunk.seq_num, status=0 if success else 1)
        await self.tcp_client.send_packet(PacketType.FILE_ACK, ack.pack())

        if self._receiver.is_complete():
            file_path = self._receiver.assemble()
            if file_path is not None:
                # 수신 성공 — 최종 경로로 이동 (필요 시)
                dest = Path(self._put_remote_path).resolve()
                if file_path.resolve() != dest:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.move(str(file_path), str(dest))
                self._logger.info("file_put complete: %s", dest)
                await self._send_put_ack(self._put_request_id, True, "")
            else:
                self._logger.error("file_put SHA-256 mismatch")
                await self._send_put_ack(
                    self._put_request_id, False, "파일 무결성 검증 실패 (SHA-256)"
                )
            self._receiver = None

    async def _send_put_ack(
        self, request_id: str, success: bool, error: str
    ) -> None:
        ack = CmdFilePutAckPayload(
            request_id=request_id, success=success, error=error
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_PUT_ACK, ack.pack())

    @property
    def is_receiving_file(self) -> bool:
        """파일 수신 진행 중 여부."""
        return self._receiver is not None
```

- [ ] **Step 2: Commit**

```bash
git add agent/core/file_handler.py
git commit -m "feat: add agent-side FileHandler for file management commands"
```

### Task 5: Wire FileHandler into TCPClient and AgentRuntime

**Files:**
- Modify: `agent/core/tcp_client.py:57-68` (callback slots)
- Modify: `agent/core/tcp_client.py:248-290` (recv_loop dispatch)
- Modify: `agent/core/agent_runtime.py` (callback wiring)

- [ ] **Step 1: Add callback slots to TCPClient**

In `agent/core/tcp_client.py`, after line 68 (`self.on_cmd_exec`), add:

```python
        self.on_cmd_file_list: PacketCallback | None = None
        self.on_cmd_file_get: PacketCallback | None = None
        self.on_cmd_file_put: PacketCallback | None = None
```

- [ ] **Step 2: Add dispatch in `_recv_loop`**

In `agent/core/tcp_client.py`, in the `_recv_loop` method, add these cases before the final `self._logger.debug("unknown packet")` line (find the end of the dispatch chain):

```python
                if packet_type == PacketType.CMD_FILE_LIST:
                    if self.on_cmd_file_list is not None:
                        await self.on_cmd_file_list(payload)
                    continue
                if packet_type == PacketType.CMD_FILE_GET:
                    if self.on_cmd_file_get is not None:
                        await self.on_cmd_file_get(payload)
                    continue
                if packet_type == PacketType.CMD_FILE_PUT:
                    if self.on_cmd_file_put is not None:
                        await self.on_cmd_file_put(payload)
                    continue
```

- [ ] **Step 3: Add PacketType imports to tcp_client.py**

Add `PacketType.CMD_FILE_LIST`, `CMD_FILE_GET`, `CMD_FILE_PUT` — these are already available through the existing `PacketType` import, so no import changes needed.

- [ ] **Step 4: Wire FileHandler in AgentRuntime**

In `agent/core/agent_runtime.py`, find `_create_server_connection()` method. Add FileHandler creation and callback wiring. Look for where `DeployHandler` is created and follow the same pattern:

```python
        # FileHandler 생성
        from agent.core.file_handler import FileHandler
        file_handler = FileHandler(tcp_client=tcp_client, config=self._config)

        # 콜백 연결
        tcp_client.on_cmd_file_list = file_handler.handle_cmd_file_list
        tcp_client.on_cmd_file_get = file_handler.handle_cmd_file_get
        tcp_client.on_cmd_file_put = file_handler.handle_cmd_file_put
```

- [ ] **Step 5: Handle FILE_CHUNK routing between deploy and file_put**

In `agent/core/agent_runtime.py`, the `on_file_chunk` callback currently goes to `deploy_handler.handle_file_chunk`. We need a router that dispatches to the correct handler:

Find where `tcp_client.on_file_chunk = deploy_handler.handle_file_chunk` is set. Replace with a routing function:

```python
        async def _route_file_chunk(payload_data: bytes) -> None:
            """FILE_CHUNK를 활성 전송 컨텍스트로 라우팅."""
            if file_handler.is_receiving_file:
                await file_handler.handle_file_chunk_for_put(payload_data)
            else:
                await deploy_handler.handle_file_chunk(payload_data)

        tcp_client.on_file_chunk = _route_file_chunk
```

- [ ] **Step 6: Verify imports**

```bash
cd D:/Work/AI_Projects/AI-LogOps
python -c "from agent.core.file_handler import FileHandler; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add agent/core/tcp_client.py agent/core/agent_runtime.py
git commit -m "feat: wire FileHandler into agent TCP client and runtime"
```

---

## Chunk 3: Server-Side TCP Extension

### Task 6: Add file manager methods to TCPServer

**Files:**
- Modify: `server/core/tcp_server.py`

- [ ] **Step 1: Add imports**

In `server/core/tcp_server.py`, add to the imports from `shared.protocol`:

```python
    CmdFileListPayload,
    CmdFileListAckPayload,
    CmdFileGetPayload,
    CmdFileGetAckPayload,
    CmdFilePutPayload,
    CmdFilePutAckPayload,
```

- [ ] **Step 2: Add instance variables to `__init__`**

After the existing `_exec_futures` line (~line 86), add:

```python
        # ── File Manager ──
        self._file_list_futures: dict[str, asyncio.Future] = {}
        self._file_get_futures: dict[str, asyncio.Future] = {}
        self._file_put_futures: dict[str, asyncio.Future] = {}
        self._file_receivers: dict[str, FileTransferReceiver] = {}
        self._file_get_save_paths: dict[str, Path] = {}
        self._file_transfer_locks: dict[str, asyncio.Lock] = {}
```

Also add import at top of file:

```python
import shutil
import tempfile
import uuid
```

And add:

```python
from agent.core.file_transfer import FileTransferReceiver
```

- [ ] **Step 3: Add `_get_transfer_lock` helper**

```python
    def _get_transfer_lock(self, agent_id: str) -> asyncio.Lock:
        """에이전트별 파일 전송 잠금 반환 (lazy 생성)."""
        if agent_id not in self._file_transfer_locks:
            self._file_transfer_locks[agent_id] = asyncio.Lock()
        return self._file_transfer_locks[agent_id]
```

- [ ] **Step 4: Add `send_file_list` method**

```python
    async def send_file_list(self, agent_id: str, path: str) -> dict:
        """에이전트의 디렉토리 목록 요청. 결과를 dict로 반환."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return {"success": False, "error": "에이전트가 연결되어 있지 않습니다"}

        writer = cast(_WriterLike, session.writer)
        request_id = str(uuid.uuid4())[:8]
        loop = asyncio.get_running_loop()
        self._file_list_futures[request_id] = loop.create_future()

        cmd = CmdFileListPayload(path=path, request_id=request_id)
        writer.write(Packet.build(PacketType.CMD_FILE_LIST, cmd.pack()))
        await writer.drain()

        try:
            result = await asyncio.wait_for(
                self._file_list_futures[request_id], timeout=30
            )
            return result
        except asyncio.TimeoutError:
            return {"success": False, "error": "응답 시간 초과 (30초)"}
        finally:
            self._file_list_futures.pop(request_id, None)
```

- [ ] **Step 5: Add `send_file_get` method (agent→server download)**

```python
    async def send_file_get(
        self, agent_id: str, remote_path: str, save_path: str
    ) -> dict:
        """에이전트 파일을 서버로 다운로드."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return {"success": False, "error": "에이전트가 연결되어 있지 않습니다"}

        lock = self._get_transfer_lock(agent_id)
        if lock.locked():
            return {"success": False, "error": "다른 전송이 진행 중입니다"}

        async with lock:
            writer = cast(_WriterLike, session.writer)
            request_id = str(uuid.uuid4())[:8]
            loop = asyncio.get_running_loop()
            self._file_get_futures[request_id] = loop.create_future()
            self._file_get_save_paths[request_id] = Path(save_path)

            cmd = CmdFileGetPayload(remote_path=remote_path, request_id=request_id)
            writer.write(Packet.build(PacketType.CMD_FILE_GET, cmd.pack()))
            await writer.drain()

            try:
                result = await asyncio.wait_for(
                    self._file_get_futures[request_id], timeout=120
                )
                return result
            except asyncio.TimeoutError:
                # 부분 수신 정리
                self._file_receivers.pop(agent_id, None)
                return {"success": False, "error": "전송 시간 초과 (120초)"}
            finally:
                self._file_get_futures.pop(request_id, None)
                self._file_get_save_paths.pop(request_id, None)
```

- [ ] **Step 6: Add `send_file_put` method (server→agent upload)**

```python
    async def send_file_put(
        self,
        agent_id: str,
        local_path: str,
        remote_path: str,
        progress_callback: Any = None,
    ) -> dict:
        """서버 파일을 에이전트로 업로드."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return {"success": False, "error": "에이전트가 연결되어 있지 않습니다"}

        lock = self._get_transfer_lock(agent_id)
        if lock.locked():
            return {"success": False, "error": "다른 전송이 진행 중입니다"}

        path = Path(local_path)
        if not path.exists():
            return {"success": False, "error": f"파일이 존재하지 않습니다: {local_path}"}

        async with lock:
            writer = cast(_WriterLike, session.writer)
            data = path.read_bytes()
            sha256 = compute_sha256(local_path)
            request_id = str(uuid.uuid4())[:8]

            loop = asyncio.get_running_loop()
            self._file_put_futures[request_id] = loop.create_future()

            cmd = CmdFilePutPayload(
                remote_path=remote_path,
                file_size=len(data),
                sha256=sha256,
                filename=path.name,
                request_id=request_id,
            )
            writer.write(Packet.build(PacketType.CMD_FILE_PUT, cmd.pack()))
            await writer.drain()

            # FILE_CHUNK 전송
            chunk_size = CHUNK_SIZE
            total_chunks = (len(data) + chunk_size - 1) // chunk_size
            seq = 0
            for offset in range(0, len(data), chunk_size):
                chunk_data = data[offset : offset + chunk_size]
                chunk = FileChunkPayload(seq_num=seq, data=chunk_data)
                writer.write(Packet.build(PacketType.FILE_CHUNK, chunk.pack()))
                seq += 1
                if seq % 8 == 0:
                    await writer.drain()
                    if progress_callback is not None:
                        progress_callback(seq, total_chunks)
            await writer.drain()
            if progress_callback is not None:
                progress_callback(total_chunks, total_chunks)

            try:
                result = await asyncio.wait_for(
                    self._file_put_futures[request_id], timeout=120
                )
                return result
            except asyncio.TimeoutError:
                return {"success": False, "error": "전송 시간 초과 (120초)"}
            finally:
                self._file_put_futures.pop(request_id, None)
```

- [ ] **Step 7: Add packet handlers in `_recv_loop`**

In the server's `_recv_loop` method, add these dispatch cases (before the final unknown-packet log):

```python
            if packet_type == PacketType.CMD_FILE_LIST_ACK:
                self._handle_file_list_ack(agent_id, payload)
                continue

            if packet_type == PacketType.CMD_FILE_GET_ACK:
                self._handle_file_get_ack(agent_id, payload)
                continue

            if packet_type == PacketType.CMD_FILE_PUT_ACK:
                self._handle_file_put_ack(agent_id, payload)
                continue
```

Also update the existing `FILE_ACK` handler to also route to file_get receiver:

Find the `FILE_ACK` handling section. The current block should remain (for deploy). But `FILE_CHUNK` from agent (for file_get) needs handling. Add a new case:

```python
            if packet_type == PacketType.FILE_CHUNK:
                # 에이전트→서버 파일 전송 (CMD_FILE_GET 응답)
                self._handle_file_chunk_from_agent(agent_id, payload)
                continue
```

- [ ] **Step 8: Add handler methods**

```python
    def _handle_file_list_ack(self, agent_id: str, payload: bytes) -> None:
        ack = CmdFileListAckPayload.unpack(payload)
        future = self._file_list_futures.get(ack.request_id)
        if future is not None and not future.done():
            future.set_result({
                "success": ack.success,
                "error": ack.error,
                "current_path": ack.current_path,
                "entries": ack.entries,
                "truncated": ack.truncated,
            })

    def _handle_file_get_ack(self, agent_id: str, payload: bytes) -> None:
        ack = CmdFileGetAckPayload.unpack(payload)
        future = self._file_get_futures.get(ack.request_id)
        if not ack.success:
            if future is not None and not future.done():
                future.set_result({"success": False, "error": ack.error})
            return
        # 서버 측 수신 준비
        temp_dir = tempfile.mkdtemp(prefix="ailogops_filemgr_")
        receiver = FileTransferReceiver(target_dir=temp_dir)
        receiver.start_receive(ack.file_size, ack.sha256, ack.filename)
        self._file_receivers[agent_id] = receiver

    def _handle_file_chunk_from_agent(self, agent_id: str, payload: bytes) -> None:
        receiver = self._file_receivers.get(agent_id)
        if receiver is None:
            return
        chunk = FileChunkPayload.unpack(payload)
        receiver.receive_chunk(chunk)
        if receiver.is_complete():
            temp_path = receiver.assemble()
            # 어떤 request_id의 future인지 찾기
            request_id = None
            for rid, save_path in self._file_get_save_paths.items():
                if rid in self._file_get_futures:
                    request_id = rid
                    break
            future = self._file_get_futures.get(request_id) if request_id else None
            if temp_path is not None:
                save_path = self._file_get_save_paths.get(request_id)
                if save_path is not None:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(temp_path), str(save_path))
                    self._logger.info("file_get saved: %s", save_path)
                if future is not None and not future.done():
                    future.set_result({"success": True, "path": str(save_path)})
            else:
                if future is not None and not future.done():
                    future.set_result({"success": False, "error": "SHA-256 불일치"})
            del self._file_receivers[agent_id]
            # temp_dir cleanup
            if temp_path is not None:
                temp_dir = temp_path.parent
                if temp_dir.exists() and str(temp_dir).startswith(tempfile.gettempdir()):
                    shutil.rmtree(str(temp_dir), ignore_errors=True)

    def _handle_file_put_ack(self, agent_id: str, payload: bytes) -> None:
        ack = CmdFilePutAckPayload.unpack(payload)
        future = self._file_put_futures.get(ack.request_id)
        if future is not None and not future.done():
            future.set_result({
                "success": ack.success,
                "error": ack.error,
            })
```

- [ ] **Step 9: Verify server imports**

```bash
cd D:/Work/AI_Projects/AI-LogOps
python -c "from server.core.tcp_server import TCPServer; print('OK')"
```

Expected: `OK`

- [ ] **Step 10: Commit**

```bash
git add server/core/tcp_server.py
git commit -m "feat: add file manager send/receive methods to TCPServer"
```

---

## Chunk 4: GUI Tab + Registration

### Task 7: Create file manager GUI tab

**Files:**
- Create: `server/gui/tabs/file_manager_tab.py`

- [ ] **Step 1: Create the full GUI tab**

Create `server/gui/tabs/file_manager_tab.py`:

```python
"""파일 관리 탭 — 듀얼 패널 파일 매니저.

서버(로컬) ↔ 에이전트(원격) 양방향 파일 전송.
"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

from server.gui.constants import (
    BG_BTN,
    BG_BTN_PRIMARY,
    BG_DARK,
    BG_FRAME,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_FAMILY,
    FONT_NORMAL,
    FONT_SMALL,
)

if TYPE_CHECKING:
    from server.gui.constants import ServerAppLike


def _human_size(size: int) -> str:
    """바이트를 사람이 읽기 좋은 크기로 변환."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _format_time(ts: float) -> str:
    """Unix timestamp → YYYY-MM-DD HH:MM."""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return ""


class FileManagerTab(tk.Frame):
    """듀얼 패널 파일 매니저 탭."""

    def __init__(self, parent: tk.Widget, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._server_path: str = str(Path.cwd())
        self._agent_path: str = "C:/"
        self._transferring: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        # ── 상단: 에이전트 선택 ──
        top = tk.Frame(self, bg=BG_FRAME, height=36)
        top.pack(fill=tk.X, padx=4, pady=(4, 0))

        tk.Label(
            top, text="에이전트:", bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(side=tk.LEFT, padx=(8, 4))

        self._agent_var = tk.StringVar(value="")
        self._agent_combo = ttk.Combobox(
            top,
            textvariable=self._agent_var,
            state="readonly",
            width=20,
            font=FONT_NORMAL,
        )
        self._agent_combo.pack(side=tk.LEFT, padx=4)
        self._agent_combo.bind("<<ComboboxSelected>>", self._on_agent_selected)

        tk.Button(
            top,
            text="새로고침",
            bg=BG_BTN,
            fg=FG_TEXT,
            font=FONT_SMALL,
            relief=tk.FLAT,
            command=self._refresh_agent_list,
        ).pack(side=tk.LEFT, padx=4)

        # ── 중앙: 듀얼 패널 ──
        mid = tk.Frame(self, bg=BG_DARK)
        mid.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 왼쪽: 서버 패널
        left = tk.Frame(mid, bg=BG_FRAME)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 2))
        self._build_panel(left, "서버 (로컬)", is_server=True)

        # 오른쪽: 에이전트 패널
        right = tk.Frame(mid, bg=BG_FRAME)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(2, 0))
        self._build_panel(right, "에이전트 (원격)", is_server=False)

        # ── 하단: 전송 버튼 + 진행률 ──
        bot = tk.Frame(self, bg=BG_FRAME, height=80)
        bot.pack(fill=tk.X, padx=4, pady=(0, 4))
        bot.pack_propagate(False)

        btn_frame = tk.Frame(bot, bg=BG_FRAME)
        btn_frame.pack(pady=4)

        self._btn_to_agent = tk.Button(
            btn_frame,
            text="→ 에이전트로 복사",
            bg=BG_BTN_PRIMARY,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            command=self._copy_to_agent,
        )
        self._btn_to_agent.pack(side=tk.LEFT, padx=8)

        self._btn_to_server = tk.Button(
            btn_frame,
            text="← 서버로 복사",
            bg=BG_BTN_PRIMARY,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            command=self._copy_to_server,
        )
        self._btn_to_server.pack(side=tk.LEFT, padx=8)

        self._progress = ttk.Progressbar(bot, mode="determinate", length=400)
        self._progress.pack(pady=2, padx=8, fill=tk.X)

        self._status_var = tk.StringVar(value="대기 중")
        tk.Label(
            bot,
            textvariable=self._status_var,
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_SMALL,
        ).pack(padx=8, anchor=tk.W)

    def _build_panel(
        self, parent: tk.Frame, title: str, *, is_server: bool
    ) -> None:
        """파일 패널 (경로 + Treeview) 구축."""
        tk.Label(
            parent, text=title, bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(padx=8, pady=(4, 0), anchor=tk.W)

        # 경로 입력
        path_frame = tk.Frame(parent, bg=BG_FRAME)
        path_frame.pack(fill=tk.X, padx=4, pady=2)

        path_var = tk.StringVar(
            value=self._server_path if is_server else self._agent_path
        )
        entry = tk.Entry(path_frame, textvariable=path_var, font=FONT_SMALL)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        entry.bind("<Return>", lambda e: self._navigate(is_server, path_var.get()))

        tk.Button(
            path_frame,
            text="↑",
            bg=BG_BTN,
            fg=FG_TEXT,
            font=FONT_SMALL,
            width=3,
            relief=tk.FLAT,
            command=lambda: self._go_up(is_server),
        ).pack(side=tk.LEFT, padx=1)

        tk.Button(
            path_frame,
            text="⟳",
            bg=BG_BTN,
            fg=FG_TEXT,
            font=FONT_SMALL,
            width=3,
            relief=tk.FLAT,
            command=lambda: self._refresh(is_server),
        ).pack(side=tk.LEFT, padx=1)

        # Treeview
        tree_frame = tk.Frame(parent, bg=BG_FRAME)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        tree = ttk.Treeview(
            tree_frame,
            columns=("size", "modified"),
            show="headings",
            selectmode="extended",
        )
        tree.heading("size", text="크기", anchor=tk.E)
        tree.heading("modified", text="수정일", anchor=tk.W)

        # 이름 컬럼은 tree column (#0)이 아닌 첫 번째 컬럼으로
        tree["columns"] = ("name", "size", "modified")
        tree.heading("name", text="이름", anchor=tk.W)
        tree.column("name", width=200, minwidth=100)
        tree.column("size", width=80, minwidth=60, anchor=tk.E)
        tree.column("modified", width=120, minwidth=80)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tree.bind("<Double-1>", lambda e: self._on_double_click(is_server))

        if is_server:
            self._server_tree = tree
            self._server_path_var = path_var
            self._load_server_dir(self._server_path)
        else:
            self._agent_tree = tree
            self._agent_path_var = path_var

    # ── 탐색 ──

    def _navigate(self, is_server: bool, path: str) -> None:
        if is_server:
            self._server_path = path
            self._server_path_var.set(path)
            self._load_server_dir(path)
        else:
            self._agent_path = path
            self._agent_path_var.set(path)
            self._load_agent_dir(path)

    def _go_up(self, is_server: bool) -> None:
        if is_server:
            parent = str(Path(self._server_path).parent)
            self._navigate(True, parent)
        else:
            parent = str(Path(self._agent_path).parent)
            self._navigate(False, parent)

    def _refresh(self, is_server: bool) -> None:
        if is_server:
            self._load_server_dir(self._server_path)
        else:
            self._load_agent_dir(self._agent_path)

    def _on_double_click(self, is_server: bool) -> None:
        tree = self._server_tree if is_server else self._agent_tree
        selected = tree.selection()
        if not selected:
            return
        item = tree.item(selected[0])
        name = item["values"][0]
        size_str = str(item["values"][1])

        if size_str == "<DIR>":
            if is_server:
                new_path = str(Path(self._server_path) / name)
                self._navigate(True, new_path)
            else:
                # Windows 경로 처리
                if self._agent_path.endswith("/") or self._agent_path.endswith("\\"):
                    new_path = self._agent_path + name
                else:
                    new_path = self._agent_path + "/" + name
                self._navigate(False, new_path)

    def _on_agent_selected(self, event: Any = None) -> None:
        self._load_agent_dir(self._agent_path)

    # ── 서버 로컬 탐색 ──

    def _load_server_dir(self, path: str) -> None:
        tree = self._server_tree
        tree.delete(*tree.get_children())
        self._server_path = path
        self._server_path_var.set(path)

        try:
            entries = []
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if not entry.is_dir() else 0,
                            "modified": stat.st_mtime,
                        })
                    except (PermissionError, OSError):
                        continue

            # 디렉토리 먼저, 이름순 정렬
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

            for e in entries:
                size = "<DIR>" if e["is_dir"] else _human_size(e["size"])
                mod = _format_time(e["modified"])
                tree.insert("", tk.END, values=(e["name"], size, mod))

        except (PermissionError, OSError) as exc:
            self._status_var.set(f"오류: {exc}")

    # ── 에이전트 원격 탐색 ──

    def _load_agent_dir(self, path: str) -> None:
        agent_id = self._agent_var.get()
        if not agent_id:
            self._status_var.set("에이전트를 선택하세요")
            return

        self._agent_tree.delete(*self._agent_tree.get_children())
        self._agent_path = path
        self._agent_path_var.set(path)
        self._status_var.set("디렉토리 로딩 중...")

        def _do() -> None:
            try:
                result = self._app.api_get(
                    f"/api/files/agent/{agent_id}/list?path={path}"
                )
                self._agent_tree.after(0, self._populate_agent_tree, result)
            except Exception as exc:
                self._agent_tree.after(
                    0, self._status_var.set, f"오류: {exc}"
                )

        threading.Thread(target=_do, daemon=True).start()

    def _populate_agent_tree(self, result: Any) -> None:
        tree = self._agent_tree
        tree.delete(*tree.get_children())

        if isinstance(result, dict):
            if not result.get("success", False):
                self._status_var.set(f"오류: {result.get('error', '알 수 없는 오류')}")
                return
            entries = result.get("entries", [])
            current_path = result.get("current_path", self._agent_path)
            self._agent_path = current_path
            self._agent_path_var.set(current_path)
        else:
            self._status_var.set("잘못된 응답 형식")
            return

        entries.sort(key=lambda e: (not e.get("is_dir", False), e.get("name", "").lower()))

        for e in entries:
            is_dir = e.get("is_dir", False)
            size = "<DIR>" if is_dir else _human_size(e.get("size", 0))
            mod = _format_time(e.get("modified", 0))
            tree.insert("", tk.END, values=(e["name"], size, mod))

        truncated = result.get("truncated", False)
        count = len(entries)
        msg = f"{count}개 항목"
        if truncated:
            msg += " (일부만 표시)"
        self._status_var.set(msg)

    # ── 파일 전송 ──

    def _get_selected_name(self, is_server: bool) -> str | None:
        tree = self._server_tree if is_server else self._agent_tree
        selected = tree.selection()
        if not selected:
            return None
        item = tree.item(selected[0])
        name = item["values"][0]
        size_str = str(item["values"][1])
        if size_str == "<DIR>":
            messagebox.showwarning("파일 관리", "디렉토리는 전송할 수 없습니다")
            return None
        return str(name)

    def _copy_to_agent(self) -> None:
        """서버 → 에이전트 파일 복사."""
        name = self._get_selected_name(is_server=True)
        if name is None:
            return
        agent_id = self._agent_var.get()
        if not agent_id:
            messagebox.showwarning("파일 관리", "에이전트를 선택하세요")
            return

        local_path = str(Path(self._server_path) / name)
        # 에이전트 경로: 현재 에이전트 디렉토리 + 파일명
        if self._agent_path.endswith("/") or self._agent_path.endswith("\\"):
            remote_path = self._agent_path + name
        else:
            remote_path = self._agent_path + "/" + name

        self._do_transfer("to_agent", agent_id, local_path, remote_path, name)

    def _copy_to_server(self) -> None:
        """에이전트 → 서버 파일 복사."""
        name = self._get_selected_name(is_server=False)
        if name is None:
            return
        agent_id = self._agent_var.get()
        if not agent_id:
            messagebox.showwarning("파일 관리", "에이전트를 선택하세요")
            return

        if self._agent_path.endswith("/") or self._agent_path.endswith("\\"):
            remote_path = self._agent_path + name
        else:
            remote_path = self._agent_path + "/" + name
        local_path = str(Path(self._server_path) / name)

        self._do_transfer("to_server", agent_id, local_path, remote_path, name)

    def _do_transfer(
        self,
        direction: str,
        agent_id: str,
        local_path: str,
        remote_path: str,
        filename: str,
    ) -> None:
        if self._transferring:
            messagebox.showwarning("파일 관리", "전송이 진행 중입니다")
            return

        self._transferring = True
        self._progress["value"] = 0
        self._status_var.set(f"전송 중: {filename}")
        self._btn_to_agent.configure(state=tk.DISABLED)
        self._btn_to_server.configure(state=tk.DISABLED)

        def _do() -> None:
            try:
                import json
                body = json.dumps({
                    "direction": direction,
                    "agent_id": agent_id,
                    "local_path": local_path,
                    "remote_path": remote_path,
                })
                result = self._app.api_post("/api/files/transfer", data=body)
                success = isinstance(result, dict) and result.get("success", False)
                error = result.get("error", "") if isinstance(result, dict) else str(result)

                def _done() -> None:
                    self._transferring = False
                    self._btn_to_agent.configure(state=tk.NORMAL)
                    self._btn_to_server.configure(state=tk.NORMAL)
                    if success:
                        self._progress["value"] = 100
                        self._status_var.set(f"전송 완료: {filename}")
                        # 양쪽 새로고침
                        self._load_server_dir(self._server_path)
                        if agent_id:
                            self._load_agent_dir(self._agent_path)
                    else:
                        self._progress["value"] = 0
                        self._status_var.set(f"전송 실패: {error}")

                self._progress.after(0, _done)
            except Exception as exc:
                def _err() -> None:
                    self._transferring = False
                    self._btn_to_agent.configure(state=tk.NORMAL)
                    self._btn_to_server.configure(state=tk.NORMAL)
                    self._status_var.set(f"전송 오류: {exc}")
                self._progress.after(0, _err)

        threading.Thread(target=_do, daemon=True).start()

    # ── 유틸리티 ──

    def _refresh_agent_list(self) -> None:
        try:
            agent_ids = self._app.get_agent_ids()
            self._agent_combo["values"] = agent_ids
            if agent_ids and not self._agent_var.get():
                self._agent_var.set(agent_ids[0])
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add server/gui/tabs/file_manager_tab.py
git commit -m "feat: add dual-pane FileManagerTab GUI"
```

### Task 8: Register tab in ServerGUI + add file transfer API

**Files:**
- Modify: `server/gui/app.py:167-186`
- Modify: `server/dashboard/routes/` (new file_api.py or inline)

- [ ] **Step 1: Register FileManagerTab in app.py**

In `server/gui/app.py`, after line 172 (`from server.gui.tabs.admin_tab import AdminTab`), add:

```python
        from server.gui.tabs.file_manager_tab import FileManagerTab
```

After line 176 (`self._admin_tab = AdminTab(self._notebook, self)`), add:

```python
        self._file_manager_tab = FileManagerTab(self._notebook, self)
```

After line 186 (`self._notebook.add(self._admin_tab, text=" 계정 관리 ")`), add:

```python
        self._notebook.add(self._file_manager_tab, text=" 파일 관리 ")
```

- [ ] **Step 2: Add file transfer API endpoints**

Create `server/dashboard/routes/file_api.py`:

```python
"""파일 관리 API — 에이전트 디렉토리 탐색 + 양방향 파일 전송."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from server.core.tcp_server import TCPServer


def register_file_api(app: any, tcp_server_getter: any) -> None:
    """FastAPI 앱에 파일 관리 라우트 등록."""

    @app.get("/api/files/server/list")
    async def list_server_files(request: Request) -> JSONResponse:
        path = request.query_params.get("path", ".")
        try:
            target = Path(path).resolve()
            if not target.is_dir():
                return JSONResponse({"success": False, "error": "디렉토리가 아닙니다"})
            entries = []
            with os.scandir(str(target)) as it:
                for entry in it:
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if not entry.is_dir() else 0,
                            "modified": stat.st_mtime,
                        })
                    except (PermissionError, OSError):
                        continue
            return JSONResponse({
                "success": True,
                "current_path": str(target),
                "entries": entries,
            })
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)})

    @app.get("/api/files/agent/{agent_id}/list")
    async def list_agent_files(request: Request, agent_id: str) -> JSONResponse:
        path = request.query_params.get("path", "C:/")
        tcp = tcp_server_getter()
        if tcp is None:
            return JSONResponse({"success": False, "error": "서버가 실행 중이 아닙니다"})
        result = await tcp.send_file_list(agent_id, path)
        return JSONResponse(result)

    @app.post("/api/files/transfer")
    async def transfer_file(request: Request) -> JSONResponse:
        tcp = tcp_server_getter()
        if tcp is None:
            return JSONResponse({"success": False, "error": "서버가 실행 중이 아닙니다"})
        body = await request.json()
        direction = body.get("direction")
        agent_id = body.get("agent_id")
        local_path = body.get("local_path")
        remote_path = body.get("remote_path")

        if direction == "to_agent":
            result = await tcp.send_file_put(agent_id, local_path, remote_path)
        elif direction == "to_server":
            result = await tcp.send_file_get(agent_id, remote_path, local_path)
        else:
            result = {"success": False, "error": f"잘못된 direction: {direction}"}
        return JSONResponse(result)
```

- [ ] **Step 3: Register the API in dashboard app**

Find `server/dashboard/app.py` and add the route registration. Look for where other routes are registered (e.g., `deploy_api`), follow the same pattern:

```python
from server.dashboard.routes.file_api import register_file_api

# Inside setup function, after other route registrations:
register_file_api(app, tcp_server_getter)
```

(The exact location depends on how the dashboard app is structured — follow existing route registration patterns.)

- [ ] **Step 4: Verify GUI launches**

```bash
cd D:/Work/AI_Projects/AI-LogOps
python -c "from server.gui.tabs.file_manager_tab import FileManagerTab; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add server/gui/app.py server/gui/tabs/file_manager_tab.py server/dashboard/routes/file_api.py
git commit -m "feat: register FileManagerTab + add file transfer API endpoints"
```

---

## Chunk 5: Integration Testing

### Task 9: Manual integration test

- [ ] **Step 1: Update agent config.yaml for testing**

Ensure the test agent's `config.yaml` includes:

```yaml
file_manager:
  enabled: true
  write_deny_paths:
    - "C:/Windows"
    - "C:/Program Files"
    - "C:/Program Files (x86)"
  max_file_size_mb: 100
```

- [ ] **Step 2: Start server and connect agent**

1. Run `python run_server_gui.py`
2. Start server from GUI
3. Connect agent (or wait for auto-connect)

- [ ] **Step 3: Test directory listing**

1. Go to "파일 관리" tab
2. Select agent from dropdown
3. Browse server directories (left panel)
4. Browse agent directories (right panel)
5. Verify: directories show entries with name/size/date

- [ ] **Step 4: Test server→agent file transfer**

1. Select a small file (<1MB) in server panel
2. Click "→ 에이전트로 복사"
3. Verify: progress bar completes, file appears on agent

- [ ] **Step 5: Test agent→server file transfer**

1. Select a small file in agent panel
2. Click "← 서버로 복사"
3. Verify: file appears in server panel

- [ ] **Step 6: Test security (write deny)**

1. Navigate agent panel to `C:/Windows`
2. Try to copy a file to `C:/Windows/test.txt`
3. Verify: error message "쓰기 금지 경로"

- [ ] **Step 7: Commit version bump**

Update `agent/__init__.py` version, then:

```bash
git add -A
git commit -m "feat: dual-pane file manager with bidirectional transfer

- 6 new TCP packet types (CMD_FILE_LIST/GET/PUT + ACKs)
- Agent FileHandler with security (write deny, size limit, path traversal)
- Server-side send/receive with transfer locking
- tkinter dual-pane GUI tab
- Dashboard HTTP API for web integration (Phase 2)"
```
