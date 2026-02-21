from __future__ import annotations

import asyncio
import shutil
import zipfile
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING

from agent.core.file_transfer import FileTransferReceiver
from agent.core.process_mgr import ProcessManager
from shared.protocol import (
    CmdCtrlAckPayload,
    CmdDeployPayload,
    CtrlAckStatus,
    CtrlAction,
    DeployTarget,
    FileAckPayload,
    FileChunkPayload,
    PacketType,
)
from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.tcp_client import TCPClient
    from agent.updater.process_deploy import ProcessDeployer
    from agent.updater.self_update import SelfUpdater


class DeployHandler:
    """Agent 측 배포 핸들러. 파일 수신 → 프로세스 교체 → 검증.

    단일 exe 배포(ProcessManager) 또는 zip 전체 업데이트(SelfUpdater)를 지원한다.
    """

    def __init__(
        self,
        tcp_client: TCPClient,
        process_mgr: ProcessManager,
        transfer_dir: str,
        updater: SelfUpdater | None = None,
        process_deployer: ProcessDeployer | None = None,
    ):
        self.tcp_client: TCPClient = tcp_client
        self.process_mgr: ProcessManager = process_mgr
        self.updater: SelfUpdater | None = updater
        self.process_deployer: ProcessDeployer | None = process_deployer
        self.receiver: FileTransferReceiver = FileTransferReceiver(
            target_dir=transfer_dir
        )
        self._logger: Logger = setup_logging("deploy_handler")
        self._deploy_target: DeployTarget = DeployTarget.AGENT

    async def handle_cmd_deploy(self, payload_data: bytes) -> None:
        """CMD_DEPLOY 수신 처리. 수신 상태 초기화."""
        cmd = CmdDeployPayload.unpack(payload_data)
        self._deploy_target = cmd.deploy_target
        self.receiver.start_receive(cmd)
        self._logger.info(
            "deploy started: filename=%s size=%d target=%s",
            cmd.filename,
            cmd.file_size,
            self._deploy_target.name,
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
        """파일 조립 → deploy_target에 따라 라우팅."""
        file_path = self.receiver.assemble()
        if file_path is None:
            self._logger.error("file assembly failed (SHA-256 mismatch)")
            await self._send_deploy_result(success=False)
            return

        # ProcessDeployer 라우팅
        if self._deploy_target == DeployTarget.PROCESS:
            await self._execute_process_deploy(file_path)
            return

        # 기본 AGENT 타겟: ZIP 파일이면 SelfUpdater로 전체 업데이트
        if file_path.suffix.lower() == ".zip" and self.updater is not None:
            await self._execute_zip_update(file_path)
            return

        # 기존 단일 exe 배포 로직
        try:
            _ = self.process_mgr.backup_current()
        except FileNotFoundError:
            self._logger.warning("no existing file to backup, proceeding")

        _ = self.process_mgr.kill_all()
        _ = shutil.copy2(str(file_path), str(self.process_mgr.process_path))
        pid = self.process_mgr.start()
        self._logger.info("process restarted: pid=%d", pid)

        healthy = await self.process_mgr.health_check(timeout=30)
        if healthy:
            self._logger.info("deploy verified successfully")
            await self._send_deploy_result(success=True, pid=pid)
            return

        self._logger.warning("health check failed, rolling back")
        _ = self.process_mgr.kill_all()
        _ = self.process_mgr.rollback()
        rollback_pid = self.process_mgr.start()
        self._logger.info("rollback complete: pid=%d", rollback_pid)
        await self._send_deploy_result(success=False)

    async def _execute_zip_update(self, zip_path: Path) -> None:
        """ZIP 파일을 SelfUpdater로 처리하여 전체 업데이트.

        흐름: zip 수신 → 압축 해제 → 배포 결과 전송 → updater.bat 실행 → 프로세스 종료
        """
        updater = self.updater
        if updater is None:
            self._logger.error("updater not configured, cannot process zip")
            await self._send_deploy_result(success=False)
            return

        data = zip_path.read_bytes()
        ok = await updater.receive_zip(data)
        if not ok:
            self._logger.error("zip extraction failed: %s", zip_path)
            await self._send_deploy_result(success=False)
            return

        self._logger.info(
            "zip update ready: %s (%.1f MB), executing self-update...",
            zip_path.name,
            len(data) / (1024 * 1024),
        )

        # 배포 결과를 먼저 전송 (서버에 성공 알림)
        await self._send_deploy_result(success=True)
        # drain 완료 대기 후 업데이트 실행
        await asyncio.sleep(1)
        # updater.bat 생성 + 실행 → sys.exit(0)
        updater.execute_update()

    async def _execute_process_deploy(self, file_path: Path) -> None:
        """ProcessDeployer를 통한 프로세스 배포.

        흐름: zip/exe 수신 → 스테이징 → ProcessDeployer.execute_deploy() → 결과 전송
        """
        deployer = self.process_deployer
        if deployer is None:
            self._logger.error("process_deployer not configured, cannot deploy")
            await self._send_deploy_result(success=False)
            return

        try:
            # ZIP 파일: 스테이징 디렉토리에 압축 해제
            if file_path.suffix.lower() == ".zip":
                self._logger.info("extracting zip to staging: %s", file_path)
                deployer.update_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(file_path, "r") as zf:
                    zf.extractall(deployer.update_dir)
                self._logger.info(
                    "zip extracted to %s, staged files: %s",
                    deployer.update_dir,
                    deployer.staged_file_summary(),
                )
            else:
                # EXE 파일: 스테이징 디렉토리에 복사
                self._logger.info("copying exe to staging: %s", file_path)
                deployer.update_dir.mkdir(parents=True, exist_ok=True)
                dest = deployer.update_dir / file_path.name
                _ = shutil.copy2(str(file_path), str(dest))
                self._logger.info("exe copied to %s", dest)

            # ProcessDeployer 실행
            result = await deployer.execute_deploy()
            if result.success:
                self._logger.info("process deploy verified: pid=%d", result.pid)
                await self._send_deploy_result(success=True, pid=result.pid)
            else:
                self._logger.error("process deploy failed: %s", result.error)
                await self._send_deploy_result(success=False)

        except Exception as exc:
            self._logger.error("process deploy exception: %s", exc, exc_info=True)
            await self._send_deploy_result(success=False)

    async def _send_deploy_result(self, success: bool, pid: int = 0) -> None:
        """CMD_CTRL_ACK로 배포 결과 전송."""
        status = (
            CtrlAckStatus.DEPLOY_VERIFIED if success else CtrlAckStatus.DEPLOY_ROLLBACK
        )
        ack = CmdCtrlAckPayload(action=CtrlAction.RESTART, pid=pid, status=status)
        await self.tcp_client.send_packet(PacketType.CMD_CTRL_ACK, ack.pack())
