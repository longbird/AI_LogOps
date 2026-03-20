from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from typing import cast

import psutil

from shared.protocol import (
    HEADER_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    DisconnectPayload,
    DisconnectReason,
    HeartbeatPayload,
    LogFileEntry,
    LogFileListPayload,
    LogFileSelectPayload,
    LogHistPayload,
    LogRealPayload,
    Packet,
    PacketHeader,
    PacketType,
)
from shared.utils import setup_logging

PacketCallback = Callable[[bytes], Awaitable[None]]


class TCPClient:
    def __init__(
        self,
        agent_id: str,
        version: str,
        token: str,
        host: str = "127.0.0.1",
        port: int = 9500,
        heartbeat_interval: int = 30,
        reconnect_attempts: int = 5,
        reconnect_delay: int = 10,
    ):
        self.agent_id: str = agent_id
        self.version: str = version
        self.token: str = token
        self.host: str = host
        self.port: int = port
        self.heartbeat_interval: int = heartbeat_interval
        self.reconnect_attempts: int = reconnect_attempts
        self.reconnect_delay: int = reconnect_delay

        self.session_id: str = ""
        self.on_cmd_deploy: PacketCallback | None = None
        self.on_file_chunk: PacketCallback | None = None
        self.on_cmd_ctrl: PacketCallback | None = None
        self.on_agent_update: PacketCallback | None = None
        self.on_cmd_log: PacketCallback | None = None
        self.on_log_file_select: PacketCallback | None = None
        self.on_rec_upload_req: PacketCallback | None = None
        self.on_cmd_rec: PacketCallback | None = None
        self.on_stt_result: PacketCallback | None = None
        self.on_rec_data_req: PacketCallback | None = None
        self.on_cmd_config: PacketCallback | None = None
        self.on_cmd_exec: PacketCallback | None = None
        self.on_cmd_file_list: PacketCallback | None = None
        self.on_cmd_file_get: PacketCallback | None = None
        self.on_cmd_file_put: PacketCallback | None = None
        self.on_cmd_file_run: PacketCallback | None = None

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._connected: bool = False
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """서버 접속 + AUTH. 성공 시 True, 실패 시 False."""

        if self._connected:
            return True

        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
        except OSError as exc:
            self._logger.warning(
                "connect failed: host=%s port=%s err=%s", self.host, self.port, exc
            )
            return False

        # TCP keepalive 설정 — NAT/방화벽의 유휴 연결 종료 방지
        sock: socket.socket | None = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Windows: SIO_KEEPALIVE_VALS (onoff, keepalivetime_ms, keepaliveinterval_ms)
                sock.ioctl(  # type: ignore[attr-defined]
                    socket.SIO_KEEPALIVE_VALS,  # type: ignore[attr-defined]
                    (1, 30_000, 10_000),  # 30초 유휴 후 10초 간격 probe
                )
            except (AttributeError, OSError):
                self._logger.debug("TCP keepalive setup skipped (unsupported)")

        self._reader = reader
        self._writer = writer

        try:
            auth_packet = Packet.build(
                PacketType.AUTH,
                AuthPayload(
                    agent_id=self.agent_id,
                    version=self.version,
                    token=self.token,
                ).pack(),
            )
            writer.write(auth_packet)
            await writer.drain()

            header_bytes = await reader.readexactly(HEADER_SIZE)
            packet_type, payload_length = PacketHeader.unpack(header_bytes)
            payload = await reader.readexactly(payload_length)

            if packet_type != PacketType.AUTH_ACK:
                self._logger.warning(
                    "unexpected auth response type: %s", int(packet_type)
                )
                await self._close_connection()
                return False

            ack = AuthAckPayload.unpack(payload)
            if ack.status != AuthStatus.SUCCESS:
                self._logger.info("auth failed: status=%s", ack.status.name)
                await self._close_connection()
                return False

            self.session_id = ack.session_id
            self._connected = True
            self._recv_task = asyncio.create_task(self._recv_loop())
            return True
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError, ValueError):
            await self._close_connection()
            return False

    async def disconnect(self) -> None:
        """DISCONNECT 패킷 전송 후 정상 종료."""

        if self._writer is not None and self._connected:
            with contextlib.suppress(OSError, ConnectionError):
                payload = DisconnectPayload(reason=DisconnectReason.NORMAL).pack()
                self._writer.write(Packet.build(PacketType.DISCONNECT, payload))
                await self._writer.drain()

        if self._recv_task is not None:
            _ = self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None

        await self._close_connection()

    async def send_heartbeat(self, process_status: int = 0) -> None:
        """HEARTBEAT 패킷 전송. psutil로 CPU/MEM 수집, 프로세스 상태 포함."""

        cpu = max(0, min(100, int(psutil.cpu_percent(interval=None))))
        mem_percent = cast(float, psutil.virtual_memory().percent)
        mem = max(0, min(100, int(mem_percent)))
        payload = HeartbeatPayload(
            timestamp=int(time.time()),
            cpu_percent=cpu,
            mem_percent=mem,
            process_status=process_status,
        ).pack()
        await self.send_packet(PacketType.HEARTBEAT, payload)

    async def send_packet(self, packet_type: PacketType, payload: bytes) -> None:
        """범용 패킷 전송."""

        writer = self._writer
        if writer is None or writer.is_closing() or not self._connected:
            raise ConnectionError("tcp client is not connected")

        writer.write(Packet.build(packet_type, payload))
        await writer.drain()

    async def send_log_history(self, filename: str, data: bytes) -> None:
        """LOG_HIST 패킷으로 누적 로그 전송."""

        payload = LogHistPayload(filename=filename, data=data)
        await self.send_packet(PacketType.LOG_HIST, payload.pack())

    async def send_log_line(
        self, filename: str, line: str, folder_index: int = 0
    ) -> None:
        """LOG_REAL 패킷으로 실시간 로그 라인 전송."""
        payload = LogRealPayload(
            filename=filename, line=line, folder_index=folder_index
        )
        await self.send_packet(PacketType.LOG_REAL, payload.pack())

    async def send_log_file_list(self, entries: list[LogFileEntry]) -> None:
        """LOG_FILE_LIST 패킷으로 파일 메타데이터 목록 전송."""

        payload = LogFileListPayload(entries=entries)
        await self.send_packet(PacketType.LOG_FILE_LIST, payload.pack())

    async def close_on_error(self) -> None:
        """연결 오류 시 강제 종료 (DISCONNECT 패킷 전송 없이).

        heartbeat 전송 실패 등 연결이 이미 끊어진 상황에서 사용.
        즉시 _connected = False 설정하여 재접속 로직이 바로 트리거되도록 한다.
        """
        self._connected = False
        if self._recv_task is not None:
            _ = self._recv_task.cancel()
        writer = self._writer
        if writer is not None and not writer.is_closing():
            writer.close()

    async def _recv_loop(self) -> None:
        """서버로부터 패킷 수신 루프 (asyncio.Task로 실행)."""

        reader = self._reader
        if reader is None:
            return

        # 서버 응답 타임아웃: heartbeat 에코가 오지 않으면 연결 끊김으로 간주
        read_timeout = max(self.heartbeat_interval * 2, 30)

        try:
            while True:
                try:
                    header_bytes = await asyncio.wait_for(
                        reader.readexactly(HEADER_SIZE),
                        timeout=read_timeout,
                    )
                except asyncio.TimeoutError:
                    self._logger.warning(
                        "no data from server for %ds — connection presumed dead",
                        read_timeout,
                    )
                    break
                packet_type, payload_length = PacketHeader.unpack(header_bytes)
                payload = await reader.readexactly(payload_length)

                if packet_type == PacketType.HEARTBEAT:
                    continue
                if packet_type == PacketType.CMD_DEPLOY:
                    if self.on_cmd_deploy is not None:
                        await self.on_cmd_deploy(payload)
                    continue
                if packet_type == PacketType.FILE_CHUNK:
                    if self.on_file_chunk is not None:
                        await self.on_file_chunk(payload)
                    continue
                if packet_type == PacketType.CMD_CTRL:
                    if self.on_cmd_ctrl is not None:
                        await self.on_cmd_ctrl(payload)
                    continue
                if packet_type == PacketType.AGENT_UPDATE:
                    if self.on_agent_update is not None:
                        await self.on_agent_update(payload)
                    continue
                if packet_type == PacketType.CMD_LOG:
                    if self.on_cmd_log is not None:
                        await self.on_cmd_log(payload)
                    continue
                if packet_type == PacketType.LOG_FILE_SELECT:
                    if self.on_log_file_select is not None:
                        await self.on_log_file_select(payload)
                    continue
                if packet_type == PacketType.REC_UPLOAD_REQ:
                    if self.on_rec_upload_req is not None:
                        await self.on_rec_upload_req(payload)
                    continue
                if packet_type == PacketType.CMD_REC:
                    if self.on_cmd_rec is not None:
                        await self.on_cmd_rec(payload)
                    continue
                if packet_type == PacketType.STT_RESULT:
                    if self.on_stt_result is not None:
                        await self.on_stt_result(payload)
                    continue
                if packet_type == PacketType.REC_DATA_REQ:
                    if self.on_rec_data_req is not None:
                        await self.on_rec_data_req(payload)
                    continue
                if packet_type == PacketType.CMD_CONFIG:
                    if self.on_cmd_config is not None:
                        await self.on_cmd_config(payload)
                    continue
                if packet_type == PacketType.CMD_EXEC:
                    if self.on_cmd_exec is not None:
                        await self.on_cmd_exec(payload)
                    continue
                if packet_type == PacketType.CMD_FILE_LIST:
                    if self.on_cmd_file_list is not None:
                        await self.on_cmd_file_list(payload)
                    continue
                if packet_type == PacketType.CMD_FILE_GET:
                    if self.on_cmd_file_get is not None:
                        await self.on_cmd_file_get(payload)
                    continue
                if packet_type == PacketType.CMD_FILE_PUT:
                    if self.on_cmd_file_put is not None:
                        await self.on_cmd_file_put(payload)
                    continue
                if packet_type == PacketType.CMD_FILE_RUN:
                    if self.on_cmd_file_run is not None:
                        await self.on_cmd_file_run(payload)
                    continue
                if packet_type == PacketType.DISCONNECT:
                    break
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError, ValueError):
            self._logger.info("recv loop ended due to connection close")
        finally:
            self._connected = False
            self.session_id = ""
            # 로컬 참조 캡처 — await 중 connect()가 새 연결을 만들 수 있으므로
            # identity 체크로 새 연결을 덮어쓰지 않도록 보호
            old_reader = self._reader
            old_writer = self._writer
            if old_writer is not None and not old_writer.is_closing():
                old_writer.close()
                with contextlib.suppress(OSError):
                    await old_writer.wait_closed()
            if self._reader is old_reader:
                self._reader = None
            if self._writer is old_writer:
                self._writer = None

    async def _close_connection(self) -> None:
        writer = self._writer
        self._connected = False
        self.session_id = ""
        self._reader = None
        self._writer = None

        if writer is None:
            return
        if not writer.is_closing():
            writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
