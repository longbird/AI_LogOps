from __future__ import annotations

import logging
from typing import Any

from server.telegram.handler import ParsedCommand, TelegramHandler
from shared.utils import setup_logging


class ServerCommandHandler:
    """서버 관리 텔레그램 명령 핸들러 (대시보드 시작/종료 등)."""

    def __init__(self, dashboard_ctrl: Any) -> None:
        self.dashboard_ctrl = dashboard_ctrl
        self._logger: logging.Logger = setup_logging("server_commands")

    def register_all(self, handler: TelegramHandler) -> None:
        handler.register("dashboard", self.cmd_dashboard)

    async def cmd_dashboard(self, cmd: ParsedCommand) -> str:
        """
        /dashboard [start|stop|status]
        대시보드 웹서버 시작/종료/상태 확인.
        인수 없으면 상태를 보여준다.
        """
        action = cmd.args[0].lower() if cmd.args else "status"

        if action == "start":
            result = await self.dashboard_ctrl.start()
            self._logger.info("텔레그램 요청으로 대시보드 시작: %s", result)
            return result

        if action == "stop":
            result = await self.dashboard_ctrl.stop()
            self._logger.info("텔레그램 요청으로 대시보드 종료: %s", result)
            return result

        if action == "status":
            return self.dashboard_ctrl.status()

        return "Usage: /dashboard [start|stop|status]"
