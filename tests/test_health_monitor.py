from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from server.core.health_monitor import HealthMonitor

pytestmark = pytest.mark.usefixtures("mock_psutil")


@dataclass
class MockMemory:
    percent: float


@dataclass
class MockDisk:
    percent: float


@pytest.fixture
def health_config() -> dict[str, int]:
    return {
        "check_interval": 300,
        "cpu_threshold": 90,
        "memory_threshold": 85,
        "disk_threshold": 90,
        "alert_cooldown": 1800,
    }


@pytest.fixture
def mock_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("psutil.cpu_percent", lambda interval=None: 50.0)
    monkeypatch.setattr("psutil.virtual_memory", lambda: MockMemory(percent=60.0))
    monkeypatch.setattr("psutil.disk_usage", lambda path: MockDisk(percent=70.0))


@pytest.mark.asyncio
async def test_check_cpu_under_threshold(health_config: dict[str, int]) -> None:
    notifier = AsyncMock()
    monitor = HealthMonitor(health_config, telegram_notifier=notifier)

    result = await monitor._check_cpu()

    assert result == {"value": 50.0, "threshold": 90, "alert": False}
    assert monitor._cpu_high_count == 0
    notifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_cpu_over_threshold_once(
    health_config: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = AsyncMock()
    monitor = HealthMonitor(health_config, telegram_notifier=notifier)
    monkeypatch.setattr("psutil.cpu_percent", lambda interval=None: 95.0)

    result = await monitor._check_cpu()

    assert result == {"value": 95.0, "threshold": 90, "alert": False}
    assert monitor._cpu_high_count == 1
    notifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_cpu_over_threshold_three_times(
    health_config: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = AsyncMock()
    monitor = HealthMonitor(health_config, telegram_notifier=notifier)
    monkeypatch.setattr("psutil.cpu_percent", lambda interval=None: 95.0)

    await monitor._check_cpu()
    await monitor._check_cpu()
    result = await monitor._check_cpu()

    assert result == {"value": 95.0, "threshold": 90, "alert": True}
    assert monitor._cpu_high_count == 3
    notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_cpu_resets_count(
    health_config: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monitor = HealthMonitor(health_config, telegram_notifier=AsyncMock())
    monkeypatch.setattr("psutil.cpu_percent", lambda interval=None: 95.0)

    await monitor._check_cpu()
    assert monitor._cpu_high_count == 1

    monkeypatch.setattr("psutil.cpu_percent", lambda interval=None: 40.0)
    result = await monitor._check_cpu()

    assert result == {"value": 40.0, "threshold": 90, "alert": False}
    assert monitor._cpu_high_count == 0


@pytest.mark.asyncio
async def test_check_memory_over_threshold(
    health_config: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = AsyncMock()
    monitor = HealthMonitor(health_config, telegram_notifier=notifier)
    monkeypatch.setattr("psutil.virtual_memory", lambda: MockMemory(percent=90.0))

    result = await monitor._check_memory()

    assert result == {"value": 90.0, "threshold": 85, "alert": True}
    notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_memory_under_threshold(
    health_config: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = AsyncMock()
    monitor = HealthMonitor(health_config, telegram_notifier=notifier)
    monkeypatch.setattr("psutil.virtual_memory", lambda: MockMemory(percent=60.0))

    result = await monitor._check_memory()

    assert result == {"value": 60.0, "threshold": 85, "alert": False}
    notifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_disk_over_threshold(
    health_config: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = AsyncMock()
    monitor = HealthMonitor(health_config, telegram_notifier=notifier)
    monkeypatch.setattr("psutil.disk_usage", lambda path: MockDisk(percent=95.0))

    result = await monitor._check_disk()

    assert result == {"value": 95.0, "threshold": 90, "alert": True}
    notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_alert_cooldown_prevents_spam(
    health_config: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = AsyncMock()
    monitor = HealthMonitor(health_config, telegram_notifier=notifier)
    now = {"value": 1000.0}

    monkeypatch.setattr(
        "server.core.health_monitor.time.time",
        lambda: now["value"],
    )

    await monitor._send_alert("memory", "first")
    now["value"] = 1010.0
    await monitor._send_alert("memory", "second")

    notifier.assert_awaited_once_with("first")


@pytest.mark.asyncio
async def test_run_checks_returns_all_metrics(health_config: dict[str, int]) -> None:
    monitor = HealthMonitor(health_config, telegram_notifier=AsyncMock())

    result = await monitor.run_checks()

    assert set(result.keys()) == {"cpu", "memory", "disk"}
    assert result["cpu"] == {"value": 50.0, "threshold": 90, "alert": False}
    assert result["memory"] == {"value": 60.0, "threshold": 85, "alert": False}
    assert result["disk"] == {"value": 70.0, "threshold": 90, "alert": False}


def test_get_status_returns_snapshot(health_config: dict[str, int]) -> None:
    monitor = HealthMonitor(health_config, telegram_notifier=AsyncMock())

    status = monitor.get_status()

    assert set(status.keys()) == {
        "cpu",
        "memory",
        "disk",
        "cpu_high_count",
        "running",
    }
    assert status["cpu"] == 50.0
    assert status["memory"] == 60.0
    assert status["disk"] == 70.0
    assert status["cpu_high_count"] == 0
    assert status["running"] is False
