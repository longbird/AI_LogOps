from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from agent.core.file_transfer import FileTransferReceiver
from shared.protocol import (
    CHUNK_SIZE,
    CmdFileGetAckPayload,
    CmdFileGetPayload,
    CmdFileListAckPayload,
    CmdFileListPayload,
    CmdFilePutAckPayload,
    CmdFilePutPayload,
    CmdFileRunAckPayload,
    CmdFileRunPayload,
    FileAckPayload,
    FileChunkPayload,
    PacketType,
)
from shared.utils import compute_sha256, setup_logging

if TYPE_CHECKING:
    import logging

    from agent.core.config_view import ConfigView
    from agent.core.tcp_client import TCPClient

MAX_DIR_ENTRIES = 5000


class FileHandler:
    """에이전트 측 파일 관리 명령 처리기.

    보안 검증 (경로 정규화, 쓰기 금지, 크기 제한) 후 파일 작업을 수행한다.
    """

    def __init__(self, tcp_client: TCPClient, config: ConfigView) -> None:
        self.tcp_client = tcp_client
        self._logger: logging.Logger = setup_logging("file_handler")
        self._receiver: FileTransferReceiver | None = None
        self._put_request_id: str = ""
        self._put_remote_path: str = ""

        # config에서 설정 읽기
        fm_cfg = config.sub("file_manager")
        self._enabled: bool = fm_cfg.b("enabled", True)
        self._write_deny_paths: list[str] = [
            p.lower().replace("\\", "/") for p in fm_cfg.ls("write_deny_paths", [])
        ]
        self._max_file_size: int = fm_cfg.i("max_file_size_mb", 100) * 1024 * 1024

    def reset_receiver(self) -> None:
        """Stale 수신 상태 초기화. CMD_DEPLOY 시작 시 호출."""
        if self._receiver is not None:
            self._logger.warning("resetting stale file_put receiver")
            self._receiver = None
            self._put_request_id = ""
            self._put_remote_path = ""

    def _is_enabled(self) -> tuple[bool, str]:
        """파일 관리 활성화 여부. (enabled, error_msg)."""
        if not self._enabled:
            return False, "파일 관리가 비활성화되어 있습니다"
        return True, ""

    def _validate_write_path(self, path: str) -> tuple[bool, str]:
        """쓰기 경로 검증. Path.resolve()로 symlink 해석 후 deny 목록 확인."""
        try:
            resolved = str(Path(path).resolve()).lower().replace("\\", "/")
        except (OSError, ValueError) as exc:
            return False, f"경로 해석 실패: {exc}"

        for deny in self._write_deny_paths:
            if resolved.startswith(deny):
                return False, f"쓰기 금지 경로: {deny}"
        return True, ""

    async def handle_cmd_file_list(self, payload_data: bytes) -> None:
        """CMD_FILE_LIST: 디렉토리 목록 요청 처리."""
        cmd = CmdFileListPayload.unpack(payload_data)
        self._logger.info("file_list request: path=%s", cmd.path)

        ok, err = self._is_enabled()
        if not ok:
            await self._send_list_ack(cmd.request_id, False, err, "", [])
            return

        try:
            target = Path(cmd.path).resolve()
        except (OSError, ValueError) as exc:
            await self._send_list_ack(cmd.request_id, False, str(exc), "", [])
            return

        if not target.is_dir():
            await self._send_list_ack(
                cmd.request_id, False, "디렉토리가 아니거나 존재하지 않습니다", "", []
            )
            return

        entries: list[dict] = []
        truncated = False
        try:
            with os.scandir(str(target)) as it:
                for entry in it:
                    if len(entries) >= MAX_DIR_ENTRIES:
                        truncated = True
                        break
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if not entry.is_dir() else 0,
                            "modified": stat.st_mtime,
                        })
                    except (PermissionError, OSError):
                        continue  # 접근 불가 항목 건너뛰기
        except PermissionError:
            await self._send_list_ack(
                cmd.request_id, False, "접근 권한이 없습니다", "", []
            )
            return
        except OSError as exc:
            await self._send_list_ack(cmd.request_id, False, str(exc), "", [])
            return

        await self._send_list_ack(
            cmd.request_id, True, "", str(target), entries, truncated
        )

    async def _send_list_ack(
        self,
        request_id: str,
        success: bool,
        error: str,
        current_path: str,
        entries: list[dict],
        truncated: bool = False,
    ) -> None:
        ack = CmdFileListAckPayload(
            request_id=request_id,
            success=success,
            error=error,
            current_path=current_path,
            entries=entries,
            truncated=truncated,
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_LIST_ACK, ack.pack())

    async def handle_cmd_file_get(self, payload_data: bytes) -> None:
        """CMD_FILE_GET: 파일 다운로드 요청. 메타 ACK + FILE_CHUNK 스트리밍."""
        cmd = CmdFileGetPayload.unpack(payload_data)
        self._logger.info("file_get request: path=%s", cmd.remote_path)

        ok, err = self._is_enabled()
        if not ok:
            await self._send_get_ack(cmd.request_id, False, err)
            return

        try:
            file_path = Path(cmd.remote_path).resolve()
        except (OSError, ValueError) as exc:
            await self._send_get_ack(cmd.request_id, False, str(exc))
            return

        if not file_path.is_file():
            await self._send_get_ack(
                cmd.request_id, False, "파일이 존재하지 않습니다"
            )
            return

        file_size = file_path.stat().st_size
        if file_size > self._max_file_size:
            max_mb = self._max_file_size // (1024 * 1024)
            await self._send_get_ack(
                cmd.request_id, False, f"파일 크기 제한 초과 ({max_mb}MB)"
            )
            return

        sha256 = compute_sha256(str(file_path))

        # 메타 ACK 전송
        ack = CmdFileGetAckPayload(
            request_id=cmd.request_id,
            success=True,
            error="",
            file_size=file_size,
            sha256=sha256,
            filename=file_path.name,
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_GET_ACK, ack.pack())

        # FILE_CHUNK 스트리밍
        data = file_path.read_bytes()
        seq = 0
        for offset in range(0, len(data), CHUNK_SIZE):
            chunk_data = data[offset : offset + CHUNK_SIZE]
            chunk = FileChunkPayload(seq_num=seq, data=chunk_data)
            await self.tcp_client.send_packet(PacketType.FILE_CHUNK, chunk.pack())
            seq += 1
        self._logger.info(
            "file_get complete: %s (%d bytes, %d chunks)", file_path.name, file_size, seq
        )

    async def _send_get_ack(
        self, request_id: str, success: bool, error: str
    ) -> None:
        ack = CmdFileGetAckPayload(
            request_id=request_id, success=success, error=error
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_GET_ACK, ack.pack())

    async def handle_cmd_file_put(self, payload_data: bytes) -> None:
        """CMD_FILE_PUT: 파일 업로드 시작. FileTransferReceiver 초기화."""
        cmd = CmdFilePutPayload.unpack(payload_data)
        self._logger.info(
            "file_put request: path=%s size=%d", cmd.remote_path, cmd.file_size
        )

        ok, err = self._is_enabled()
        if not ok:
            await self._send_put_ack(cmd.request_id, False, err)
            return

        # 쓰기 경로 검증
        valid, msg = self._validate_write_path(cmd.remote_path)
        if not valid:
            await self._send_put_ack(cmd.request_id, False, msg)
            return

        # 크기 제한 검증
        if cmd.file_size > self._max_file_size:
            max_mb = self._max_file_size // (1024 * 1024)
            await self._send_put_ack(
                cmd.request_id, False, f"파일 크기 제한 초과 ({max_mb}MB)"
            )
            return

        # 수신 준비
        dest = Path(cmd.remote_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        self._receiver = FileTransferReceiver(target_dir=str(dest.parent))
        self._receiver.start_receive(cmd.file_size, cmd.sha256, cmd.filename)
        self._put_request_id = cmd.request_id
        self._put_remote_path = cmd.remote_path
        self._logger.info("file_put receiver initialized: %s", dest)

    async def handle_file_chunk_for_put(self, payload_data: bytes) -> None:
        """FILE_CHUNK for file_put: 청크 수신 + ACK. 완료 시 PUT_ACK."""
        if self._receiver is None:
            return

        chunk = FileChunkPayload.unpack(payload_data)
        success = self._receiver.receive_chunk(chunk)
        ack = FileAckPayload(seq_num=chunk.seq_num, status=0 if success else 1)
        await self.tcp_client.send_packet(PacketType.FILE_ACK, ack.pack())

        if self._receiver.is_complete():
            file_path = self._receiver.assemble()
            if file_path is not None:
                # 수신 성공 — 최종 경로로 이동 (필요 시)
                dest = Path(self._put_remote_path).resolve()
                if file_path.resolve() != dest:
                    import shutil
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(file_path), str(dest))
                self._logger.info("file_put complete: %s", dest)
                await self._send_put_ack(self._put_request_id, True, "")
            else:
                self._logger.error("file_put SHA-256 mismatch")
                await self._send_put_ack(
                    self._put_request_id, False, "파일 무결성 검증 실패 (SHA-256)"
                )
            self._receiver = None

    async def _send_put_ack(
        self, request_id: str, success: bool, error: str
    ) -> None:
        ack = CmdFilePutAckPayload(
            request_id=request_id, success=success, error=error
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_PUT_ACK, ack.pack())

    async def handle_cmd_file_run(self, payload_data: bytes) -> None:
        """CMD_FILE_RUN: 에이전트 PC에서 파일 실행."""
        cmd = CmdFileRunPayload.unpack(payload_data)
        self._logger.info("file_run request: path=%s", cmd.file_path)

        ok, err = self._is_enabled()
        if not ok:
            await self._send_run_ack(cmd.request_id, False, err)
            return

        try:
            file_path = Path(cmd.file_path).resolve()
        except (OSError, ValueError) as exc:
            await self._send_run_ack(cmd.request_id, False, str(exc))
            return

        if not file_path.is_file():
            await self._send_run_ack(
                cmd.request_id, False, "파일이 존재하지 않습니다"
            )
            return

        try:
            await asyncio.to_thread(os.startfile, str(file_path))
            self._logger.info("file_run started: %s", file_path)
            await self._send_run_ack(cmd.request_id, True, "")
        except OSError as exc:
            self._logger.error("file_run failed: %s", exc)
            await self._send_run_ack(cmd.request_id, False, str(exc))

    async def _send_run_ack(
        self, request_id: str, success: bool, error: str
    ) -> None:
        ack = CmdFileRunAckPayload(
            request_id=request_id, success=success, error=error
        )
        await self.tcp_client.send_packet(PacketType.CMD_FILE_RUN_ACK, ack.pack())

    @property
    def is_receiving_file(self) -> bool:
        """파일 수신 진행 중 여부."""
        return self._receiver is not None
