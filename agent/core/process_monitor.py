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
    """지정 간격으로 대상 프로세스 생존 여부 확인, 다운 시 알림 및 선택적 자동 재시작."""

    def __init__(
        self,
        process_mgr: ProcessManager,
        check_interval: float = 30.0,
        process_args: list[str] | None = None,
        on_notify: NotifyCallback | None = None,
        enabled: bool = True,
        auto_restart: bool = True,
    ):
        self._mgr = process_mgr
        self._interval = check_interval
        self._args = process_args
        self._notify = on_notify
        self._enabled = enabled
        self._auto_restart = auto_restart
        self._running = False
        self._prev_alive: bool | None = None  # None = 첫 체크 전

    @property
    def process_status(self) -> int:
        """현재 프로세스 상태. 0=미설정/첫체크전, 1=실행중, 2=다운."""
        if self._prev_alive is None:
            return 0
        return 1 if self._prev_alive else 2

    def stop(self) -> None:
        """루프 중지 요청."""
        self._running = False

    async def run(self) -> None:
        """모니터링 루프. stop() 호출 시 종료."""
        if not self._enabled:
            return

        self._running = True
        while self._running:
            try:
                pid = self._mgr.find_pid()
                is_alive = pid is not None

                # 프로세스 다운 감지 (running → not running 전환)
                if self._prev_alive is True and not is_alive:
                    down_msg = (
                        f"[프로세스 다운] {self._mgr.process_name} "
                        f"프로세스가 종료되었습니다."
                    )
                    logger.warning(down_msg)
                    if self._notify is not None:
                        await self._notify(down_msg)

                # 자동 재시작 (활성화 시)
                if not is_alive and self._auto_restart:
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
                    # 재시작 성공 시 상태 갱신
                    is_alive = new_pid is not None

                self._prev_alive = is_alive
            except Exception:
                logger.exception("process monitor check failed")

            # Sleep in small increments for faster stop response
            elapsed = 0.0
            while self._running and elapsed < self._interval:
                await asyncio.sleep(min(0.5, self._interval - elapsed))
                elapsed += 0.5
