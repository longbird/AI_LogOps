from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownMemberType=false

import logging
from pathlib import Path
from typing import Protocol, cast

from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
from server.telegram.handler import ParsedCommand, TelegramHandler
from shared.protocol import CmdCtrlPayload, CtrlAction, Packet, PacketType
from shared.utils import setup_logging


class DeployCommandHandler:
    """배포 관련 텔레그램 명령 핸들러."""

    def __init__(
        self,
        tcp_server: TCPServer,
        session_mgr: SessionManager,
        storage_dir: str = "./storage",
    ):
        self.tcp_server: TCPServer = tcp_server
        self.session_mgr: SessionManager = session_mgr
        self.storage_dir: Path = Path(storage_dir)
        self._logger: logging.Logger = setup_logging("deploy_commands")

    def register_all(self, handler: TelegramHandler) -> None:
        handler.register("deploy", self.cmd_deploy)
        handler.register("rollback", self.cmd_rollback)

    async def cmd_deploy(self, cmd: ParsedCommand) -> str:
        """
        /deploy <agent_id> [filename]
        수정된 파일을 에이전트에 배포.
        """
        if not cmd.args:
            return "Usage: /deploy <agent_id> [filename]"

        agent_id = cmd.args[0]
        session = self.session_mgr.get_session(agent_id)
        if session is None:
            return f"Agent {agent_id} is not connected."

        filename = cmd.args[1] if len(cmd.args) > 1 else None
        file_path = self._find_deploy_file(agent_id, filename)
        if file_path is None:
            return f"No deployable file found for {agent_id}."

        success = await self.tcp_server.send_deploy(agent_id, str(file_path))
        if success:
            return f"Deploy initiated for {agent_id}: {file_path.name}"
        return f"Deploy failed for {agent_id}. Check connection."

    async def cmd_rollback(self, cmd: ParsedCommand) -> str:
        """
        /rollback <agent_id>
        에이전트에 롤백 명령 전송 (CMD_CTRL RESTART with rollback flag).
        """
        if not cmd.args:
            return "Usage: /rollback <agent_id>"

        agent_id = cmd.args[0]
        session = self.session_mgr.get_session(agent_id)
        if session is None:
            return f"Agent {agent_id} is not connected."

        writer = session.writer
        if writer is None:
            return f"Agent {agent_id} has no active connection."

        ctrl = CmdCtrlPayload(action=CtrlAction.RESTART)
        stream_writer = cast(_WriterLike, writer)
        stream_writer.write(Packet.build(PacketType.CMD_CTRL, ctrl.pack()))
        await stream_writer.drain()
        return f"Rollback command sent to {agent_id}."

    def _find_deploy_file(
        self, agent_id: str, filename: str | None = None
    ) -> Path | None:
        """storage에서 배포할 파일 검색."""
        if filename:
            path = self.storage_dir / "reports" / "ai_pipeline" / filename
            return path if path.exists() else None

        default_path = (
            self.storage_dir / "reports" / "ai_pipeline" / "Fixed_Source_Code.py"
        )
        if default_path.exists():
            return default_path
        self._logger.debug("deploy file not found for agent_id=%s", agent_id)
        return None


class _WriterLike(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...
