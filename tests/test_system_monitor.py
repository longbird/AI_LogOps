from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import sys
from unittest.mock import MagicMock, patch

from agent.core.system_monitor import ProcessMetrics, SystemMetrics, SystemMonitor


class TestSystemMetrics:
    def test_creation(self) -> None:
        metrics = SystemMetrics(
            cpu_percent=12.5,
            memory_percent=34.5,
            handle_count=12345,
            gdi_count=678,
        )

        assert metrics.cpu_percent == 12.5
        assert metrics.memory_percent == 34.5
        assert metrics.handle_count == 12345
        assert metrics.gdi_count == 678

    def test_format_contains_all_fields(self) -> None:
        metrics = SystemMetrics(
            cpu_percent=12.5,
            memory_percent=34.5,
            handle_count=12345,
            gdi_count=678,
        )

        output = metrics.format()
        assert "CPU" in output
        assert "Memory" in output
        assert "Handle" in output
        assert "GDI" in output


class TestProcessMetrics:
    def test_creation(self) -> None:
        metrics = ProcessMetrics(
            name="target.exe",
            pid=999,
            cpu_percent=15.0,
            memory_mb=256.0,
            handle_count=200,
            gdi_count=10,
        )

        assert metrics.name == "target.exe"
        assert metrics.pid == 999
        assert metrics.cpu_percent == 15.0
        assert metrics.memory_mb == 256.0
        assert metrics.handle_count == 200
        assert metrics.gdi_count == 10

    def test_format_contains_name_and_pid(self) -> None:
        metrics = ProcessMetrics(
            name="target.exe",
            pid=999,
            cpu_percent=15.0,
            memory_mb=256.0,
            handle_count=200,
            gdi_count=10,
        )

        output = metrics.format()
        assert "target.exe" in output
        assert "999" in output


class TestSystemMonitorCollect:
    def test_collect_system_returns_valid_metrics(self) -> None:
        monitor = SystemMonitor(target_process_name="nonexistent.exe")
        metrics = monitor.collect_system()

        assert isinstance(metrics, SystemMetrics)
        assert 0 <= metrics.cpu_percent <= 100
        assert 0 <= metrics.memory_percent <= 100

    def test_collect_process_not_found(self) -> None:
        monitor = SystemMonitor(target_process_name="nonexistent_xyz_99.exe")
        assert monitor.collect_target_process() is None

    @patch("agent.core.system_monitor.psutil.process_iter")
    def test_collect_process_found(self, mock_iter: MagicMock) -> None:
        class _MemoryInfo:
            def __init__(self, rss: int) -> None:
                self.rss: int = rss

        class _MockProcess:
            def __init__(self) -> None:
                self.info: dict[str, str | int] = {"name": "target.exe", "pid": 999}

            def cpu_percent(self, interval: float | None = None) -> float:
                _ = interval
                return 15.0

            def memory_info(self) -> _MemoryInfo:
                return _MemoryInfo(rss=256 * 1024 * 1024)

            def num_handles(self) -> int:
                if sys.platform == "win32":
                    return 200
                raise AttributeError

        mock_iter.return_value = [_MockProcess()]
        monitor = SystemMonitor(target_process_name="target.exe")

        result = monitor.collect_target_process()

        assert result is not None
        assert result.name == "target.exe"
        assert result.pid == 999

    def test_format_status_report(self) -> None:
        monitor = SystemMonitor(target_process_name="nonexistent.exe")
        report = monitor.format_status_report()

        assert isinstance(report, str)
        assert "System" in report or "CPU" in report
