# Agent Feature Enhancement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enhance the AI-LogOps Agent with 4 unified features: command-driven log transmission, enhanced process management with auto-restart, scheduled process restarts, and system monitoring with Telegram direct response.

**Architecture:** All features integrate into the existing Agent (Windows service) architecture. New modules plug into `agent/core/` and wire through `win_service.py`. Server-side additions are limited to new Telegram commands (`log_commands.py`) and TCP server CMD_LOG handling. Agent responds directly to Telegram `/status` for monitoring data.

**Tech Stack:** Python 3.10+, asyncio, psutil, ctypes (GDI), watchdog, python-telegram-bot, pytest

**Baseline:** 298 tests passing, branch `develop`, commit `102c4cf`

**Reference:** Protocol types `CMD_LOG(0x15)`, `CMD_LOG_ACK(0x16)`, `LogAction`, `CmdLogPayload(9B)`, `CmdLogAckPayload(4B)` already committed in `shared/protocol.py`.

---

## Pre-existing Completed Work

- **Task 1 (Protocol):** `CMD_LOG`, `CMD_LOG_ACK`, `LogAction`, `CmdLogPayload`, `CmdLogAckPayload` — DONE (commit `102c4cf`)
- **LogWatcher.find_files_by_date() / get_latest_file():** Already implemented in `agent/core/log_watcher.py:111-130` — DONE

---

## Wave 1: Foundation Modules (parallel, no dependencies)

### Task 2: ProcessManager — start with args from config

**Feature:** B (Process Management)

**Files:**
- Modify: `agent/core/process_mgr.py` (line 60-68, `start()` method)
- Modify: `tests/test_process_mgr.py`

**Step 1: Write the failing test**

```python
# tests/test_process_mgr.py — ADD to existing tests
class TestProcessManagerStartWithArgs:
    def test_start_with_args(self, tmp_path):
        """start() should pass args to subprocess.Popen."""
        exe = tmp_path / "fake.exe"
        exe.write_text("fake")
        mgr = ProcessManager(
            process_name="fake.exe",
            process_path=str(exe),
            backup_dir=str(tmp_path / "backups"),
        )
        with patch("agent.core.process_mgr.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=1234)
            pid = mgr.start(args=["--port", "8080"])
            assert pid == 1234
            call_args = mock_popen.call_args[0][0]
            assert call_args == [str(exe), "--port", "8080"]

    def test_start_without_args(self, tmp_path):
        """start() with no args should work as before."""
        exe = tmp_path / "fake.exe"
        exe.write_text("fake")
        mgr = ProcessManager(
            process_name="fake.exe",
            process_path=str(exe),
            backup_dir=str(tmp_path / "backups"),
        )
        with patch("agent.core.process_mgr.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=5678)
            pid = mgr.start()
            assert pid == 5678
            call_args = mock_popen.call_args[0][0]
            assert call_args == [str(exe)]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_process_mgr.py -v -k "start_with_args or start_without_args"`
Expected: FAIL — `start()` does not accept `args` parameter

**Step 3: Write minimal implementation**

Modify `agent/core/process_mgr.py` `start()` method:

```python
def start(self, args: list[str] | None = None) -> int:
    """프로세스 시작. PID 반환. args: 추가 실행 인수."""
    import subprocess

    cmd = [str(self.process_path)]
    if args:
        cmd.extend(args)

    proc = subprocess.Popen(
        cmd,
        creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
    )
    return proc.pid
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_process_mgr.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: >= 298 passing (no regressions)

**Step 6: Commit**

```bash
git add agent/core/process_mgr.py tests/test_process_mgr.py
git commit -m "feat: add args parameter to ProcessManager.start() for configurable process arguments"
```

---

### Task 3: SystemMonitor — CPU/Memory/Handle/GDI collection

**Feature:** D (System Monitoring)

**Files:**
- Create: `agent/core/system_monitor.py`
- Create: `tests/test_system_monitor.py`

**Step 1: Write the failing test**

```python
# tests/test_system_monitor.py
"""시스템 모니터링 모듈 테스트."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from agent.core.system_monitor import SystemMonitor, SystemMetrics, ProcessMetrics


class TestSystemMetrics:
    def test_system_metrics_creation(self):
        m = SystemMetrics(cpu_percent=45.0, memory_percent=62.5, handle_count=1234, gdi_count=500)
        assert m.cpu_percent == 45.0
        assert m.memory_percent == 62.5
        assert m.handle_count == 1234
        assert m.gdi_count == 500

    def test_system_metrics_format(self):
        m = SystemMetrics(cpu_percent=45.0, memory_percent=62.5, handle_count=1234, gdi_count=500)
        text = m.format()
        assert "CPU" in text
        assert "45.0" in text
        assert "Memory" in text
        assert "Handle" in text
        assert "GDI" in text


class TestProcessMetrics:
    def test_process_metrics_creation(self):
        m = ProcessMetrics(
            name="test.exe", pid=1234,
            cpu_percent=12.0, memory_mb=256.5,
            handle_count=100, gdi_count=50,
        )
        assert m.name == "test.exe"
        assert m.pid == 1234

    def test_process_metrics_format(self):
        m = ProcessMetrics(
            name="test.exe", pid=1234,
            cpu_percent=12.0, memory_mb=256.5,
            handle_count=100, gdi_count=50,
        )
        text = m.format()
        assert "test.exe" in text
        assert "1234" in text


class TestSystemMonitorCollect:
    def test_collect_system_metrics(self):
        monitor = SystemMonitor(target_process_name="nonexistent.exe")
        metrics = monitor.collect_system()
        assert isinstance(metrics, SystemMetrics)
        assert 0 <= metrics.cpu_percent <= 100
        assert 0 <= metrics.memory_percent <= 100
        # Handle/GDI may be -1 on non-Windows
        assert isinstance(metrics.handle_count, int)
        assert isinstance(metrics.gdi_count, int)

    def test_collect_process_not_found(self):
        monitor = SystemMonitor(target_process_name="nonexistent_process_xyz.exe")
        result = monitor.collect_target_process()
        assert result is None

    @patch("agent.core.system_monitor.psutil.process_iter")
    def test_collect_process_found(self, mock_iter):
        mock_proc = MagicMock()
        mock_proc.info = {"name": "target.exe", "pid": 999}
        mock_proc.cpu_percent.return_value = 15.0
        mock_proc.memory_info.return_value = MagicMock(rss=256 * 1024 * 1024)
        # num_handles only on Windows
        if sys.platform == "win32":
            mock_proc.num_handles.return_value = 200
        else:
            mock_proc.num_handles = MagicMock(side_effect=AttributeError)
        mock_iter.return_value = [mock_proc]

        monitor = SystemMonitor(target_process_name="target.exe")
        result = monitor.collect_target_process()
        assert result is not None
        assert result.name == "target.exe"
        assert result.pid == 999
        assert result.cpu_percent == 15.0

    def test_format_status_report(self):
        monitor = SystemMonitor(target_process_name="nonexistent.exe")
        report = monitor.format_status_report()
        assert isinstance(report, str)
        assert "System" in report
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_system_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.core.system_monitor'`

**Step 3: Write minimal implementation**

```python
# agent/core/system_monitor.py
"""시스템 및 대상 프로세스 모니터링. Handle/GDI 수집 포함."""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

import psutil

from shared.utils import setup_logging

logger = setup_logging("system_monitor")

# Windows GDI constants
GR_GDIOBJECTS = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(slots=True)
class SystemMetrics:
    """전체 시스템 메트릭."""
    cpu_percent: float
    memory_percent: float
    handle_count: int   # 전체 시스템 핸들 수 (합산)
    gdi_count: int      # 전체 시스템 GDI 수 (합산) — -1 if unavailable

    def format(self) -> str:
        gdi_str = str(self.gdi_count) if self.gdi_count >= 0 else "N/A"
        handle_str = str(self.handle_count) if self.handle_count >= 0 else "N/A"
        return (
            f"CPU: {self.cpu_percent:.1f}%\n"
            f"Memory: {self.memory_percent:.1f}%\n"
            f"Handle: {handle_str}\n"
            f"GDI: {gdi_str}"
        )


@dataclass(slots=True)
class ProcessMetrics:
    """대상 프로세스 메트릭."""
    name: str
    pid: int
    cpu_percent: float
    memory_mb: float
    handle_count: int   # -1 if unavailable
    gdi_count: int      # -1 if unavailable

    def format(self) -> str:
        gdi_str = str(self.gdi_count) if self.gdi_count >= 0 else "N/A"
        handle_str = str(self.handle_count) if self.handle_count >= 0 else "N/A"
        return (
            f"Process: {self.name} (PID: {self.pid})\n"
            f"  CPU: {self.cpu_percent:.1f}%\n"
            f"  Memory: {self.memory_mb:.1f} MB\n"
            f"  Handle: {handle_str}\n"
            f"  GDI: {gdi_str}"
        )


class SystemMonitor:
    """시스템 + 대상 프로세스 모니터링."""

    def __init__(self, target_process_name: str):
        self._target_name = target_process_name

    def collect_system(self) -> SystemMetrics:
        """전체 시스템 CPU/Memory/Handle/GDI 수집."""
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        total_handles = 0
        total_gdi = 0
        gdi_available = sys.platform == "win32"

        for proc in psutil.process_iter(["pid"]):
            try:
                p = psutil.Process(proc.info["pid"])
                if sys.platform == "win32":
                    try:
                        total_handles += p.num_handles()
                    except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
                        pass
                if gdi_available:
                    gdi = _get_gdi_count(proc.info["pid"])
                    if gdi >= 0:
                        total_gdi += gdi
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return SystemMetrics(
            cpu_percent=cpu,
            memory_percent=mem,
            handle_count=total_handles if sys.platform == "win32" else -1,
            gdi_count=total_gdi if gdi_available else -1,
        )

    def collect_target_process(self) -> ProcessMetrics | None:
        """대상 프로세스 CPU/Memory/Handle/GDI 수집. 없으면 None."""
        for proc in psutil.process_iter(["name", "pid"]):
            name = proc.info.get("name")
            if not isinstance(name, str):
                continue
            if name.lower() != self._target_name.lower():
                continue

            pid = proc.info["pid"]
            try:
                p = psutil.Process(pid)
                cpu = p.cpu_percent(interval=None)
                mem_bytes = p.memory_info().rss
                mem_mb = mem_bytes / (1024 * 1024)

                handle_count = -1
                if sys.platform == "win32":
                    try:
                        handle_count = p.num_handles()
                    except (psutil.AccessDenied, AttributeError):
                        pass

                gdi_count = _get_gdi_count(pid) if sys.platform == "win32" else -1

                return ProcessMetrics(
                    name=name,
                    pid=pid,
                    cpu_percent=cpu,
                    memory_mb=mem_mb,
                    handle_count=handle_count,
                    gdi_count=gdi_count,
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None

        return None

    def format_status_report(self) -> str:
        """텔레그램 응답용 포맷된 상태 리포트."""
        sys_metrics = self.collect_system()
        proc_metrics = self.collect_target_process()

        lines = ["=== System Status ===", sys_metrics.format()]
        if proc_metrics is not None:
            lines.append("")
            lines.append("=== Target Process ===")
            lines.append(proc_metrics.format())
        else:
            lines.append("")
            lines.append(f"Target process '{self._target_name}' not found.")

        return "\n".join(lines)


def _get_gdi_count(pid: int) -> int:
    """Windows GDI 오브젝트 수 조회. 실패 시 -1."""
    if sys.platform != "win32":
        return -1
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_process:
            return -1
        try:
            gdi = user32.GetGuiResources(h_process, GR_GDIOBJECTS)
            return gdi
        finally:
            kernel32.CloseHandle(h_process)
    except (OSError, AttributeError):
        return -1
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_system_monitor.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: >= 298 passing

**Step 6: Commit**

```bash
git add agent/core/system_monitor.py tests/test_system_monitor.py
git commit -m "feat: add SystemMonitor for CPU/Memory/Handle/GDI collection"
```

---

## Wave 2: Communication & Background Loops (depends on Wave 1)

### Task 4: TCPClient on_cmd_log + TCPServer send_log_command / CMD_LOG_ACK handling

**Feature:** A (Log Transmission)

**Files:**
- Modify: `agent/core/tcp_client.py` (add `on_cmd_log` callback in `_recv_loop`)
- Modify: `server/core/tcp_server.py` (add `send_log_command()`, `_handle_cmd_log_ack()`)
- Modify: `tests/test_tcp_client.py`
- Modify: `tests/test_tcp_server.py`

**Step 1: Write failing tests**

Add to `tests/test_tcp_client.py`:

```python
class TestTCPClientCmdLog:
    async def test_on_cmd_log_callback(self, running_server):
        """CMD_LOG packet triggers on_cmd_log callback."""
        srv, port, mgr = running_server
        client = TCPClient(
            agent_id="PC-LOG-01", version="1.0.0", token=TOKEN,
            host="127.0.0.1", port=port,
        )
        received = []
        async def log_handler(payload: bytes) -> None:
            received.append(payload)
        client.on_cmd_log = log_handler
        await client.connect()
        # Server sends CMD_LOG to the agent
        session = mgr.get_session("PC-LOG-01")
        from shared.protocol import CmdLogPayload, LogAction, Packet, PacketType
        cmd = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217")
        writer = cast(_WriterLike, session.writer)
        writer.write(Packet.build(PacketType.CMD_LOG, cmd.pack()))
        await writer.drain()
        await asyncio.sleep(0.2)
        assert len(received) == 1
        parsed = CmdLogPayload.unpack(received[0])
        assert parsed.action == LogAction.HIST_REQUEST
        assert parsed.date == "20260217"
        await client.disconnect()
```

Add to `tests/test_tcp_server.py`:

```python
class TestTCPServerLogCommand:
    async def test_send_log_command(self, server_and_port):
        """send_log_command sends CMD_LOG packet to agent."""
        srv, port = server_and_port
        # Connect a client
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        # AUTH
        auth = AuthPayload(agent_id="PC-CMD-01", version="1.0.0", token=TOKEN)
        writer.write(Packet.build(PacketType.AUTH, auth.pack()))
        await writer.drain()
        header = await reader.readexactly(HEADER_SIZE)
        ptype, length = PacketHeader.unpack(header)
        _ = await reader.readexactly(length)
        # Server sends log command
        from shared.protocol import LogAction
        result = await srv.send_log_command("PC-CMD-01", LogAction.HIST_REQUEST, "20260217")
        assert result is True
        # Client receives CMD_LOG
        header = await reader.readexactly(HEADER_SIZE)
        ptype, length = PacketHeader.unpack(header)
        payload = await reader.readexactly(length)
        assert ptype == PacketType.CMD_LOG
        from shared.protocol import CmdLogPayload
        cmd = CmdLogPayload.unpack(payload)
        assert cmd.action == LogAction.HIST_REQUEST
        assert cmd.date == "20260217"
        writer.close()
        await writer.wait_closed()
```

**Step 2: Run tests → FAIL**

**Step 3: Implement**

In `agent/core/tcp_client.py`:
- Add `on_cmd_log: PacketCallback | None = None` to `__init__`
- Add `CMD_LOG` handling in `_recv_loop`:
```python
if packet_type == PacketType.CMD_LOG:
    if self.on_cmd_log is not None:
        await self.on_cmd_log(payload)
    continue
```

In `server/core/tcp_server.py`:
- Add import: `CmdLogPayload, CmdLogAckPayload, LogAction, LogAckStatus`
- Add `send_log_command()`:
```python
async def send_log_command(self, agent_id: str, action: LogAction, date: str = "") -> bool:
    """CMD_LOG 패킷을 에이전트에 전송."""
    session = self.session_mgr.get_session(agent_id)
    if session is None or session.writer is None:
        return False
    writer = cast(_WriterLike, session.writer)
    cmd = CmdLogPayload(action=action, date=date)
    writer.write(Packet.build(PacketType.CMD_LOG, cmd.pack()))
    await writer.drain()
    return True
```
- Add `CMD_LOG_ACK` handling in `_recv_loop`:
```python
if packet_type == PacketType.CMD_LOG_ACK:
    self._handle_cmd_log_ack(agent_id, payload)
    continue
```
- Add `_handle_cmd_log_ack()`:
```python
def _handle_cmd_log_ack(self, agent_id: str, payload: bytes) -> None:
    try:
        ack = CmdLogAckPayload.unpack(payload)
    except ValueError:
        self._logger.warning("invalid CMD_LOG_ACK payload: agent_id=%s", agent_id)
        return
    self._logger.info(
        "cmd log ack: agent_id=%s action=%s status=%s file_count=%s",
        agent_id, ack.action.name, ack.status.name, ack.file_count,
    )
```

**Step 4: Run tests → PASS**

**Step 5: Full suite**

Run: `pytest tests/ -v --tb=short`

**Step 6: Commit**

```bash
git add agent/core/tcp_client.py server/core/tcp_server.py tests/test_tcp_client.py tests/test_tcp_server.py
git commit -m "feat: add CMD_LOG handling in TCPClient and send_log_command in TCPServer"
```

---

### Task 5: ProcessMonitorLoop — auto-restart monitoring

**Feature:** B (Process Management)

**Files:**
- Create: `agent/core/process_monitor.py`
- Create: `tests/test_process_monitor.py`

**Step 1: Write failing test**

```python
# tests/test_process_monitor.py
"""프로세스 자동 재시작 모니터링 루프 테스트."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.process_monitor import ProcessMonitorLoop


class TestProcessMonitorLoop:
    async def test_detects_missing_process_and_restarts(self):
        """대상 프로세스 없을 때 자동 재시작."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = None  # not running
        mock_mgr.start.return_value = 1234
        mock_mgr.process_name = "test.exe"

        notify = AsyncMock()
        loop = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            process_args=["--port", "8080"],
            on_notify=notify,
        )
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.3)
        loop.stop()
        await task

        mock_mgr.start.assert_called_with(args=["--port", "8080"])
        notify.assert_called()  # Telegram notification sent
        # Verify notification message contains process name and PID
        call_text = notify.call_args[0][0]
        assert "test.exe" in call_text
        assert "1234" in call_text

    async def test_does_not_restart_when_running(self):
        """프로세스 실행 중이면 재시작 안함."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = 5678  # running

        notify = AsyncMock()
        loop = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            on_notify=notify,
        )
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.3)
        loop.stop()
        await task

        mock_mgr.start.assert_not_called()

    async def test_disabled_does_nothing(self):
        """enabled=False이면 모니터링 안함."""
        mock_mgr = MagicMock()
        loop = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            enabled=False,
        )
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.2)
        loop.stop()
        await task

        mock_mgr.find_pid.assert_not_called()
```

**Step 2: Run test → FAIL**

**Step 3: Implement**

```python
# agent/core/process_monitor.py
"""프로세스 자동 재시작 모니터링 루프."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.process_mgr import ProcessManager

logger = setup_logging("process_monitor")

NotifyCallback = Callable[[str], Awaitable[None]]


class ProcessMonitorLoop:
    """지정 간격으로 대상 프로세스 생존 여부 확인, 없으면 자동 재시작."""

    def __init__(
        self,
        process_mgr: ProcessManager,
        check_interval: float = 30.0,
        process_args: list[str] | None = None,
        on_notify: NotifyCallback | None = None,
        enabled: bool = True,
    ):
        self._mgr = process_mgr
        self._interval = check_interval
        self._args = process_args
        self._notify = on_notify
        self._enabled = enabled
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """모니터링 루프. stop() 호출 시 종료."""
        if not self._enabled:
            return

        self._running = True
        while self._running:
            try:
                pid = self._mgr.find_pid()
                if pid is None:
                    logger.warning(
                        "target process '%s' not found, auto-restarting",
                        self._mgr.process_name,
                    )
                    new_pid = self._mgr.start(args=self._args)
                    msg = (
                        f"[Auto-Restart] {self._mgr.process_name} "
                        f"was not running. Started with PID {new_pid}."
                    )
                    logger.info(msg)
                    if self._notify is not None:
                        await self._notify(msg)
            except Exception:
                logger.exception("process monitor check failed")

            # Sleep in small increments for faster stop response
            elapsed = 0.0
            while self._running and elapsed < self._interval:
                await asyncio.sleep(min(0.5, self._interval - elapsed))
                elapsed += 0.5
```

**Step 4: Run test → PASS**

**Step 5: Full suite**

**Step 6: Commit**

```bash
git add agent/core/process_monitor.py tests/test_process_monitor.py
git commit -m "feat: add ProcessMonitorLoop for auto-restart monitoring with Telegram notification"
```

---

### Task 6: ProcessScheduler — HH:MM scheduled restart

**Feature:** C (Scheduling)

**Files:**
- Create: `agent/core/scheduler.py`
- Create: `tests/test_scheduler.py`

**Step 1: Write failing test**

```python
# tests/test_scheduler.py
"""프로세스 스케줄러 테스트."""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.scheduler import ProcessScheduler


class TestProcessScheduler:
    def test_parse_restart_times(self):
        sched = ProcessScheduler(
            process_mgr=MagicMock(),
            restart_times=["06:00", "18:30"],
        )
        assert sched._times == [time(6, 0), time(18, 30)]

    def test_invalid_time_format_ignored(self):
        sched = ProcessScheduler(
            process_mgr=MagicMock(),
            restart_times=["06:00", "invalid", "18:30"],
        )
        assert len(sched._times) == 2

    async def test_triggers_restart_at_scheduled_time(self):
        mock_mgr = MagicMock()
        mock_mgr.kill.return_value = True
        mock_mgr.start.return_value = 9999
        mock_mgr.process_name = "target.exe"

        notify = AsyncMock()
        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["00:00"],  # will mock current time
            process_args=["--verbose"],
            on_notify=notify,
        )

        # Mock datetime.now to return matching time
        fake_now = datetime(2026, 2, 17, 0, 0, 0)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            await sched.check_and_restart()

        mock_mgr.kill.assert_called_once()
        mock_mgr.start.assert_called_once_with(args=["--verbose"])
        notify.assert_called()

    async def test_does_not_trigger_outside_schedule(self):
        mock_mgr = MagicMock()
        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["06:00"],
        )

        fake_now = datetime(2026, 2, 17, 12, 30, 0)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            await sched.check_and_restart()

        mock_mgr.kill.assert_not_called()
```

**Step 2: Run test → FAIL**

**Step 3: Implement**

```python
# agent/core/scheduler.py
"""프로세스 스케줄 재시작."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, time
from typing import TYPE_CHECKING

from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.process_mgr import ProcessManager

logger = setup_logging("scheduler")

NotifyCallback = Callable[[str], Awaitable[None]]


class ProcessScheduler:
    """설정된 시간(HH:MM)에 대상 프로세스를 재시작."""

    def __init__(
        self,
        process_mgr: ProcessManager,
        restart_times: list[str],
        process_args: list[str] | None = None,
        on_notify: NotifyCallback | None = None,
        check_interval: float = 30.0,
    ):
        self._mgr = process_mgr
        self._args = process_args
        self._notify = on_notify
        self._interval = check_interval
        self._running = False
        self._last_triggered: set[str] = set()

        self._times: list[time] = []
        for t_str in restart_times:
            try:
                parts = t_str.strip().split(":")
                self._times.append(time(int(parts[0]), int(parts[1])))
            except (ValueError, IndexError):
                logger.warning("invalid restart time format: '%s', skipping", t_str)

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """스케줄 루프. check_interval마다 현재 시간 확인."""
        if not self._times:
            return

        self._running = True
        while self._running:
            await self.check_and_restart()
            elapsed = 0.0
            while self._running and elapsed < self._interval:
                await asyncio.sleep(min(1.0, self._interval - elapsed))
                elapsed += 1.0

    async def check_and_restart(self) -> None:
        """현재 시간이 스케줄에 맞으면 재시작 실행."""
        now = datetime.now()
        current_hm = now.strftime("%H:%M")

        for scheduled_time in self._times:
            time_key = scheduled_time.strftime("%H:%M")
            if now.hour == scheduled_time.hour and now.minute == scheduled_time.minute:
                if current_hm in self._last_triggered:
                    continue

                self._last_triggered.add(current_hm)
                logger.info("scheduled restart triggered at %s", time_key)

                try:
                    self._mgr.kill()
                    new_pid = self._mgr.start(args=self._args)
                    msg = (
                        f"[Scheduled Restart] {self._mgr.process_name} "
                        f"restarted at {time_key}. New PID: {new_pid}"
                    )
                    logger.info(msg)
                    if self._notify is not None:
                        await self._notify(msg)
                except Exception:
                    logger.exception("scheduled restart failed at %s", time_key)
            else:
                self._last_triggered.discard(time_key)
```

**Step 4: Run test → PASS**

**Step 5: Full suite**

**Step 6: Commit**

```bash
git add agent/core/scheduler.py tests/test_scheduler.py
git commit -m "feat: add ProcessScheduler for HH:MM scheduled process restarts"
```

---

## Wave 3: Integration Handlers (depends on Wave 2)

### Task 7: LogCmdHandler — process CMD_LOG commands on Agent

**Feature:** A (Log Transmission)

**Files:**
- Create: `agent/core/log_cmd_handler.py`
- Create: `tests/test_log_cmd_handler.py`

**Step 1: Write failing test**

```python
# tests/test_log_cmd_handler.py
"""로그 명령 핸들러 테스트."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.log_cmd_handler import LogCmdHandler
from shared.protocol import CmdLogPayload, LogAction


class TestLogCmdHandlerHistRequest:
    async def test_hist_request_sends_matching_files(self):
        mock_watcher = MagicMock()
        mock_watcher.find_files_by_date.return_value = [
            "/logs/20260217_app.txt",
            "/logs/20260217_error.txt",
        ]
        mock_watcher.read_history.side_effect = [b"log data 1", b"log data 2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(
            log_watcher=mock_watcher,
            tcp_client=mock_client,
        )

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        # Should send CMD_LOG_ACK with SUCCESS and file_count=2
        assert mock_client.send_packet.call_count >= 1
        # Should send 2 LOG_HIST packets
        assert mock_client.send_log_history.call_count == 2

    async def test_hist_request_no_files(self):
        mock_watcher = MagicMock()
        mock_watcher.find_files_by_date.return_value = []

        mock_client = AsyncMock()
        handler = LogCmdHandler(
            log_watcher=mock_watcher,
            tcp_client=mock_client,
        )

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        # ACK with file_count=0
        mock_client.send_packet.assert_called_once()


class TestLogCmdHandlerRealtime:
    async def test_real_start_activates_watching(self):
        mock_watcher = MagicMock()
        mock_watcher.get_latest_file.return_value = "/logs/20260217_app.txt"

        mock_client = AsyncMock()
        handler = LogCmdHandler(
            log_watcher=mock_watcher,
            tcp_client=mock_client,
        )

        payload = CmdLogPayload(action=LogAction.REAL_START, date="").pack()
        await handler.handle_cmd_log(payload)

        assert handler.is_realtime_active
        # ACK sent
        mock_client.send_packet.assert_called()

    async def test_real_stop_deactivates(self):
        mock_watcher = MagicMock()
        mock_watcher.get_latest_file.return_value = "/logs/20260217_app.txt"
        mock_client = AsyncMock()
        handler = LogCmdHandler(
            log_watcher=mock_watcher,
            tcp_client=mock_client,
        )
        # Start first
        start_payload = CmdLogPayload(action=LogAction.REAL_START, date="").pack()
        await handler.handle_cmd_log(start_payload)
        assert handler.is_realtime_active

        # Stop
        stop_payload = CmdLogPayload(action=LogAction.REAL_STOP, date="").pack()
        await handler.handle_cmd_log(stop_payload)
        assert not handler.is_realtime_active
```

**Step 2: Run test → FAIL**

**Step 3: Implement**

```python
# agent/core/log_cmd_handler.py
"""로그 전송 명령 핸들러. CMD_LOG 수신 시 처리."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shared.protocol import (
    CmdLogAckPayload,
    CmdLogPayload,
    LogAckStatus,
    LogAction,
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
    ):
        self._watcher = log_watcher
        self._client = tcp_client
        self._history_max_mb = history_max_mb
        self._realtime_active = False

    @property
    def is_realtime_active(self) -> bool:
        return self._realtime_active

    async def handle_cmd_log(self, payload_data: bytes) -> None:
        """CMD_LOG 패킷 수신 콜백."""
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

    async def _send_ack(
        self, action: LogAction, status: LogAckStatus, file_count: int = 0
    ) -> None:
        ack = CmdLogAckPayload(action=action, status=status, file_count=file_count)
        await self._client.send_packet(PacketType.CMD_LOG_ACK, ack.pack())

    async def _handle_hist_request(self, date_str: str) -> None:
        """YYYYMMDD 날짜의 로그 파일 검색 후 전송."""
        files = self._watcher.find_files_by_date(date_str)
        if not files:
            logger.info("no files found for date=%s", date_str)
            await self._send_ack(LogAction.HIST_REQUEST, LogAckStatus.SUCCESS, 0)
            return

        await self._send_ack(LogAction.HIST_REQUEST, LogAckStatus.SUCCESS, len(files))

        for filepath in files:
            try:
                data = self._watcher.read_history(filepath, max_mb=self._history_max_mb)
                filename = Path(filepath).name
                await self._client.send_log_history(filename, data)
                logger.info("sent log history: %s (%d bytes)", filename, len(data))
            except Exception:
                logger.exception("failed to send log history: %s", filepath)

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

**Step 4: Run test → PASS**

**Step 5: Full suite**

**Step 6: Commit**

```bash
git add agent/core/log_cmd_handler.py tests/test_log_cmd_handler.py
git commit -m "feat: add LogCmdHandler for HIST_REQUEST/REAL_START/REAL_STOP on agent"
```

---

### Task 8: Server Telegram log_commands.py

**Feature:** A (Log Transmission)

**Files:**
- Create: `server/telegram/log_commands.py`
- Create: `tests/test_log_commands.py`

**Step 1: Write failing test**

```python
# tests/test_log_commands.py
"""서버 텔레그램 로그 명령 테스트."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.telegram.log_commands import LogCommandHandler
from server.telegram.handler import ParsedCommand


class TestLogCommands:
    async def test_log_hist_success(self):
        mock_tcp = AsyncMock()
        mock_tcp.send_log_command = AsyncMock(return_value=True)
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(
            tcp_server=mock_tcp,
            session_mgr=mock_session_mgr,
        )
        cmd = ParsedCommand(
            command="log_hist",
            args=["PC-01", "20260217"],
            chat_id=123,
            raw_text="/log_hist PC-01 20260217",
        )
        result = await handler.cmd_log_hist(cmd)
        assert "sent" in result.lower() or "전송" in result.lower() or "PC-01" in result

    async def test_log_hist_missing_args(self):
        handler = LogCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=MagicMock(),
        )
        cmd = ParsedCommand(command="log_hist", args=[], chat_id=123, raw_text="/log_hist")
        result = await handler.cmd_log_hist(cmd)
        assert "usage" in result.lower()

    async def test_log_hist_agent_not_connected(self):
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = None

        handler = LogCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=mock_session_mgr,
        )
        cmd = ParsedCommand(
            command="log_hist",
            args=["PC-01", "20260217"],
            chat_id=123,
            raw_text="/log_hist PC-01 20260217",
        )
        result = await handler.cmd_log_hist(cmd)
        assert "not connected" in result.lower()

    async def test_log_real_start(self):
        mock_tcp = AsyncMock()
        mock_tcp.send_log_command = AsyncMock(return_value=True)
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(tcp_server=mock_tcp, session_mgr=mock_session_mgr)
        cmd = ParsedCommand(
            command="log_real",
            args=["PC-01", "start"],
            chat_id=123,
            raw_text="/log_real PC-01 start",
        )
        result = await handler.cmd_log_real(cmd)
        assert "PC-01" in result

    async def test_log_real_stop(self):
        mock_tcp = AsyncMock()
        mock_tcp.send_log_command = AsyncMock(return_value=True)
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(tcp_server=mock_tcp, session_mgr=mock_session_mgr)
        cmd = ParsedCommand(
            command="log_real",
            args=["PC-01", "stop"],
            chat_id=123,
            raw_text="/log_real PC-01 stop",
        )
        result = await handler.cmd_log_real(cmd)
        assert "PC-01" in result

    def test_register_all(self):
        mock_handler = MagicMock()
        log_handler = LogCommandHandler(
            tcp_server=AsyncMock(), session_mgr=MagicMock()
        )
        log_handler.register_all(mock_handler)
        assert mock_handler.register.call_count == 2
```

**Step 2: Run test → FAIL**

**Step 3: Implement**

```python
# server/telegram/log_commands.py
"""텔레그램 로그 전송 명령 핸들러."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.telegram.handler import ParsedCommand, TelegramHandler
from shared.protocol import LogAction
from shared.utils import setup_logging

if TYPE_CHECKING:
    from server.core.session_mgr import SessionManager
    from server.core.tcp_server import TCPServer


class LogCommandHandler:
    """로그 전송 관련 텔레그램 명령."""

    def __init__(self, tcp_server: TCPServer, session_mgr: SessionManager):
        self.tcp_server: TCPServer = tcp_server
        self.session_mgr: SessionManager = session_mgr
        self._logger: logging.Logger = setup_logging("log_commands")

    def register_all(self, handler: TelegramHandler) -> None:
        handler.register("log_hist", self.cmd_log_hist)
        handler.register("log_real", self.cmd_log_real)

    async def cmd_log_hist(self, cmd: ParsedCommand) -> str:
        """/log_hist <agent_id> <YYYYMMDD>"""
        if len(cmd.args) < 2:
            return "Usage: /log_hist <agent_id> <YYYYMMDD>"

        agent_id = cmd.args[0]
        date_str = cmd.args[1]

        if len(date_str) != 8 or not date_str.isdigit():
            return "Date must be YYYYMMDD format (e.g., 20260217)."

        session = self.session_mgr.get_session(agent_id)
        if session is None:
            return f"Agent {agent_id} is not connected."

        success = await self.tcp_server.send_log_command(
            agent_id, LogAction.HIST_REQUEST, date_str
        )
        if success:
            return f"Log history request sent to {agent_id} for {date_str}."
        return f"Failed to send log command to {agent_id}."

    async def cmd_log_real(self, cmd: ParsedCommand) -> str:
        """/log_real <agent_id> <start|stop>"""
        if len(cmd.args) < 2:
            return "Usage: /log_real <agent_id> <start|stop>"

        agent_id = cmd.args[0]
        action_str = cmd.args[1].lower()

        session = self.session_mgr.get_session(agent_id)
        if session is None:
            return f"Agent {agent_id} is not connected."

        if action_str == "start":
            action = LogAction.REAL_START
        elif action_str == "stop":
            action = LogAction.REAL_STOP
        else:
            return "Action must be 'start' or 'stop'."

        success = await self.tcp_server.send_log_command(agent_id, action, "")
        if success:
            return f"Realtime log {action_str} command sent to {agent_id}."
        return f"Failed to send log command to {agent_id}."
```

**Step 4: Run test → PASS**

**Step 5: Full suite**

**Step 6: Commit**

```bash
git add server/telegram/log_commands.py tests/test_log_commands.py
git commit -m "feat: add server Telegram log commands (/log_hist, /log_real)"
```

---

### Task 9: Agent Telegram /status enhancement with SystemMonitor

**Feature:** D (System Monitoring)

**Files:**
- Modify: `agent/telegram/poller.py` (enhance `_cmd_status`)
- Modify: `tests/test_telegram_poller.py`

**Step 1: Write failing test**

```python
# ADD to tests/test_telegram_poller.py
class TestAgentTelegramStatus:
    async def test_status_includes_system_info(self):
        """Agent /status should include system monitoring data."""
        from agent.telegram.poller import AgentTelegramPoller

        poller = AgentTelegramPoller(bot_token="fake", admin_chat_id=123)

        # Set system monitor
        mock_monitor = MagicMock()
        mock_monitor.format_status_report.return_value = "CPU: 45.0%\nMemory: 62.5%"
        poller.system_monitor = mock_monitor

        # Simulate /status
        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock(id=123)
        mock_update.effective_message = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = None

        await poller._cmd_status(mock_update, mock_context)

        reply_text = mock_update.effective_message.reply_text.call_args[0][0]
        assert "CPU" in reply_text
        assert "Memory" in reply_text
```

**Step 2: Run test → FAIL**

**Step 3: Implement**

In `agent/telegram/poller.py`:
- Add `system_monitor` attribute: `self.system_monitor: SystemMonitor | None = None`
- Modify `_cmd_status` to use `system_monitor.format_status_report()` when available

```python
async def _cmd_status(self, update: _UpdateLike, context: _ContextLike) -> None:
    del context
    if not self._is_admin(update):
        return
    if update.effective_message is None:
        return

    if self.system_monitor is not None:
        report = self.system_monitor.format_status_report()
        status_text = f"Agent: {self._state}\n\n{report}"
    else:
        status_text = f"Agent: {self._state}"

    _ = await update.effective_message.reply_text(status_text)
```

**Step 4: Run test → PASS**

**Step 5: Full suite**

**Step 6: Commit**

```bash
git add agent/telegram/poller.py tests/test_telegram_poller.py
git commit -m "feat: enhance agent /status with SystemMonitor data"
```

---

## Wave 4: Integration & Wiring (depends on all previous)

### Task 10: win_service.py — wire all new components

**Feature:** ALL

**Files:**
- Modify: `agent/service/win_service.py`
- Modify: `tests/test_win_service.py`

**Step 1: Write failing tests**

Add tests verifying:
- `ProcessMonitorLoop` is created and started when `auto_restart: true`
- `ProcessScheduler` is created and started when `schedule.restart_times` exists
- `LogCmdHandler` is created and wired to `tcp_client.on_cmd_log`
- `SystemMonitor` is created and wired to `poller.system_monitor`
- LogWatcher `on_new_line` callback respects `LogCmdHandler.is_realtime_active`

**Step 2: Implement wiring in `_run_agent()`**

Key changes:
1. Parse new config sections (`target_process.args`, `target_process.auto_restart`, `target_process.check_interval`, `schedule.restart_times`)
2. Create `SystemMonitor` → assign to `poller.system_monitor`
3. Create `LogCmdHandler` → assign to `tcp_client.on_cmd_log`
4. Create `ProcessMonitorLoop` (if `auto_restart: true`) → create task
5. Create `ProcessScheduler` (if `restart_times` exists) → create task
6. Modify `_build_log_sender` to check `log_cmd_handler.is_realtime_active`
7. Ensure all tasks are cancelled in `finally` block

**Step 3: Run tests → PASS**

**Step 4: Full suite**

**Step 5: Commit**

```bash
git add agent/service/win_service.py tests/test_win_service.py
git commit -m "feat: wire ProcessMonitorLoop, ProcessScheduler, LogCmdHandler, SystemMonitor into agent service"
```

---

### Task 11: config.yaml — add new configuration sections

**Feature:** B, C

**Files:**
- Modify: `agent/config.yaml`

**Step 1: Add new sections**

```yaml
agent:
  id: "PC-FACTORY-01"
  version: "1.0.0"

telegram:
  bot_token: "YOUR_BOT_TOKEN"
  admin_chat_id: 123456789

monitoring:
  log_folders:
    - "C:/Apps/TargetApp/logs"
    - "C:/Apps/TargetApp/error_logs"
  history_max_mb: 10
  watch_extensions: [".log", ".txt"]

target_process:
  name: "TargetApp.exe"
  path: "C:/Apps/TargetApp/TargetApp.exe"
  backup_dir: "C:/Apps/TargetApp/backups"
  args: []                    # NEW: process start arguments
  auto_restart: false         # NEW: auto-restart if process dies
  check_interval: 30          # NEW: check interval in seconds

schedule:
  restart_times: []           # NEW: HH:MM format, e.g. ["06:00", "18:00"]

connection:
  heartbeat_interval: 30
  reconnect_attempts: 5
  reconnect_delay: 10
```

**Step 2: Commit**

```bash
git add agent/config.yaml
git commit -m "feat: add process args, auto_restart, check_interval, and schedule config"
```

---

### Task 12: Integration test — full feature verification

**Feature:** ALL

**Files:**
- Create: `tests/test_integration_features.py`

**Step 1: Write integration tests**

Test scenarios:
1. **Log transmission:** Server sends CMD_LOG → Agent sends LOG_HIST files back
2. **Realtime toggle:** Server sends REAL_START → Agent's LogCmdHandler activates → Agent sends only new lines → Server sends REAL_STOP → Agent stops
3. **Process auto-restart:** ProcessMonitorLoop detects missing process → starts it → sends Telegram notification
4. **Schedule restart:** ProcessScheduler triggers at scheduled time → kills + starts process
5. **System monitoring:** Agent receives /status → responds with CPU/Memory/Handle/GDI report

**Step 2: Implement integration tests**

**Step 3: Run full suite**

Run: `pytest tests/ -v --tb=short`
Expected: ALL tests pass

**Step 4: Commit**

```bash
git add tests/test_integration_features.py
git commit -m "test: add integration tests for log transmission, process management, scheduling, monitoring"
```

---

## Summary

| Task | Feature | Module | Status |
|------|---------|--------|--------|
| 1 | A | shared/protocol.py (CMD_LOG types) | DONE |
| — | A | agent/core/log_watcher.py (find_files_by_date, get_latest_file) | DONE |
| 2 | B | agent/core/process_mgr.py (start with args) | TODO |
| 3 | D | agent/core/system_monitor.py (NEW) | TODO |
| 4 | A | tcp_client + tcp_server CMD_LOG handling | TODO |
| 5 | B | agent/core/process_monitor.py (NEW) | TODO |
| 6 | C | agent/core/scheduler.py (NEW) | TODO |
| 7 | A | agent/core/log_cmd_handler.py (NEW) | TODO |
| 8 | A | server/telegram/log_commands.py (NEW) | TODO |
| 9 | D | agent/telegram/poller.py (enhanced /status) | TODO |
| 10 | ALL | agent/service/win_service.py (wiring) | TODO |
| 11 | B,C | agent/config.yaml (new sections) | TODO |
| 12 | ALL | tests/test_integration_features.py | TODO |
