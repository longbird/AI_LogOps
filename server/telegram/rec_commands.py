"""텔레그램 음성 분석 명령 핸들러."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.telegram.handler import ParsedCommand, TelegramHandler
from shared.protocol import RecAction
from shared.utils import setup_logging

if TYPE_CHECKING:
    from server.core.session_mgr import SessionManager
    from server.core.tcp_server import TCPServer


class RecCommandHandler:
    """음성 분석 관련 텔레그램 명령."""

    def __init__(self, tcp_server: TCPServer, session_mgr: SessionManager):
        self.tcp_server: TCPServer = tcp_server
        self.session_mgr: SessionManager = session_mgr
        self._logger: logging.Logger = setup_logging("rec_commands")

    def register_all(self, handler: TelegramHandler) -> None:
        handler.register("rec_analyze", self.cmd_rec_analyze)

    async def cmd_rec_analyze(self, cmd: ParsedCommand) -> str:
        """/rec_analyze <agent_id> <start|stop> [YYYYMMDD]"""
        if len(cmd.args) < 2:
            return "Usage: /rec_analyze <agent_id> <start|stop> [YYYYMMDD]"

        agent_id = cmd.args[0]
        action_str = cmd.args[1].lower()

        if action_str not in ("start", "stop"):
            return "Action must be 'start' or 'stop'."

        session = self.session_mgr.get_session(agent_id)
        if session is None:
            return f"Agent {agent_id} is not connected."

        action = RecAction.START if action_str == "start" else RecAction.STOP

        date_str = ""
        if len(cmd.args) >= 3:
            date_str = cmd.args[2]
            if len(date_str) != 8 or not date_str.isdigit():
                return "Date must be YYYYMMDD format (e.g., 20260220)."

        success = await self.tcp_server.send_rec_command(agent_id, action, date_str)
        if success:
            date_msg = f" (date filter: {date_str})" if date_str else ""
            return (
                f"Recording analysis {action_str} command sent to {agent_id}.{date_msg}"
            )
        return f"Failed to send rec command to {agent_id}."
