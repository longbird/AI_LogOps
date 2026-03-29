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
        # "YYYY-MM-DD HH:MM" 키로 하루에 한 번만 트리거
        self._triggered_dates: set[str] = set()

        self._times: list[time] = []
        for t_str in restart_times:
            try:
                parts = t_str.strip().split(":")
                self._times.append(time(int(parts[0]), int(parts[1])))
            except (ValueError, IndexError):
                logger.warning("invalid restart time format: '%s', skipping", t_str)

    def update_times(self, restart_times: list[str]) -> None:
        """핫리로드: 재시작 스케줄 시간을 교체합니다."""
        new_times: list[time] = []
        for t_str in restart_times:
            try:
                parts = t_str.strip().split(":")
                new_times.append(time(int(parts[0]), int(parts[1])))
            except (ValueError, IndexError):
                logger.warning("invalid restart time format: '%s', skipping", t_str)
        self._times = new_times
        self._triggered_dates.clear()
        logger.info("restart_times hot-reloaded: %s", [t.strftime("%H:%M") for t in self._times])

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
        """현재 시간이 스케줄에 맞으면 재시작 실행 (하루 1회)."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        # 지난 날짜 키 정리
        self._triggered_dates = {
            k for k in self._triggered_dates if k.startswith(today)
        }

        for scheduled_time in self._times:
            time_key = scheduled_time.strftime("%H:%M")
            full_key = f"{today} {time_key}"

            if now.hour == scheduled_time.hour and now.minute == scheduled_time.minute:
                if full_key in self._triggered_dates:
                    continue

                self._triggered_dates.add(full_key)
                logger.info("scheduled restart triggered at %s", time_key)
                await self._do_restart(time_key)

    async def _do_restart(self, time_key: str) -> None:
        """프로세스 재시작 + 검증. 실패 시 1회 재시도."""
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                self._mgr.kill_all()
                await asyncio.sleep(3)
                new_pid = self._mgr.start(args=self._args)

                # 5초 후 프로세스 생존 검증
                await asyncio.sleep(5)
                verify_pid = self._mgr.find_pid()
                if verify_pid is not None:
                    msg = (
                        f"[Scheduled Restart] {self._mgr.process_name} "
                        f"restarted at {time_key}. PID: {new_pid}"
                    )
                    logger.info(msg)
                    if self._notify is not None:
                        await self._notify(msg)
                    return

                logger.warning(
                    "restart verification failed (attempt %d/%d)",
                    attempt,
                    max_attempts,
                )
            except Exception:
                logger.exception(
                    "scheduled restart failed at %s (attempt %d/%d)",
                    time_key,
                    attempt,
                    max_attempts,
                )

        # 모든 시도 실패
        msg = (
            f"[Scheduled Restart FAILED] {self._mgr.process_name} "
            f"at {time_key} after {max_attempts} attempts"
        )
        logger.error(msg)
        if self._notify is not None:
            await self._notify(msg)
