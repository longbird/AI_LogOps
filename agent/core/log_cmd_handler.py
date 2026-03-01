"""로그 전송 명령 핸들러. CMD_LOG 수신 시 처리."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from shared.protocol import (
    CmdLogAckPayload,
    CmdLogPayload,
    LogAckStatus,
    LogAction,
    LogFileSelectPayload,
    PacketType,
)
from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.log_watcher import LogWatcher
    from agent.core.tcp_client import TCPClient

logger = setup_logging("log_cmd_handler")


class LogCmdHandler:
    """CMD_LOG 패킷 처리: HIST_REQUEST / REAL_START / REAL_STOP."""

    def __init__(
        self,
        log_watcher: LogWatcher,
        tcp_client: TCPClient,
        history_max_mb: int = 10,
        file_select_timeout: float = 30.0,
    ):
        self._watcher = log_watcher
        self._client = tcp_client
        self._history_max_mb = history_max_mb
        self._file_select_timeout = file_select_timeout
        self._realtime_active = False

        # Phase 2 state
        self._file_select_event: asyncio.Event = asyncio.Event()
        self._selected_filenames: list[str] = []
        self._pending_file_map: dict[str, str] = {}

    @property
    def is_realtime_active(self) -> bool:
        return self._realtime_active

    async def handle_cmd_log(self, payload_data: bytes) -> None:
        """CMD_LOG 패킷 수신 콜백."""
        try:
            cmd = CmdLogPayload.unpack(payload_data)
        except ValueError:
            logger.warning("invalid CMD_LOG payload")
            return

        if cmd.action == LogAction.HIST_REQUEST:
            await self._handle_hist_request(cmd.date, cmd.folder_index)
        elif cmd.action == LogAction.REAL_START:
            await self._handle_real_start()
        elif cmd.action == LogAction.REAL_STOP:
            await self._handle_real_stop()
        else:
            logger.warning("unknown log action: %s", cmd.action)

    async def handle_file_select(self, payload_data: bytes) -> None:
        """LOG_FILE_SELECT 수신 콜백. tcp_client.on_log_file_select에 바인딩."""
        try:
            select = LogFileSelectPayload.unpack(payload_data)
        except ValueError:
            logger.warning("invalid LOG_FILE_SELECT payload")
            return
        self._selected_filenames = select.filenames
        self._file_select_event.set()

    async def _send_ack(
        self, action: LogAction, status: LogAckStatus, file_count: int = 0
    ) -> None:
        ack = CmdLogAckPayload(action=action, status=status, file_count=file_count)
        await self._client.send_packet(PacketType.CMD_LOG_ACK, ack.pack())

    async def _handle_hist_request(
        self, date_str: str, folder_index: int = -1
    ) -> None:
        """2-Phase selective transfer:
        Phase 1: Collect file metadata → send LOG_FILE_LIST
        Phase 2: Wait for LOG_FILE_SELECT → send only selected files
        """
        # Phase 1: Send file list (폴더 필터링)
        entries = self._watcher.get_files_metadata_by_folder(date_str, folder_index)
        await self._client.send_log_file_list(entries)

        if not entries:
            logger.info(
                "no files found for date=%s folder_index=%d", date_str, folder_index
            )
            return

        # Build lookup map (폴더 필터링)
        files = self._watcher.find_files_by_date_and_folder(date_str, folder_index)
        self._pending_file_map = {Path(f).name: f for f in files}

        # Phase 2: Wait for selection
        self._file_select_event.clear()
        self._selected_filenames = []

        try:
            await asyncio.wait_for(
                self._file_select_event.wait(),
                timeout=self._file_select_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("file select timeout for date=%s", date_str)
            self._pending_file_map.clear()
            return

        # Send selected files
        sent_count = 0
        for filename in self._selected_filenames:
            filepath = self._pending_file_map.get(filename)
            if filepath is None:
                logger.warning("selected file not found: %s", filename)
                continue
            try:
                data = self._watcher.read_history(filepath, max_mb=self._history_max_mb)
                await self._client.send_log_history(filename, data)
                sent_count += 1
                logger.info("sent log history: %s (%d bytes)", filename, len(data))
            except Exception:
                logger.exception("failed to send log history: %s", filepath)

        await self._send_ack(LogAction.HIST_REQUEST, LogAckStatus.SUCCESS, sent_count)
        self._pending_file_map.clear()

    async def _handle_real_start(self) -> None:
        self._realtime_active = True
        logger.info("realtime log transmission started")
        await self._send_ack(LogAction.REAL_START, LogAckStatus.SUCCESS, 0)

        # 진단용: 감시 폴더 상태 + 테스트 라인 전송
        watch_dirs = [str(d) for d in self._watcher._watch_dirs]
        watchable = self._watcher.get_watchable_files()
        latest = self._watcher.get_latest_files_by_dir()
        diag = (
            f"[진단] watch_dirs={watch_dirs}, "
            f"watchable_files={len(watchable)}, "
            f"latest_by_dir={[(d, Path(f).name) for d, f in latest]}"
        )
        logger.info(diag)
        try:
            from shared.protocol import LogRealPayload

            await self._client.send_packet(
                PacketType.LOG_REAL,
                LogRealPayload(filename="__diag__", line=diag).pack(),
            )
        except Exception:
            logger.warning("failed to send diagnostic line")

    async def _handle_real_stop(self) -> None:
        self._realtime_active = False
        logger.info("realtime log transmission stopped")
        await self._send_ack(LogAction.REAL_STOP, LogAckStatus.SUCCESS, 0)
