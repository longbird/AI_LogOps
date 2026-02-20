"""프로세스 스케줄러 테스트."""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.scheduler import ProcessScheduler


class TestProcessSchedulerParsing:
    def test_parse_valid_restart_times(self):
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

    def test_empty_restart_times(self):
        sched = ProcessScheduler(
            process_mgr=MagicMock(),
            restart_times=[],
        )
        assert sched._times == []


class TestProcessSchedulerRestart:
    async def test_triggers_restart_at_scheduled_time(self):
        mock_mgr = MagicMock()
        mock_mgr.kill_all.return_value = True
        mock_mgr.start.return_value = 9999
        mock_mgr.process_name = "target.exe"

        notify = AsyncMock()
        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["14:30"],
            process_args=["--verbose"],
            on_notify=notify,
        )

        fake_now = datetime(2026, 2, 17, 14, 30, 0)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await sched.check_and_restart()

        mock_mgr.kill_all.assert_called_once()
        mock_mgr.start.assert_called_once_with(args=["--verbose"])
        notify.assert_called()
        call_text = notify.call_args[0][0]
        assert "target.exe" in call_text
        assert "14:30" in call_text
        assert "9999" in call_text

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

        mock_mgr.kill_all.assert_not_called()

    async def test_does_not_trigger_twice_same_minute(self):
        mock_mgr = MagicMock()
        mock_mgr.kill_all.return_value = True
        mock_mgr.start.return_value = 1111
        mock_mgr.process_name = "app.exe"

        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["10:00"],
        )

        fake_now = datetime(2026, 2, 17, 10, 0, 30)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await sched.check_and_restart()
            await sched.check_and_restart()  # second call same minute

        # kill_all and start should be called only once
        assert mock_mgr.kill_all.call_count == 1
        assert mock_mgr.start.call_count == 1

    async def test_run_with_empty_times_returns_immediately(self):
        sched = ProcessScheduler(
            process_mgr=MagicMock(),
            restart_times=[],
        )
        # Should return immediately without blocking
        task = asyncio.create_task(sched.run())
        await asyncio.sleep(0.1)
        assert task.done()

    async def test_no_notify_callback(self):
        mock_mgr = MagicMock()
        mock_mgr.kill_all.return_value = True
        mock_mgr.start.return_value = 2222
        mock_mgr.process_name = "app.exe"

        sched = ProcessScheduler(
            process_mgr=mock_mgr,
            restart_times=["15:00"],
            on_notify=None,
        )

        fake_now = datetime(2026, 2, 17, 15, 0, 0)
        with patch("agent.core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await sched.check_and_restart()

        mock_mgr.kill_all.assert_called_once()
