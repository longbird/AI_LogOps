"""로그 전송 명령 핸들러. CMD_LOG 수신 시 처리."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shared.protocol import (
    CmdLogAckPayload,
    CmdLogPayload,
    LogAckStatus,
    LogAction,
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
    ):
        self._watcher = log_watcher
        self._client = tcp_client
        self._history_max_mb = history_max_mb
        self._realtime_active = False

    @property
    def is_realtime_active(self) -> bool:
        return self._realtime_active

    async def handle_cmd_log(self, payload_data: bytes) -> None:
        """CMD_LOG 패킷 수신 콜백. tcp_client.on_cmd_log에 바인딩."""
        try:
            cmd = CmdLogPayload.unpack(payload_data)
        except ValueError:
            logger.warning("invalid CMD_LOG payload")
            return

        if cmd.action == LogAction.HIST_REQUEST:
            await self._handle_hist_request(cmd.date)
        elif cmd.action == LogAction.REAL_START:
            await self._handle_real_start()
        elif cmd.action == LogAction.REAL_STOP:
            await self._handle_real_stop()
        else:
            logger.warning("unknown log action: %s", cmd.action)

    async def _send_ack(
        self, action: LogAction, status: LogAckStatus, file_count: int = 0
    ) -> None:
        ack = CmdLogAckPayload(action=action, status=status, file_count=file_count)
        await self._client.send_packet(PacketType.CMD_LOG_ACK, ack.pack())

    async def _handle_hist_request(self, date_str: str) -> None:
        """YYYYMMDD 날짜의 로그 파일 검색 후 전송."""
        files = self._watcher.find_files_by_date(date_str)
        if not files:
            logger.info("no files found for date=%s", date_str)
            await self._send_ack(LogAction.HIST_REQUEST, LogAckStatus.SUCCESS, 0)
            return

        await self._send_ack(LogAction.HIST_REQUEST, LogAckStatus.SUCCESS, len(files))

        for filepath in files:
            try:
                data = self._watcher.read_history(filepath, max_mb=self._history_max_mb)
                filename = Path(filepath).name
                await self._client.send_log_history(filename, data)
                logger.info("sent log history: %s (%d bytes)", filename, len(data))
            except Exception:
                logger.exception("failed to send log history: %s", filepath)

    async def _handle_real_start(self) -> None:
        """실시간 로그 전송 시작."""
        self._realtime_active = True
        logger.info("realtime log transmission started")
        await self._send_ack(LogAction.REAL_START, LogAckStatus.SUCCESS, 0)

    async def _handle_real_stop(self) -> None:
        """실시간 로그 전송 중지."""
        self._realtime_active = False
        logger.info("realtime log transmission stopped")
        await self._send_ack(LogAction.REAL_STOP, LogAckStatus.SUCCESS, 0)
