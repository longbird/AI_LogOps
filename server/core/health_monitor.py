from __future__ import annotations

import asyncio
import contextlib
import time
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

import psutil

from shared.utils import setup_logging


class HealthMonitor:
    """서버 리소스 모니터링. 스펙 섹션 3.3 / v2.1 참조.

    5분 간격으로 CPU/메모리/디스크 사용률 점검.
    임계값 초과 시 텔레그램 알림 (30분 쿨다운).
    """

    def __init__(
        self,
        config: Mapping[str, int | float],
        telegram_notifier: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.check_interval: float = float(config.get("check_interval", 300))
        self.cpu_threshold: float = float(config.get("cpu_threshold", 90))
        self.memory_threshold: float = float(config.get("memory_threshold", 85))
        self.disk_threshold: float = float(config.get("disk_threshold", 90))
        self.alert_cooldown: float = float(config.get("alert_cooldown", 1800))
        self.telegram_notifier: Callable[[str], Awaitable[None]] | None = (
            telegram_notifier
        )
        self._logger: logging.Logger = setup_logging("health_monitor")

        self._last_alerts: dict[str, float] = {}
        self._cpu_high_count: int = 0
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """모니터링 루프 시작."""
        self._running = True
        self._task = asyncio.create_task(self._check_loop())

    async def stop(self) -> None:
        """모니터링 중지."""
        self._running = False
        if self._task:
            _ = self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _check_loop(self) -> None:
        while self._running:
            _ = await self.run_checks()
            await asyncio.sleep(self.check_interval)

    async def run_checks(self) -> dict[str, dict[str, float | bool]]:
        """모든 헬스 체크 실행. 결과 dict 반환."""
        results: dict[str, dict[str, float | bool]] = {}
        results["cpu"] = await self._check_cpu()
        results["memory"] = await self._check_memory()
        results["disk"] = await self._check_disk()
        return results

    async def _check_cpu(self) -> dict[str, float | bool]:
        cpu_percent = psutil.cpu_percent(interval=1)
        result = {
            "value": cpu_percent,
            "threshold": self.cpu_threshold,
            "alert": False,
        }
        if cpu_percent > self.cpu_threshold:
            self._cpu_high_count += 1
            if self._cpu_high_count >= 3:
                result["alert"] = True
                await self._send_alert(
                    "cpu",
                    f"서버 CPU 과부하 ({cpu_percent:.0f}%, {self._cpu_high_count}회 연속)",
                )
        else:
            self._cpu_high_count = 0
        return result

    async def _check_memory(self) -> dict[str, float | bool]:
        mem = psutil.virtual_memory()
        mem_percent = cast(float, mem.percent)
        result = {
            "value": mem_percent,
            "threshold": self.memory_threshold,
            "alert": False,
        }
        if mem_percent > self.memory_threshold:
            result["alert"] = True
            await self._send_alert("memory", f"서버 메모리 부족 ({mem_percent:.0f}%)")
        return result

    async def _check_disk(self) -> dict[str, float | bool]:
        disk = psutil.disk_usage("/")
        disk_percent = disk.percent
        result = {
            "value": disk_percent,
            "threshold": self.disk_threshold,
            "alert": False,
        }
        if disk_percent > self.disk_threshold:
            result["alert"] = True
            await self._send_alert("disk", f"서버 디스크 부족 ({disk_percent:.0f}%)")
        return result

    async def _send_alert(self, metric: str, message: str) -> None:
        """쿨다운 확인 후 텔레그램 알림 전송."""
        now = time.time()
        last = self._last_alerts.get(metric)
        if last is not None and now - last < self.alert_cooldown:
            self._logger.debug("alert cooldown active: metric=%s", metric)
            return

        self._last_alerts[metric] = now
        self._logger.warning("health alert: %s", message)
        if self.telegram_notifier:
            await self.telegram_notifier(message)

    def get_status(self) -> dict[str, float | int | bool]:
        """현재 상태 스냅샷 반환 (대시보드용)."""
        cpu_percent = psutil.cpu_percent(interval=None)
        memory_percent = cast(float, psutil.virtual_memory().percent)
        disk_percent = psutil.disk_usage("/").percent
        return {
            "cpu": cpu_percent,
            "memory": memory_percent,
            "disk": disk_percent,
            "cpu_high_count": self._cpu_high_count,
            "running": self._running,
        }
