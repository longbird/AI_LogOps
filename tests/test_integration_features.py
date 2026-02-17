"""통합 테스트: 로그 전송, 프로세스 관리, 스케줄링, 시스템 모니터링."""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.log_cmd_handler import LogCmdHandler
from agent.core.log_watcher import LogWatcher
from agent.core.process_mgr import ProcessManager
from agent.core.process_monitor import ProcessMonitorLoop
from agent.core.scheduler import ProcessScheduler
from agent.core.system_monitor import SystemMonitor, SystemMetrics, ProcessMetrics
from shared.protocol import (
    CmdLogPayload,
    CmdLogAckPayload,
    LogAction,
    LogAckStatus,
    LogFileSelectPayload,
    PacketType,
)


class TestFeatureA_LogTransmission:
    """Feature A: Command-driven log transmission."""

    async def test_hist_request_end_to_end(self, tmp_path: Path) -> None:
        """CMD_LOG HIST_REQUEST → find files → send ACK + LOG_HIST packets."""
        # Setup: create log files in tmp_path
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "20260217_app.txt").write_text("line1\nline2\n")
        (log_dir / "20260217_error.txt").write_text("error1\n")
        (log_dir / "20260216_old.txt").write_text("old data\n")

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

        # Send HIST_REQUEST for 20260217
        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        # Verify: LOG_FILE_LIST sent
        mock_client.send_log_file_list.assert_called_once()

        # Send file selection
        from shared.protocol import LogFileSelectPayload

        select = LogFileSelectPayload(
            filenames=["20260217_app.txt", "20260217_error.txt"]
        ).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        # Verify: ACK sent with file_count=2
        ack_call = mock_client.send_packet.call_args_list[0]
        assert ack_call[0][0] == PacketType.CMD_LOG_ACK
        ack = CmdLogAckPayload.unpack(ack_call[0][1])
        assert ack.file_count == 2
        assert ack.status == LogAckStatus.SUCCESS

        # Verify: 2 LOG_HIST packets sent
        assert mock_client.send_log_history.call_count == 2

    async def test_hist_request_no_matching_date(self, tmp_path: Path) -> None:
        """HIST_REQUEST for non-existent date returns empty file list."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "20260217_app.txt").write_text("data\n")

        watcher = LogWatcher(
            watch_dirs=[str(log_dir)],
            extensions=[".txt"],
            on_new_line=AsyncMock(),
        )

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20250101").pack()
        await handler.handle_cmd_log(payload)

        # Empty file list sent, no ACK via send_packet
        mock_client.send_log_file_list.assert_called_once_with([])
        mock_client.send_packet.assert_not_called()
        mock_client.send_log_history.assert_not_called()

    async def test_realtime_toggle(self) -> None:
        """REAL_START → active → REAL_STOP → inactive."""
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        assert not handler.is_realtime_active

        # Start
        start = CmdLogPayload(action=LogAction.REAL_START, date="").pack()
        await handler.handle_cmd_log(start)
        assert handler.is_realtime_active

        # Stop
        stop = CmdLogPayload(action=LogAction.REAL_STOP, date="").pack()
        await handler.handle_cmd_log(stop)
        assert not handler.is_realtime_active

    async def test_realtime_ack_packets_sent(self) -> None:
        """REAL_START and REAL_STOP both send proper ACK packets."""
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        # Start
        start = CmdLogPayload(action=LogAction.REAL_START, date="").pack()
        await handler.handle_cmd_log(start)
        start_ack = CmdLogAckPayload.unpack(
            mock_client.send_packet.call_args_list[0][0][1]
        )
        assert start_ack.action == LogAction.REAL_START
        assert start_ack.status == LogAckStatus.SUCCESS

        # Stop
        stop = CmdLogPayload(action=LogAction.REAL_STOP, date="").pack()
        await handler.handle_cmd_log(stop)
        stop_ack = CmdLogAckPayload.unpack(
            mock_client.send_packet.call_args_list[1][0][1]
        )
        assert stop_ack.action == LogAction.REAL_STOP
        assert stop_ack.status == LogAckStatus.SUCCESS

    async def test_invalid_payload_ignored(self) -> None:
        """Invalid CMD_LOG payload is silently ignored."""
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        await handler.handle_cmd_log(b"\xff")
        mock_client.send_packet.assert_not_called()


class TestFeatureB_ProcessManagement:
    """Feature B: Enhanced process management with auto-restart."""

    async def test_auto_restart_detects_and_restarts(self) -> None:
        """ProcessMonitorLoop detects missing process and auto-restarts."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = None  # not running
        mock_mgr.start.return_value = 5555
        mock_mgr.process_name = "TestApp.exe"

        notifications: list[str] = []

        async def notify(msg: str) -> None:
            notifications.append(msg)

        loop = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            process_args=["--config", "prod.yaml"],
            on_notify=notify,
        )
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.3)
        loop.stop()
        await task

        # Process was started with args
        mock_mgr.start.assert_called_with(args=["--config", "prod.yaml"])
        # Notification sent
        assert len(notifications) >= 1
        assert "TestApp.exe" in notifications[0]
        assert "5555" in notifications[0]

    async def test_monitor_does_not_restart_running_process(self) -> None:
        """ProcessMonitorLoop does not restart when process is alive."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = 4444  # running
        mock_mgr.process_name = "RunningApp.exe"

        notifications: list[str] = []

        async def notify(msg: str) -> None:
            notifications.append(msg)

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
        assert len(notifications) == 0

    def test_start_with_configurable_args(self, tmp_path: Path) -> None:
        """ProcessManager.start() accepts args from config."""
        exe = tmp_path / "app.exe"
        exe.write_text("fake")
        mgr = ProcessManager(
            process_name="app.exe",
            process_path=str(exe),
            backup_dir=str(tmp_path / "backups"),
        )
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=7777)
            pid = mgr.start(args=["--port", "9090", "--verbose"])
            assert pid == 7777
            cmd = mock_popen.call_args[0][0]
            assert cmd == [str(exe), "--port", "9090", "--verbose"]

    def test_start_without_args(self, tmp_path: Path) -> None:
        """ProcessManager.start() works without extra args."""
        exe = tmp_path / "app.exe"
        exe.write_text("fake")
        mgr = ProcessManager(
            process_name="app.exe",
            process_path=str(exe),
            backup_dir=str(tmp_path / "backups"),
        )
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=6666)
            pid = mgr.start()
            assert pid == 6666
            cmd = mock_popen.call_args[0][0]
            assert cmd == [str(exe)]


class TestFeatureC_Scheduling:
    """Feature C: HH:MM scheduled process restart."""

    async def test_scheduled_restart_at_configured_time(self) -> None:
        """ProcessScheduler restarts at scheduled HH:MM."""
        mock_mgr = MagicMock()
        mock_mgr.kill.return_value = True
        mock_mgr.start.return_value = 8888
        mock_mgr.process_name = "ScheduledApp.exe"

        notifications: list[str] = []

        async def notify(msg: str) -> None:
            notifications.append(msg)

        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["09:30"],
            process_args=["--daemon"],
            on_notify=notify,
        )

        # Simulate time matching
        fake_now = datetime(2026, 2, 17, 9, 30, 0)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await sched.check_and_restart()

        mock_mgr.kill.assert_called_once()
        mock_mgr.start.assert_called_once_with(args=["--daemon"])
        assert len(notifications) == 1
        assert "ScheduledApp.exe" in notifications[0]
        assert "09:30" in notifications[0]

    async def test_no_restart_outside_schedule(self) -> None:
        """No restart when current time doesn't match."""
        mock_mgr = MagicMock()
        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["06:00", "18:00"],
        )

        fake_now = datetime(2026, 2, 17, 12, 0, 0)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            await sched.check_and_restart()

        mock_mgr.kill.assert_not_called()

    async def test_no_duplicate_restart_same_minute(self) -> None:
        """Scheduler does not restart twice in the same minute."""
        mock_mgr = MagicMock()
        mock_mgr.kill.return_value = True
        mock_mgr.start.return_value = 3333
        mock_mgr.process_name = "app.exe"

        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["10:00"],
        )

        fake_now = datetime(2026, 2, 17, 10, 0, 15)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await sched.check_and_restart()
            await sched.check_and_restart()  # second call same minute

        assert mock_mgr.kill.call_count == 1
        assert mock_mgr.start.call_count == 1

    async def test_multiple_scheduled_times(self) -> None:
        """Scheduler supports multiple HH:MM entries."""
        mock_mgr = MagicMock()
        mock_mgr.kill.return_value = True
        mock_mgr.start.return_value = 1111
        mock_mgr.process_name = "multi.exe"

        notifications: list[str] = []

        async def notify(msg: str) -> None:
            notifications.append(msg)

        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["06:00", "12:00", "18:00"],
            on_notify=notify,
        )

        # Trigger at 06:00
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 17, 6, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await sched.check_and_restart()

        assert mock_mgr.kill.call_count == 1
        assert "06:00" in notifications[0]


class TestFeatureD_SystemMonitoring:
    """Feature D: System monitoring with Telegram direct response."""

    def test_system_metrics_collection(self) -> None:
        """SystemMonitor collects valid system-wide metrics."""
        monitor = SystemMonitor(target_process_name="nonexistent_xyz.exe")
        metrics = monitor.collect_system()
        assert isinstance(metrics, SystemMetrics)
        assert 0 <= metrics.cpu_percent <= 100
        assert 0 <= metrics.memory_percent <= 100

    def test_target_process_not_found(self) -> None:
        """collect_target_process returns None when process doesn't exist."""
        monitor = SystemMonitor(target_process_name="nonexistent_xyz_99.exe")
        assert monitor.collect_target_process() is None

    def test_format_status_report_includes_system_info(self) -> None:
        """format_status_report produces readable output."""
        monitor = SystemMonitor(target_process_name="nonexistent.exe")
        report = monitor.format_status_report()
        assert "CPU" in report
        assert "Memory" in report

    def test_format_status_report_shows_not_found(self) -> None:
        """format_status_report shows 'not found' for missing process."""
        monitor = SystemMonitor(target_process_name="nonexistent_abc_42.exe")
        report = monitor.format_status_report()
        assert "not found" in report

    def test_metrics_format_methods(self) -> None:
        """SystemMetrics and ProcessMetrics format correctly."""
        sys_m = SystemMetrics(
            cpu_percent=55.5,
            memory_percent=70.2,
            handle_count=3000,
            gdi_count=800,
        )
        text = sys_m.format()
        assert "55.5" in text
        assert "70.2" in text
        assert "3000" in text

        proc_m = ProcessMetrics(
            name="app.exe",
            pid=1234,
            cpu_percent=12.3,
            memory_mb=512.7,
            handle_count=150,
            gdi_count=45,
        )
        text = proc_m.format()
        assert "app.exe" in text
        assert "1234" in text
        assert "12.3" in text
        assert "512.7" in text

    def test_system_metrics_dataclass_fields(self) -> None:
        """SystemMetrics stores all expected fields."""
        m = SystemMetrics(
            cpu_percent=10.0, memory_percent=20.0, handle_count=100, gdi_count=50
        )
        assert m.cpu_percent == 10.0
        assert m.memory_percent == 20.0
        assert m.handle_count == 100
        assert m.gdi_count == 50

    def test_process_metrics_dataclass_fields(self) -> None:
        """ProcessMetrics stores all expected fields."""
        m = ProcessMetrics(
            name="x.exe",
            pid=99,
            cpu_percent=5.0,
            memory_mb=256.0,
            handle_count=10,
            gdi_count=3,
        )
        assert m.name == "x.exe"
        assert m.pid == 99
        assert m.memory_mb == 256.0


class TestFeaturesCombined:
    """Cross-feature integration scenarios."""

    async def test_log_cmd_handler_with_real_watcher(self, tmp_path: Path) -> None:
        """LogCmdHandler uses real LogWatcher to find and read files."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "20260217_system.txt").write_text(
            "boot sequence ok\nservice started\n"
        )

        watcher = LogWatcher(
            watch_dirs=[str(log_dir)],
            extensions=[".txt"],
            on_new_line=AsyncMock(),
        )

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=watcher, tcp_client=mock_client)

        # HIST_REQUEST
        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        # Send file selection
        from shared.protocol import LogFileSelectPayload

        select = LogFileSelectPayload(filenames=["20260217_system.txt"]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        # Verify file was found and sent
        assert mock_client.send_log_history.call_count == 1
        sent_filename = mock_client.send_log_history.call_args[0][0]
        assert "20260217_system.txt" in sent_filename

    async def test_monitor_and_scheduler_coexist(self) -> None:
        """ProcessMonitorLoop and ProcessScheduler can run concurrently."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = 1234  # process is running
        mock_mgr.process_name = "app.exe"

        monitor = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
        )
        scheduler = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["23:59"],  # won't trigger
            check_interval=0.1,
        )

        monitor_task = asyncio.create_task(monitor.run())
        scheduler_task = asyncio.create_task(scheduler.run())

        await asyncio.sleep(0.3)

        monitor.stop()
        scheduler.stop()
        await monitor_task
        await scheduler_task

        # No restarts should have happened
        mock_mgr.start.assert_not_called()
        mock_mgr.kill.assert_not_called()

    async def test_monitor_restart_triggers_notification(self) -> None:
        """Monitor auto-restart sends notification that could go to Telegram."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.side_effect = [None, 9999]  # first check: down, then up
        mock_mgr.start.return_value = 9999
        mock_mgr.process_name = "monitored.exe"

        notifications: list[str] = []

        async def notify(msg: str) -> None:
            notifications.append(msg)

        loop = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            process_args=["--mode", "production"],
            on_notify=notify,
        )
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.3)
        loop.stop()
        await task

        # At least one restart notification
        assert len(notifications) >= 1
        assert "monitored.exe" in notifications[0]
        assert "9999" in notifications[0]

    async def test_scheduler_restart_with_system_metrics(self) -> None:
        """Scheduler restart + system monitor report can be generated."""
        mock_mgr = MagicMock()
        mock_mgr.kill.return_value = True
        mock_mgr.start.return_value = 7777
        mock_mgr.process_name = "combined.exe"

        notifications: list[str] = []

        async def notify(msg: str) -> None:
            notifications.append(msg)

        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["08:00"],
            on_notify=notify,
        )

        fake_now = datetime(2026, 2, 17, 8, 0, 0)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await sched.check_and_restart()

        # Scheduler triggered
        assert mock_mgr.kill.call_count == 1
        assert "combined.exe" in notifications[0]
        assert "08:00" in notifications[0]

        # System monitor can still collect metrics independently
        monitor = SystemMonitor(target_process_name="combined.exe")
        metrics = monitor.collect_system()
        assert isinstance(metrics, SystemMetrics)

    async def test_log_watcher_finds_only_matching_extensions(
        self, tmp_path: Path
    ) -> None:
        """LogWatcher filters files by extension correctly."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "20260217_app.txt").write_text("log data\n")
        (log_dir / "20260217_app.log").write_text("log data\n")
        (log_dir / "20260217_app.csv").write_text("col1,col2\n")

        watcher = LogWatcher(
            watch_dirs=[str(log_dir)],
            extensions=[".txt", ".log"],
            on_new_line=AsyncMock(),
        )

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        # Send file selection for both matching files
        from shared.protocol import LogFileSelectPayload

        select = LogFileSelectPayload(
            filenames=["20260217_app.txt", "20260217_app.log"]
        ).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        # ACK should have file_count=2 (.txt and .log, not .csv)
        ack = CmdLogAckPayload.unpack(mock_client.send_packet.call_args_list[0][0][1])
        assert ack.file_count == 2
        assert mock_client.send_log_history.call_count == 2


class TestFeatureA_SelectiveLogTransfer:
    """Feature A v2: Selective 3-step log transfer."""

    async def test_selective_transfer_full_flow(self, tmp_path: Path) -> None:
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

        # Verify: LOG_FILE_LIST sent with 3 entries (metadata)
        mock_client.send_log_file_list.assert_called_once()
        entries = mock_client.send_log_file_list.call_args[0][0]
        assert len(entries) == 3
        # Verify entries have correct metadata
        filenames = {e.filename for e in entries}
        assert filenames == {
            "20260217_app.txt",
            "20260217_error.txt",
            "20260217_debug.txt",
        }
        for entry in entries:
            assert entry.file_size > 0
            assert len(entry.md5) == 16

        # Phase 2: Server selects only 2 files (simulating comparison result)
        select = LogFileSelectPayload(
            filenames=["20260217_app.txt", "20260217_debug.txt"]
        ).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        # Verify: Only 2 files sent via LOG_HIST (not 3)
        assert mock_client.send_log_history.call_count == 2
        sent_filenames = {
            call.args[0] for call in mock_client.send_log_history.call_args_list
        }
        assert sent_filenames == {"20260217_app.txt", "20260217_debug.txt"}

        # ACK sent with correct count
        ack_calls = [
            c
            for c in mock_client.send_packet.call_args_list
            if c[0][0] == PacketType.CMD_LOG_ACK
        ]
        assert len(ack_calls) == 1
        ack = CmdLogAckPayload.unpack(ack_calls[0][0][1])
        assert ack.file_count == 2
        assert ack.status == LogAckStatus.SUCCESS

    async def test_selective_transfer_server_selects_none(self, tmp_path: Path) -> None:
        """Server selects 0 files (all already stored) → no LOG_HIST sent."""
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

        # Server says: I have everything already
        select = LogFileSelectPayload(filenames=[]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        mock_client.send_log_history.assert_not_called()
        # ACK still sent with 0 files
        ack_calls = [
            c
            for c in mock_client.send_packet.call_args_list
            if c[0][0] == PacketType.CMD_LOG_ACK
        ]
        assert len(ack_calls) == 1
        ack = CmdLogAckPayload.unpack(ack_calls[0][0][1])
        assert ack.file_count == 0

    async def test_selective_transfer_with_metadata_verification(
        self, tmp_path: Path
    ) -> None:
        """Verify file metadata (size, MD5) is computed correctly."""
        import hashlib

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        content = b"known content for hash verification"
        (log_dir / "20260217_test.txt").write_bytes(content)

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

        entries = mock_client.send_log_file_list.call_args[0][0]
        assert len(entries) == 1
        assert entries[0].filename == "20260217_test.txt"
        assert entries[0].file_size == len(content)
        assert entries[0].md5 == hashlib.md5(content).digest()

        # Complete the flow
        select = LogFileSelectPayload(filenames=["20260217_test.txt"]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)
