from __future__ import annotations

import shutil
from logging import Logger

from agent.core.file_transfer import FileTransferReceiver
from agent.core.process_mgr import ProcessManager
from shared.protocol import (
    CmdCtrlAckPayload,
    CmdDeployPayload,
    CtrlAckStatus,
    CtrlAction,
    FileAckPayload,
    FileChunkPayload,
    PacketType,
)
from shared.utils import setup_logging


class DeployHandler:
    """Agent 측 배포 핸들러. 파일 수신 → 프로세스 교체 → 검증."""

    def __init__(
        self,
        tcp_client: TCPClient,
        process_mgr: ProcessManager,
        transfer_dir: str,
    ):
        self.tcp_client: TCPClient = tcp_client
        self.process_mgr: ProcessManager = process_mgr
        self.receiver: FileTransferReceiver = FileTransferReceiver(
            target_dir=transfer_dir
        )
        self._logger: Logger = setup_logging("deploy_handler")

    async def handle_cmd_deploy(self, payload_data: bytes) -> None:
        """CMD_DEPLOY 수신 처리. 수신 상태 초기화."""
        cmd = CmdDeployPayload.unpack(payload_data)
        self.receiver.start_receive(cmd)
        self._logger.info(
            "deploy started: filename=%s size=%d", cmd.filename, cmd.file_size
        )

    async def handle_file_chunk(self, payload_data: bytes) -> None:
        """FILE_CHUNK 수신 → FILE_ACK 응답."""
        chunk = FileChunkPayload.unpack(payload_data)
        success = self.receiver.receive_chunk(chunk)
        ack = FileAckPayload(seq_num=chunk.seq_num, status=0 if success else 1)
        await self.tcp_client.send_packet(PacketType.FILE_ACK, ack.pack())

        if self.receiver.is_complete():
            await self._execute_deploy()

    async def _execute_deploy(self) -> None:
        """파일 조립 → 프로세스 교체 → 헬스체크."""
        file_path = self.receiver.assemble()
        if file_path is None:
            self._logger.error("file assembly failed (SHA-256 mismatch)")
            await self._send_deploy_result(success=False)
            return

        try:
            _ = self.process_mgr.backup_current()
        except FileNotFoundError:
            self._logger.warning("no existing file to backup, proceeding")

        _ = self.process_mgr.kill()
        _ = shutil.copy2(str(file_path), str(self.process_mgr.process_path))
        pid = self.process_mgr.start()
        self._logger.info("process restarted: pid=%d", pid)

        healthy = await self.process_mgr.health_check(timeout=30)
        if healthy:
            self._logger.info("deploy verified successfully")
            await self._send_deploy_result(success=True, pid=pid)
            return

        self._logger.warning("health check failed, rolling back")
        _ = self.process_mgr.kill()
        _ = self.process_mgr.rollback()
        rollback_pid = self.process_mgr.start()
        self._logger.info("rollback complete: pid=%d", rollback_pid)
        await self._send_deploy_result(success=False)

    async def _send_deploy_result(self, success: bool, pid: int = 0) -> None:
        """CMD_CTRL_ACK로 배포 결과 전송."""
        status = (
            CtrlAckStatus.DEPLOY_VERIFIED if success else CtrlAckStatus.DEPLOY_ROLLBACK
        )
        ack = CmdCtrlAckPayload(action=CtrlAction.RESTART, pid=pid, status=status)
        await self.tcp_client.send_packet(PacketType.CMD_CTRL_ACK, ack.pack())


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.tcp_client import TCPClient
