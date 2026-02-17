"""텔레그램 로그 전송 명령 핸들러."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.telegram.handler import ParsedCommand, TelegramHandler
from shared.protocol import LogAction
from shared.utils import setup_logging

if TYPE_CHECKING:
    from server.core.session_mgr import SessionManager
    from server.core.tcp_server import TCPServer


class LogCommandHandler:
    """로그 전송 관련 텔레그램 명령."""

    def __init__(self, tcp_server: TCPServer, session_mgr: SessionManager):
        self.tcp_server: TCPServer = tcp_server
        self.session_mgr: SessionManager = session_mgr
        self._logger: logging.Logger = setup_logging("log_commands")

    def register_all(self, handler: TelegramHandler) -> None:
        handler.register("log_hist", self.cmd_log_hist)
        handler.register("log_real", self.cmd_log_real)

    async def cmd_log_hist(self, cmd: ParsedCommand) -> str:
        """/log_hist <agent_id> <YYYYMMDD>"""
        if len(cmd.args) < 2:
            return "Usage: /log_hist <agent_id> <YYYYMMDD>"

        agent_id = cmd.args[0]
        date_str = cmd.args[1]

        if len(date_str) != 8 or not date_str.isdigit():
            return "Date must be YYYYMMDD format (e.g., 20260217)."

        session = self.session_mgr.get_session(agent_id)
        if session is None:
            return f"Agent {agent_id} is not connected."

        success = await self.tcp_server.send_log_command(
            agent_id, LogAction.HIST_REQUEST, date_str
        )
        if success:
            return (
                f"Log history request sent to {agent_id} for {date_str}. "
                f"Server will request file list, compare with stored files, "
                f"and transfer only new/changed files."
            )
        return f"Failed to send log command to {agent_id}."

    async def cmd_log_real(self, cmd: ParsedCommand) -> str:
        """/log_real <agent_id> <start|stop>"""
        if len(cmd.args) < 2:
            return "Usage: /log_real <agent_id> <start|stop>"

        agent_id = cmd.args[0]
        action_str = cmd.args[1].lower()

        session = self.session_mgr.get_session(agent_id)
        if session is None:
            return f"Agent {agent_id} is not connected."

        if action_str == "start":
            action = LogAction.REAL_START
        elif action_str == "stop":
            action = LogAction.REAL_STOP
        else:
            return "Action must be 'start' or 'stop'."

        success = await self.tcp_server.send_log_command(agent_id, action, "")
        if success:
            return f"Realtime log {action_str} command sent to {agent_id}."
        return f"Failed to send log command to {agent_id}."
