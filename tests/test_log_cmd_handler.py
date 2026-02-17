"""로그 명령 핸들러 테스트."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.log_cmd_handler import LogCmdHandler
from shared.protocol import (
    CmdLogPayload,
    CmdLogAckPayload,
    LogAction,
    LogAckStatus,
    PacketType,
)


class TestLogCmdHandlerHistRequest:
    async def test_hist_request_sends_matching_files(self):
        mock_watcher = MagicMock()
        mock_watcher.find_files_by_date.return_value = [
            "/logs/20260217_app.txt",
            "/logs/20260217_error.txt",
        ]
        mock_watcher.read_history.side_effect = [b"log data 1", b"log data 2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        # ACK with file_count=2 sent via send_packet
        assert mock_client.send_packet.call_count >= 1
        # Verify ACK payload
        ack_call = mock_client.send_packet.call_args_list[0]
        assert ack_call[0][0] == PacketType.CMD_LOG_ACK
        ack_data = CmdLogAckPayload.unpack(ack_call[0][1])
        assert ack_data.action == LogAction.HIST_REQUEST
        assert ack_data.status == LogAckStatus.SUCCESS
        assert ack_data.file_count == 2
        # 2 LOG_HIST packets sent
        assert mock_client.send_log_history.call_count == 2

    async def test_hist_request_no_files_found(self):
        mock_watcher = MagicMock()
        mock_watcher.find_files_by_date.return_value = []

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        # ACK with file_count=0
        mock_client.send_packet.assert_called_once()
        ack_data = CmdLogAckPayload.unpack(mock_client.send_packet.call_args[0][1])
        assert ack_data.file_count == 0
        mock_client.send_log_history.assert_not_called()

    async def test_hist_request_file_read_error_continues(self):
        mock_watcher = MagicMock()
        mock_watcher.find_files_by_date.return_value = [
            "/logs/file1.txt",
            "/logs/file2.txt",
        ]
        mock_watcher.read_history.side_effect = [OSError("read failed"), b"data2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        # Should still send the second file
        assert mock_client.send_log_history.call_count == 1


class TestLogCmdHandlerRealtime:
    async def test_real_start_activates(self):
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        assert not handler.is_realtime_active
        payload = CmdLogPayload(action=LogAction.REAL_START, date="").pack()
        await handler.handle_cmd_log(payload)
        assert handler.is_realtime_active

        # ACK sent
        mock_client.send_packet.assert_called_once()
        ack_data = CmdLogAckPayload.unpack(mock_client.send_packet.call_args[0][1])
        assert ack_data.action == LogAction.REAL_START
        assert ack_data.status == LogAckStatus.SUCCESS

    async def test_real_stop_deactivates(self):
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        # Start first
        start_payload = CmdLogPayload(action=LogAction.REAL_START, date="").pack()
        await handler.handle_cmd_log(start_payload)
        assert handler.is_realtime_active

        # Stop
        stop_payload = CmdLogPayload(action=LogAction.REAL_STOP, date="").pack()
        await handler.handle_cmd_log(stop_payload)
        assert not handler.is_realtime_active

    async def test_invalid_payload_does_not_crash(self):
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)
        # Invalid payload (too short)
        await handler.handle_cmd_log(b"\x00")
        # Should not raise, no calls made
        mock_client.send_packet.assert_not_called()
