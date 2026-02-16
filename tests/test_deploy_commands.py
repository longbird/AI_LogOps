from __future__ import annotations

# pyright: reportMissingImports=false, reportArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from pathlib import Path
from typing import Callable, cast
from unittest.mock import AsyncMock, Mock

import pytest

from server.telegram.handler import ParsedCommand
from shared.models import AgentInfo, AgentSession, AgentState
from shared.protocol import CmdCtrlPayload, CtrlAction, HEADER_SIZE, Packet, PacketType


class SessionManagerStub:
    def __init__(self, resolver: Callable[[str], AgentSession | None]) -> None:
        self._resolver: Callable[[str], AgentSession | None] = resolver

    def get_session(self, agent_id: str) -> AgentSession | None:
        return self._resolver(agent_id)


class RegisterRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def register(self, command: str, handler: object) -> None:
        self.calls.append((command, handler))


def _make_session(agent_id: str, writer: object | None = None) -> AgentSession:
    return AgentSession(
        agent_info=AgentInfo(
            agent_id=agent_id,
            version="1.0.0",
            state=AgentState.CONNECTED,
        ),
        session_id="session-1",
        writer=writer,
    )


@pytest.mark.asyncio
class TestDeployCommandHandler:
    async def test_cmd_deploy_success(self, tmp_path: Path) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        report_dir = tmp_path / "reports" / "ai_pipeline"
        report_dir.mkdir(parents=True, exist_ok=True)
        deploy_file = report_dir / "Fixed_Source_Code.py"
        _ = deploy_file.write_text("print('ok')\n", encoding="utf-8")

        tcp_server = AsyncMock()
        send_deploy_mock: AsyncMock = AsyncMock(return_value=True)
        tcp_server.send_deploy = send_deploy_mock
        session_mgr = SessionManagerStub(
            resolver=lambda _: _make_session("agent-1", writer=object())
        )
        deploy_handler = DeployCommandHandler(
            tcp_server=tcp_server,
            session_mgr=session_mgr,
            storage_dir=str(tmp_path),
        )
        cmd = ParsedCommand(
            command="deploy",
            args=["agent-1"],
            chat_id=101,
            raw_text="/deploy agent-1",
        )

        response = await deploy_handler.cmd_deploy(cmd)

        assert response == "Deploy initiated for agent-1: Fixed_Source_Code.py"
        send_deploy_mock.assert_awaited_once_with("agent-1", str(deploy_file))

    async def test_cmd_deploy_no_args(self) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        deploy_handler = DeployCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=SessionManagerStub(resolver=lambda _: None),
        )
        cmd = ParsedCommand(
            command="deploy",
            args=[],
            chat_id=101,
            raw_text="/deploy",
        )

        response = await deploy_handler.cmd_deploy(cmd)

        assert response == "Usage: /deploy <agent_id> [filename]"

    async def test_cmd_deploy_agent_not_connected(self) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        deploy_handler = DeployCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=SessionManagerStub(resolver=lambda _: None),
        )
        cmd = ParsedCommand(
            command="deploy",
            args=["missing-agent"],
            chat_id=101,
            raw_text="/deploy missing-agent",
        )

        response = await deploy_handler.cmd_deploy(cmd)

        assert response == "Agent missing-agent is not connected."

    async def test_cmd_deploy_no_file(self, tmp_path: Path) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        deploy_handler = DeployCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=SessionManagerStub(
                resolver=lambda _: _make_session("agent-1", writer=object())
            ),
            storage_dir=str(tmp_path),
        )
        cmd = ParsedCommand(
            command="deploy",
            args=["agent-1"],
            chat_id=101,
            raw_text="/deploy agent-1",
        )

        response = await deploy_handler.cmd_deploy(cmd)

        assert response == "No deployable file found for agent-1."

    async def test_cmd_rollback_success(self) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        writer = Mock()
        write_mock: Mock = Mock()
        drain_mock: AsyncMock = AsyncMock()
        writer.write = write_mock
        writer.drain = drain_mock
        session_mgr = SessionManagerStub(
            resolver=lambda _: _make_session("agent-1", writer=writer)
        )

        deploy_handler = DeployCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=session_mgr,
        )
        cmd = ParsedCommand(
            command="rollback",
            args=["agent-1"],
            chat_id=101,
            raw_text="/rollback agent-1",
        )

        response = await deploy_handler.cmd_rollback(cmd)

        assert response == "Rollback command sent to agent-1."
        write_mock.assert_called_once()
        drain_mock.assert_awaited_once()

        packet = cast(bytes, write_mock.call_args.args[0])
        packet_type, payload_len = Packet.parse_header(packet[:HEADER_SIZE])
        payload = packet[HEADER_SIZE:]
        ctrl = CmdCtrlPayload.unpack(payload)
        assert packet_type == PacketType.CMD_CTRL
        assert payload_len == 1
        assert ctrl.action == CtrlAction.RESTART

    async def test_cmd_rollback_no_args(self) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        deploy_handler = DeployCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=SessionManagerStub(resolver=lambda _: None),
        )
        cmd = ParsedCommand(
            command="rollback",
            args=[],
            chat_id=101,
            raw_text="/rollback",
        )

        response = await deploy_handler.cmd_rollback(cmd)

        assert response == "Usage: /rollback <agent_id>"

    async def test_cmd_rollback_agent_not_connected(self) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        deploy_handler = DeployCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=SessionManagerStub(resolver=lambda _: None),
        )
        cmd = ParsedCommand(
            command="rollback",
            args=["missing-agent"],
            chat_id=101,
            raw_text="/rollback missing-agent",
        )

        response = await deploy_handler.cmd_rollback(cmd)

        assert response == "Agent missing-agent is not connected."

    async def test_register_all(self) -> None:
        from server.telegram.deploy_commands import DeployCommandHandler

        deploy_handler = DeployCommandHandler(
            tcp_server=AsyncMock(),
            session_mgr=SessionManagerStub(resolver=lambda _: None),
        )
        recorder = RegisterRecorder()

        deploy_handler.register_all(recorder)

        registered_commands = {command for command, _ in recorder.calls}
        assert registered_commands == {"deploy", "rollback"}
