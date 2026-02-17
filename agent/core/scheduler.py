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
                    continue  # Already triggered this minute

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
                # Clear trigger flag when minute passes
                self._last_triggered.discard(time_key)
