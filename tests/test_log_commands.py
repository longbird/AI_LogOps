"""서버 텔레그램 로그 명령 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.telegram.log_commands import LogCommandHandler
from server.telegram.handler import ParsedCommand


class TestLogHistCommand:
    async def test_success(self):
        mock_tcp = AsyncMock()
        mock_tcp.send_log_command = AsyncMock(return_value=True)
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(tcp_server=mock_tcp, session_mgr=mock_session_mgr)
        cmd = ParsedCommand(
            command="log_hist",
            args=["PC-01", "20260217"],
            chat_id=123,
            raw_text="/log_hist PC-01 20260217",
        )
        result = await handler.cmd_log_hist(cmd)
        assert "PC-01" in result
        assert "20260217" in result
        mock_tcp.send_log_command.assert_called_once()

    async def test_missing_args(self):
        handler = LogCommandHandler(tcp_server=AsyncMock(), session_mgr=MagicMock())
        cmd = ParsedCommand(
            command="log_hist", args=[], chat_id=123, raw_text="/log_hist"
        )
        result = await handler.cmd_log_hist(cmd)
        assert "Usage" in result

    async def test_invalid_date_format(self):
        handler = LogCommandHandler(tcp_server=AsyncMock(), session_mgr=MagicMock())
        cmd = ParsedCommand(
            command="log_hist",
            args=["PC-01", "2026-02-17"],
            chat_id=123,
            raw_text="/log_hist PC-01 2026-02-17",
        )
        result = await handler.cmd_log_hist(cmd)
        assert "YYYYMMDD" in result

    async def test_agent_not_connected(self):
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = None

        handler = LogCommandHandler(
            tcp_server=AsyncMock(), session_mgr=mock_session_mgr
        )
        cmd = ParsedCommand(
            command="log_hist",
            args=["PC-01", "20260217"],
            chat_id=123,
            raw_text="/log_hist PC-01 20260217",
        )
        result = await handler.cmd_log_hist(cmd)
        assert "not connected" in result.lower()

    async def test_send_fails(self):
        mock_tcp = AsyncMock()
        mock_tcp.send_log_command = AsyncMock(return_value=False)
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(tcp_server=mock_tcp, session_mgr=mock_session_mgr)
        cmd = ParsedCommand(
            command="log_hist",
            args=["PC-01", "20260217"],
            chat_id=123,
            raw_text="/log_hist PC-01 20260217",
        )
        result = await handler.cmd_log_hist(cmd)
        assert "Failed" in result


class TestLogRealCommand:
    async def test_start(self):
        mock_tcp = AsyncMock()
        mock_tcp.send_log_command = AsyncMock(return_value=True)
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(tcp_server=mock_tcp, session_mgr=mock_session_mgr)
        cmd = ParsedCommand(
            command="log_real",
            args=["PC-01", "start"],
            chat_id=123,
            raw_text="/log_real PC-01 start",
        )
        result = await handler.cmd_log_real(cmd)
        assert "PC-01" in result
        assert "start" in result.lower()

    async def test_stop(self):
        mock_tcp = AsyncMock()
        mock_tcp.send_log_command = AsyncMock(return_value=True)
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(tcp_server=mock_tcp, session_mgr=mock_session_mgr)
        cmd = ParsedCommand(
            command="log_real",
            args=["PC-01", "stop"],
            chat_id=123,
            raw_text="/log_real PC-01 stop",
        )
        result = await handler.cmd_log_real(cmd)
        assert "PC-01" in result

    async def test_invalid_action(self):
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_session.return_value = MagicMock()

        handler = LogCommandHandler(
            tcp_server=AsyncMock(), session_mgr=mock_session_mgr
        )
        cmd = ParsedCommand(
            command="log_real",
            args=["PC-01", "pause"],
            chat_id=123,
            raw_text="/log_real PC-01 pause",
        )
        result = await handler.cmd_log_real(cmd)
        assert "start" in result.lower() and "stop" in result.lower()

    async def test_missing_args(self):
        handler = LogCommandHandler(tcp_server=AsyncMock(), session_mgr=MagicMock())
        cmd = ParsedCommand(
            command="log_real", args=["PC-01"], chat_id=123, raw_text="/log_real PC-01"
        )
        result = await handler.cmd_log_real(cmd)
        assert "Usage" in result


class TestLogCommandRegistration:
    def test_register_all(self):
        mock_handler = MagicMock()
        log_handler = LogCommandHandler(tcp_server=AsyncMock(), session_mgr=MagicMock())
        log_handler.register_all(mock_handler)
        assert mock_handler.register.call_count == 2
        registered_cmds = [call[0][0] for call in mock_handler.register.call_args_list]
        assert "log_hist" in registered_cmds
        assert "log_real" in registered_cmds
