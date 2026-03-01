"""제어 명령 핸들러. CMD_CTRL 수신 시 처리."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.protocol import (
    CmdCtrlAckPayload,
    CmdCtrlPayload,
    CtrlAckStatus,
    CtrlAction,
    PacketType,
)
from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.process_mgr import ProcessManager
    from agent.core.tcp_client import TCPClient

logger = setup_logging("ctrl_handler")


class CtrlHandler:
    """CMD_CTRL 패킷 처리: RESTART / STOP / START."""

    def __init__(
        self,
        tcp_client: TCPClient,
        process_mgr: ProcessManager,
        process_args: list[str] | None = None,
    ) -> None:
        self._client = tcp_client
        self._process_mgr = process_mgr
        self._process_args = process_args

    async def handle_cmd_ctrl(self, payload_data: bytes) -> None:
        """CMD_CTRL 패킷 수신 콜백."""
        try:
            cmd = CmdCtrlPayload.unpack(payload_data)
        except ValueError:
            logger.warning("invalid CMD_CTRL payload")
            return

        if cmd.action == CtrlAction.RESTART:
            await self._handle_restart()
        elif cmd.action == CtrlAction.STOP:
            await self._handle_stop()
        elif cmd.action == CtrlAction.START:
            await self._handle_start()
        else:
            logger.warning("unknown ctrl action: %s", cmd.action)

    async def _handle_restart(self) -> None:
        """대상 프로세스 재시작."""
        logger.info("restart command received, restarting process: %s",
                     self._process_mgr.process_name)

        killed = self._process_mgr.kill_all()
        if not killed:
            logger.warning("failed to kill process, attempting start anyway")

        pid = 0
        try:
            pid = self._process_mgr.start(args=self._process_args)
            logger.info("process restarted: pid=%d", pid)
            await self._send_ack(CtrlAction.RESTART, CtrlAckStatus.SUCCESS, pid)
        except Exception:
            logger.exception("failed to restart process")
            await self._send_ack(CtrlAction.RESTART, CtrlAckStatus.FAILED, 0)

    async def _handle_stop(self) -> None:
        """대상 프로세스 정지."""
        logger.info("stop command received")
        killed = self._process_mgr.kill_all()
        status = CtrlAckStatus.SUCCESS if killed else CtrlAckStatus.FAILED
        await self._send_ack(CtrlAction.STOP, status, 0)

    async def _handle_start(self) -> None:
        """대상 프로세스 시작."""
        logger.info("start command received")
        try:
            pid = self._process_mgr.start(args=self._process_args)
            logger.info("process started: pid=%d", pid)
            await self._send_ack(CtrlAction.START, CtrlAckStatus.SUCCESS, pid)
        except Exception:
            logger.exception("failed to start process")
            await self._send_ack(CtrlAction.START, CtrlAckStatus.FAILED, 0)

    async def _send_ack(
        self, action: CtrlAction, status: CtrlAckStatus, pid: int
    ) -> None:
        ack = CmdCtrlAckPayload(action=action, pid=pid, status=status)
        await self._client.send_packet(PacketType.CMD_CTRL_ACK, ack.pack())
