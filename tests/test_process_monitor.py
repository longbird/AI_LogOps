"""프로세스 자동 재시작 모니터링 루프 테스트."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.process_monitor import ProcessMonitorLoop


class TestProcessMonitorLoop:
    async def test_detects_missing_process_and_restarts(self):
        """대상 프로세스 없을 때 자동 재시작 + 알림."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = None
        mock_mgr.start.return_value = 1234
        mock_mgr.process_name = "test.exe"

        notify = AsyncMock()
        monitor = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            process_args=["--port", "8080"],
            on_notify=notify,
        )
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.3)
        monitor.stop()
        await task

        mock_mgr.start.assert_called_with(args=["--port", "8080"])
        notify.assert_called()
        call_text = notify.call_args[0][0]
        assert "test.exe" in call_text
        assert "1234" in call_text

    async def test_does_not_restart_when_running(self):
        """프로세스 실행 중이면 재시작하지 않음."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = 5678

        notify = AsyncMock()
        monitor = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            on_notify=notify,
        )
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.3)
        monitor.stop()
        await task

        mock_mgr.start.assert_not_called()
        notify.assert_not_called()

    async def test_disabled_does_nothing(self):
        """enabled=False이면 루프 진입 안함."""
        mock_mgr = MagicMock()
        monitor = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            enabled=False,
        )
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.2)
        monitor.stop()
        await task

        mock_mgr.find_pid.assert_not_called()

    async def test_no_notify_callback(self):
        """on_notify=None이면 알림 없이 재시작."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.return_value = None
        mock_mgr.start.return_value = 9999
        mock_mgr.process_name = "app.exe"

        monitor = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
            on_notify=None,
        )
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.3)
        monitor.stop()
        await task

        mock_mgr.start.assert_called()

    async def test_exception_in_check_does_not_crash(self):
        """find_pid에서 예외 발생해도 루프 계속."""
        mock_mgr = MagicMock()
        mock_mgr.find_pid.side_effect = OSError("access denied")
        mock_mgr.process_name = "err.exe"

        monitor = ProcessMonitorLoop(
            process_mgr=mock_mgr,
            check_interval=0.1,
        )
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.3)
        monitor.stop()
        await task
        # Should not raise — loop continued
