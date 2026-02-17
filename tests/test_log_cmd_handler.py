"""로그 명령 핸들러 테스트."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.core.log_cmd_handler import LogCmdHandler
from shared.protocol import (
    CmdLogPayload,
    CmdLogAckPayload,
    LogAction,
    LogAckStatus,
    LogFileEntry,
    LogFileListPayload,
    LogFileSelectPayload,
    PacketType,
)


class TestLogCmdHandlerHistRequest:
    """2-phase selective transfer tests."""

    async def test_hist_request_sends_file_list(self):
        """Phase 1: HIST_REQUEST -> agent sends LOG_FILE_LIST."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="20260217_app.txt", file_size=1024, md5=b"\xaa" * 16),
            LogFileEntry(
                filename="20260217_error.txt", file_size=512, md5=b"\xbb" * 16
            ),
        ]
        mock_watcher.get_files_metadata.return_value = entries
        mock_watcher.find_files_by_date.return_value = [
            "/logs/20260217_app.txt",
            "/logs/20260217_error.txt",
        ]
        mock_watcher.read_history.side_effect = [b"data1", b"data2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        # File list sent
        mock_client.send_log_file_list.assert_called_once_with(entries)
        assert not task.done()

        # Phase 2: select 1 file
        select = LogFileSelectPayload(filenames=["20260217_app.txt"]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        assert mock_client.send_log_history.call_count == 1
        assert mock_client.send_log_history.call_args[0][0] == "20260217_app.txt"

    async def test_hist_request_no_files_sends_empty_list(self):
        """No files -> send empty list, no wait."""
        mock_watcher = MagicMock()
        mock_watcher.get_files_metadata.return_value = []

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        mock_client.send_log_file_list.assert_called_once_with([])
        mock_client.send_log_history.assert_not_called()

    async def test_hist_request_all_files_selected(self):
        """Server selects all -> agent sends all."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
            LogFileEntry(filename="f2.txt", file_size=200, md5=b"\xbb" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries
        mock_watcher.find_files_by_date.return_value = ["/logs/f1.txt", "/logs/f2.txt"]
        mock_watcher.read_history.side_effect = [b"data1", b"data2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        select = LogFileSelectPayload(filenames=["f1.txt", "f2.txt"]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        assert mock_client.send_log_history.call_count == 2

    async def test_hist_request_empty_selection(self):
        """Server selects nothing -> no LOG_HIST sent."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries
        mock_watcher.find_files_by_date.return_value = ["/logs/f1.txt"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        select = LogFileSelectPayload(filenames=[]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

        mock_client.send_log_history.assert_not_called()

    async def test_hist_request_timeout(self):
        """No FILE_SELECT response -> timeout, no files sent."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries
        mock_watcher.find_files_by_date.return_value = ["/logs/f1.txt"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(
            log_watcher=mock_watcher, tcp_client=mock_client, file_select_timeout=0.1
        )

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        await handler.handle_cmd_log(payload)

        mock_client.send_log_history.assert_not_called()

    async def test_hist_request_file_read_error_continues(self):
        """One file read fails -> continue sending others."""
        mock_watcher = MagicMock()
        entries = [
            LogFileEntry(filename="f1.txt", file_size=100, md5=b"\xaa" * 16),
            LogFileEntry(filename="f2.txt", file_size=200, md5=b"\xbb" * 16),
        ]
        mock_watcher.get_files_metadata.return_value = entries
        mock_watcher.find_files_by_date.return_value = ["/logs/f1.txt", "/logs/f2.txt"]
        mock_watcher.read_history.side_effect = [OSError("read failed"), b"data2"]

        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        payload = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217").pack()
        task = asyncio.create_task(handler.handle_cmd_log(payload))
        await asyncio.sleep(0.05)

        select = LogFileSelectPayload(filenames=["f1.txt", "f2.txt"]).pack()
        await handler.handle_file_select(select)
        await asyncio.wait_for(task, timeout=2.0)

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

        mock_client.send_packet.assert_called_once()
        ack_data = CmdLogAckPayload.unpack(mock_client.send_packet.call_args[0][1])
        assert ack_data.action == LogAction.REAL_START
        assert ack_data.status == LogAckStatus.SUCCESS

    async def test_real_stop_deactivates(self):
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)

        start_payload = CmdLogPayload(action=LogAction.REAL_START, date="").pack()
        await handler.handle_cmd_log(start_payload)
        assert handler.is_realtime_active

        stop_payload = CmdLogPayload(action=LogAction.REAL_STOP, date="").pack()
        await handler.handle_cmd_log(stop_payload)
        assert not handler.is_realtime_active

    async def test_invalid_payload_does_not_crash(self):
        mock_watcher = MagicMock()
        mock_client = AsyncMock()
        handler = LogCmdHandler(log_watcher=mock_watcher, tcp_client=mock_client)
        await handler.handle_cmd_log(b"\x00")
        mock_client.send_packet.assert_not_called()
