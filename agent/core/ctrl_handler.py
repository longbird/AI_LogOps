"""제어 명령 핸들러. CMD_CTRL 수신 시 target별 처리."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.protocol import (
    CmdCtrlAckPayload,
    CmdCtrlPayload,
    CtrlAckStatus,
    CtrlAction,
    DeployTarget,
    PacketType,
)
from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.process_mgr import ProcessManager
    from agent.core.tcp_client import TCPClient

logger = setup_logging("ctrl_handler")


class CtrlHandler:
    """CMD_CTRL 패킷 처리: target(PROCESS/REC_CLIENT)별 RESTART/STOP/START."""

    def __init__(
        self,
        tcp_client: TCPClient,
        process_mgrs: dict[str, ProcessManager],
        process_configs: dict[str, dict],
        rec_client_mgr: ProcessManager | None = None,
        rec_client_args: list[str] | None = None,
    ) -> None:
        self._client = tcp_client
        self._process_mgrs = process_mgrs
        self._process_configs = process_configs
        self._rec_client_mgr = rec_client_mgr
        self._rec_client_args = rec_client_args

    def _resolve_mgr(
        self, target: int, target_name: str = "",
    ) -> tuple[ProcessManager | None, list[str] | None, str]:
        """target 값에 따라 적절한 ProcessManager 반환."""
        if target == DeployTarget.REC_CLIENT:
            return self._rec_client_mgr, self._rec_client_args, "rec_client"
        # PROCESS: resolve by target_name
        if target_name and target_name in self._process_mgrs:
            mgr = self._process_mgrs[target_name]
            args = self._process_configs.get(target_name, {}).get("args")
            return mgr, args, target_name
        # Fallback: first process manager
        if self._process_mgrs:
            first_name = next(iter(self._process_mgrs))
            mgr = self._process_mgrs[first_name]
            args = self._process_configs.get(first_name, {}).get("args")
            return mgr, args, first_name
        return None, None, "process"

    async def handle_cmd_ctrl(self, payload_data: bytes) -> None:
        """CMD_CTRL 패킷 수신 콜백."""
        try:
            cmd = CmdCtrlPayload.unpack(payload_data)
        except ValueError:
            logger.warning("invalid CMD_CTRL payload")
            return

        mgr, args, label = self._resolve_mgr(cmd.target, cmd.target_name)
        if mgr is None:
            logger.warning(
                "no process manager for target=%d (%s), ignoring %s",
                cmd.target, label, cmd.action.name,
            )
            await self._send_ack(cmd.action, CtrlAckStatus.FAILED, 0)
            return

        logger.info(
            "CMD_CTRL received: action=%s target=%s(%d) process=%s",
            cmd.action.name, label, cmd.target, mgr.process_name,
        )

        if cmd.action == CtrlAction.RESTART:
            await self._handle_restart(mgr, args, cmd.action)
        elif cmd.action == CtrlAction.STOP:
            await self._handle_stop(mgr, cmd.action)
        elif cmd.action == CtrlAction.START:
            await self._handle_start(mgr, args, cmd.action)
        else:
            logger.warning("unknown ctrl action: %s", cmd.action)

    async def _handle_restart(
        self, mgr: ProcessManager, args: list[str] | None, action: CtrlAction,
    ) -> None:
        """대상 프로세스 재시작."""
        killed = mgr.kill_all()
        if not killed:
            logger.warning("failed to kill process, attempting start anyway")

        try:
            pid = mgr.start(args=args)
            logger.info("process restarted: pid=%d", pid)
            await self._send_ack(action, CtrlAckStatus.SUCCESS, pid)
        except Exception:
            logger.exception("failed to restart process")
            await self._send_ack(action, CtrlAckStatus.FAILED, 0)

    async def _handle_stop(self, mgr: ProcessManager, action: CtrlAction) -> None:
        """대상 프로세스 정지."""
        killed = mgr.kill_all()
        status = CtrlAckStatus.SUCCESS if killed else CtrlAckStatus.FAILED
        await self._send_ack(action, status, 0)

    async def _handle_start(
        self, mgr: ProcessManager, args: list[str] | None, action: CtrlAction,
    ) -> None:
        """대상 프로세스 시작."""
        try:
            pid = mgr.start(args=args)
            logger.info("process started: pid=%d", pid)
            await self._send_ack(action, CtrlAckStatus.SUCCESS, pid)
        except Exception:
            logger.exception("failed to start process")
            await self._send_ack(action, CtrlAckStatus.FAILED, 0)

    async def _send_ack(
        self, action: CtrlAction, status: CtrlAckStatus, pid: int
    ) -> None:
        ack = CmdCtrlAckPayload(action=action, pid=pid, status=status)
        await self._client.send_packet(PacketType.CMD_CTRL_ACK, ack.pack())
