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
    from agent.core.process_monitor import ProcessMonitorLoop
    from agent.core.tcp_client import TCPClient
    from agent.updater.process_deploy import ProcessDeployer

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
        process_deployers: dict[str, ProcessDeployer] | None = None,
        process_monitors: dict[str, ProcessMonitorLoop] | None = None,
    ) -> None:
        self._client = tcp_client
        self._process_mgrs = process_mgrs
        self._process_configs = process_configs
        self._rec_client_mgr = rec_client_mgr
        self._rec_client_args = rec_client_args
        self._process_deployers = process_deployers or {}
        self._process_monitors = process_monitors or {}

    def _resolve_mgr(
        self, target: int, target_name: str = "",
    ) -> tuple[ProcessManager | None, list[str] | None, str]:
        """target 값에 따라 적절한 ProcessManager 반환."""
        if target == DeployTarget.REC_CLIENT:
            return self._rec_client_mgr, self._rec_client_args, "rec_client"
        # PROCESS: resolve by target_name (exact match required)
        if target_name and target_name in self._process_mgrs:
            mgr = self._process_mgrs[target_name]
            args = self._process_configs.get(target_name, {}).get("args")
            return mgr, args, target_name
        # target_name 미지정 또는 미매칭: 단일 프로세스일 때만 허용
        if not target_name and len(self._process_mgrs) == 1:
            first_name = next(iter(self._process_mgrs))
            mgr = self._process_mgrs[first_name]
            args = self._process_configs.get(first_name, {}).get("args")
            return mgr, args, first_name
        # 다중 프로세스인데 target_name 미지정 또는 미매칭 → 에러
        if target_name:
            logger.error(
                "target_name '%s' not found in process_mgrs (available: %s)",
                target_name, list(self._process_mgrs.keys()),
            )
        else:
            logger.error(
                "target_name required for multi-process config (available: %s)",
                list(self._process_mgrs.keys()),
            )
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
            await self._handle_restart(mgr, args, cmd.action, label)
        elif cmd.action == CtrlAction.STOP:
            await self._handle_stop(mgr, cmd.action)
        elif cmd.action == CtrlAction.START:
            await self._handle_start(mgr, args, cmd.action)
        elif cmd.action == CtrlAction.BACKUP:
            await self._handle_backup(mgr, cmd.action, label)
        elif cmd.action == CtrlAction.ROLLBACK:
            await self._handle_rollback(mgr, args, cmd.action, label)
        else:
            logger.warning("unknown ctrl action: %s", cmd.action)

    async def _handle_restart(
        self,
        mgr: ProcessManager,
        args: list[str] | None,
        action: CtrlAction,
        label: str = "",
    ) -> None:
        """대상 프로세스 재시작. 스테이징 파일이 있으면 deployer로 배포 후 시작."""
        deployer = self._process_deployers.get(label)
        if deployer and deployer.has_staged_files:
            logger.info("staged update found, using deployer for %s", label)
            try:
                result = await deployer.execute_deploy(process_args=args)
                if result.success:
                    logger.info("process deployed and restarted: pid=%d", result.pid)
                    await self._send_ack(action, CtrlAckStatus.SUCCESS, result.pid)
                else:
                    logger.error("deploy failed for %s: %s", label, result.error)
                    await self._send_ack(action, CtrlAckStatus.FAILED, 0)
            except Exception:
                logger.exception("deploy-restart failed for %s", label)
                await self._send_ack(action, CtrlAckStatus.FAILED, 0)
            return

        # No staged files: bare restart
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
        """대상 프로세스 정지. auto_restart를 일시정지하여 재시작 방지."""
        monitor = self._process_monitors.get(mgr.process_name)
        if monitor:
            monitor.pause_auto_restart()
        killed = mgr.kill_all()
        status = CtrlAckStatus.SUCCESS if killed else CtrlAckStatus.FAILED
        await self._send_ack(action, status, 0)

    async def _handle_start(
        self, mgr: ProcessManager, args: list[str] | None, action: CtrlAction,
    ) -> None:
        """대상 프로세스 시작. STOP으로 일시정지된 auto_restart를 재개."""
        try:
            pid = mgr.start(args=args)
            logger.info("process started: pid=%d", pid)
            monitor = self._process_monitors.get(mgr.process_name)
            if monitor:
                monitor.resume_auto_restart()
            await self._send_ack(action, CtrlAckStatus.SUCCESS, pid)
        except Exception:
            logger.exception("failed to start process")
            await self._send_ack(action, CtrlAckStatus.FAILED, 0)

    async def _handle_backup(
        self, mgr: ProcessManager, action: CtrlAction, label: str = "",
    ) -> None:
        """대상 프로세스 파일 백업."""
        try:
            backup_path = mgr.backup_current()
            logger.info("backup created for %s: %s", label, backup_path)
            await self._send_ack(action, CtrlAckStatus.SUCCESS, 0)
        except FileNotFoundError:
            logger.warning("backup target not found for %s", label)
            await self._send_ack(action, CtrlAckStatus.FAILED, 0)
        except Exception:
            logger.exception("backup failed for %s", label)
            await self._send_ack(action, CtrlAckStatus.FAILED, 0)

    async def _handle_rollback(
        self,
        mgr: ProcessManager,
        args: list[str] | None,
        action: CtrlAction,
        label: str = "",
    ) -> None:
        """최신 백업으로 롤백 후 재시작."""
        mgr.kill_all()
        if not mgr.rollback():
            logger.error("rollback failed for %s: no backup available", label)
            await self._send_ack(action, CtrlAckStatus.FAILED, 0)
            return
        try:
            pid = mgr.start(args=args)
            logger.info("rollback + restart for %s: pid=%d", label, pid)
            await self._send_ack(action, CtrlAckStatus.SUCCESS, pid)
        except Exception:
            logger.exception("rollback ok but restart failed for %s", label)
            await self._send_ack(action, CtrlAckStatus.FAILED, 0)

    async def _send_ack(
        self, action: CtrlAction, status: CtrlAckStatus, pid: int
    ) -> None:
        ack = CmdCtrlAckPayload(action=action, pid=pid, status=status)
        await self._client.send_packet(PacketType.CMD_CTRL_ACK, ack.pack())
