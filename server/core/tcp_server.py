from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol, cast

from server.core.session_mgr import SessionManager
from shared.protocol import (
    HEADER_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    CmdLogPayload,
    CmdLogAckPayload,
    LogAction,
    Packet,
    PacketHeader,
    PacketType,
    CHUNK_SIZE,
    CmdCtrlAckPayload,
    CmdDeployPayload,
    CtrlAckStatus,
    FileAckPayload,
    FileChunkPayload,
    LogHistPayload,
    LogRealPayload,
)
from shared.utils import compute_sha256, setup_logging

if TYPE_CHECKING:
    from server.storage.manager import StorageManager


class TCPServer:
    def __init__(
        self,
        host: str,
        port: int,
        session_mgr: SessionManager,
        auth_token: str,
        storage_mgr: StorageManager | None = None,
    ):
        self.host: str = host
        self.port: int = port
        self.session_mgr: SessionManager = session_mgr
        self.auth_token: str = auth_token
        self.storage_mgr: StorageManager | None = storage_mgr
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)
        self._server: asyncio.base_events.Server | None = None
        self._is_running: bool = False
        self._deploy_results: dict[str, asyncio.Future[CmdCtrlAckPayload]] = {}

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> int:
        """서버 시작. asyncio.start_server 사용. 바인딩된 포트 반환."""

        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        sockets = self._server.sockets or []
        if not sockets:
            raise RuntimeError("server failed to bind socket")
        socket_addr = cast(tuple[str, int], sockets[0].getsockname())
        bound_port = socket_addr[1]
        self._is_running = True
        self._logger.info("tcp server started: host=%s port=%s", self.host, bound_port)
        return bound_port

    async def stop(self) -> None:
        if self._server is None:
            self._is_running = False
            return

        self._is_running = False
        for session in self.session_mgr.get_all_sessions():
            if session.writer is None:
                continue
            writer = cast(_WriterLike, session.writer)
            try:
                if not writer.is_closing():
                    writer.close()
                await writer.wait_closed()
            except Exception:
                self._logger.debug(
                    "failed to close writer during stop: agent_id=%s",
                    session.agent_info.agent_id,
                )
            self.session_mgr.remove_session(session.agent_info.agent_id)

        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._logger.info("tcp server stopped")

    async def _send_auth_ack(
        self,
        writer: _WriterLike,
        status: AuthStatus,
        session_id: str,
    ) -> None:
        writer.write(
            Packet.build(
                PacketType.AUTH_ACK,
                AuthAckPayload(status=status, session_id=session_id).pack(),
            )
        )
        await writer.drain()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: _WriterLike,
    ) -> None:
        """클라이언트 핸들러: AUTH → 메인 루프."""

        peer = writer.get_extra_info("peername")
        agent_id: str | None = None
        self._logger.info("client connected: peer=%s", peer)
        try:
            agent_id = await self._handle_auth(reader, writer)
            if agent_id is None:
                return
            await self._recv_loop(reader, writer, agent_id)
        except Exception:
            self._logger.exception("client handler error: peer=%s", peer)
        finally:
            if agent_id is not None:
                self.session_mgr.remove_session(agent_id)
            try:
                if not writer.is_closing():
                    writer.close()
                await writer.wait_closed()
            except Exception:
                self._logger.debug("writer close failed: peer=%s", peer)
            self._logger.info("client disconnected: peer=%s", peer)

    async def _handle_auth(
        self,
        reader: asyncio.StreamReader,
        writer: _WriterLike,
    ) -> str | None:
        """AUTH 패킷 처리. 성공 시 agent_id 반환, 실패 시 None."""

        try:
            header_bytes = await reader.readexactly(HEADER_SIZE)
            packet_type, payload_length = PacketHeader.unpack(header_bytes)
            payload = await reader.readexactly(payload_length)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            self._logger.info("connection lost before auth complete")
            return None
        except ValueError:
            self._logger.warning("invalid auth header")
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        if packet_type != PacketType.AUTH:
            self._logger.warning("first packet is not auth: type=%s", packet_type)
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        try:
            auth_payload = AuthPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid auth payload")
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        if auth_payload.token != self.auth_token:
            self._logger.warning(
                "auth failed: invalid token for agent_id=%s", auth_payload.agent_id
            )
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        session = self.session_mgr.create_session(
            agent_id=auth_payload.agent_id,
            version=auth_payload.version,
            writer=writer,
        )
        if session is None:
            self._logger.warning(
                "auth failed: session limit reached for agent_id=%s",
                auth_payload.agent_id,
            )
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        await self._send_auth_ack(writer, AuthStatus.SUCCESS, session.session_id)
        self._logger.info(
            "auth success: agent_id=%s session_id=%s",
            auth_payload.agent_id,
            session.session_id,
        )
        return auth_payload.agent_id

    async def _recv_loop(
        self,
        reader: asyncio.StreamReader,
        writer: _WriterLike,
        agent_id: str,
    ) -> None:
        """인증 후 패킷 수신 루프."""

        while True:
            try:
                header_bytes = await reader.readexactly(HEADER_SIZE)
                packet_type, payload_length = PacketHeader.unpack(header_bytes)
                payload = await reader.readexactly(payload_length)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                self._logger.info("connection lost in recv loop: agent_id=%s", agent_id)
                break
            except ValueError:
                self._logger.warning("invalid packet header: agent_id=%s", agent_id)
                break

            if packet_type == PacketType.HEARTBEAT:
                self.session_mgr.update_heartbeat(agent_id)
                writer.write(Packet.build(PacketType.HEARTBEAT, payload))
                await writer.drain()
                self._logger.debug("heartbeat echoed: agent_id=%s", agent_id)
                continue

            if packet_type == PacketType.DISCONNECT:
                self._logger.info("disconnect received: agent_id=%s", agent_id)
                break

            if packet_type == PacketType.LOG_HIST:
                self._handle_log_hist(agent_id, payload)
                continue

            if packet_type == PacketType.LOG_REAL:
                self._handle_log_real(agent_id, payload)
                continue

            if packet_type in {PacketType.FILE_ACK, PacketType.CMD_CTRL_ACK}:
                if packet_type == PacketType.FILE_ACK:
                    self._handle_file_ack(agent_id, payload)
                else:
                    self._handle_cmd_ctrl_ack(agent_id, payload)
                continue

            if packet_type == PacketType.CMD_LOG_ACK:
                self._handle_cmd_log_ack(agent_id, payload)
                continue

            self._logger.warning(
                "unexpected packet type: agent_id=%s type=%s payload_len=%s",
                agent_id,
                int(packet_type),
                payload_length,
            )

    def _handle_log_hist(self, agent_id: str, payload: bytes) -> None:
        if self.storage_mgr is None:
            return

        try:
            message = LogHistPayload.unpack(payload)
            saved_path = self.storage_mgr.save_log_history(
                agent_id=agent_id,
                filename=message.filename,
                data=message.data,
            )
            self._logger.info(
                "log history stored: agent_id=%s filename=%s path=%s size=%s",
                agent_id,
                message.filename,
                saved_path,
                len(message.data),
            )
        except ValueError:
            self._logger.warning("invalid LOG_HIST payload: agent_id=%s", agent_id)

    def _handle_log_real(self, agent_id: str, payload: bytes) -> None:
        if self.storage_mgr is None:
            return

        try:
            message = LogRealPayload.unpack(payload)
            self.storage_mgr.append_realtime_log(
                agent_id=agent_id,
                filename=message.filename,
                line=message.line,
            )
            self._logger.debug(
                "realtime log appended: agent_id=%s filename=%s",
                agent_id,
                message.filename,
            )
        except ValueError:
            self._logger.warning("invalid LOG_REAL payload: agent_id=%s", agent_id)

    def _handle_file_ack(self, agent_id: str, payload: bytes) -> None:
        try:
            ack = FileAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid FILE_ACK payload: agent_id=%s", agent_id)
            return

        self._logger.info(
            "file ack received: agent_id=%s seq=%s status=%s",
            agent_id,
            ack.seq_num,
            ack.status,
        )

    def _handle_cmd_ctrl_ack(self, agent_id: str, payload: bytes) -> None:
        try:
            ack = CmdCtrlAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid CMD_CTRL_ACK payload: agent_id=%s", agent_id)
            return

        self._logger.info(
            "cmd ctrl ack received: agent_id=%s action=%s pid=%s status=%s",
            agent_id,
            ack.action.name,
            ack.pid,
            ack.status.name,
        )

        future = self._deploy_results.get(agent_id)
        if future is not None and not future.done():
            future.set_result(ack)

        if ack.status == CtrlAckStatus.DEPLOY_VERIFIED:
            self._logger.info("deploy verified: agent_id=%s pid=%s", agent_id, ack.pid)
        elif ack.status == CtrlAckStatus.DEPLOY_ROLLBACK:
            self._logger.warning("deploy rolled back: agent_id=%s", agent_id)

    async def send_log_command(
        self, agent_id: str, action: LogAction, date: str = ""
    ) -> bool:
        """CMD_LOG 패킷을 에이전트에 전송."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return False
        writer = cast(_WriterLike, session.writer)
        cmd = CmdLogPayload(action=action, date=date)
        writer.write(Packet.build(PacketType.CMD_LOG, cmd.pack()))
        await writer.drain()
        return True

    def _handle_cmd_log_ack(self, agent_id: str, payload: bytes) -> None:
        try:
            ack = CmdLogAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid CMD_LOG_ACK payload: agent_id=%s", agent_id)
            return
        self._logger.info(
            "cmd log ack: agent_id=%s action=%s status=%s file_count=%s",
            agent_id,
            ack.action.name,
            ack.status.name,
            ack.file_count,
        )

    async def send_deploy(self, agent_id: str, file_path: str) -> bool:
        """에이전트에 파일 배포. CMD_DEPLOY + FILE_CHUNKs 전송."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return False
        writer = cast(_WriterLike, session.writer)

        path = Path(file_path)
        if not path.exists():
            return False

        data = path.read_bytes()
        sha256_hash = compute_sha256(file_path)

        loop = asyncio.get_running_loop()
        self._deploy_results[agent_id] = loop.create_future()

        cmd = CmdDeployPayload(
            file_size=len(data), sha256=sha256_hash, filename=path.name
        )
        writer.write(Packet.build(PacketType.CMD_DEPLOY, cmd.pack()))
        await writer.drain()

        seq = 0
        for offset in range(0, len(data), CHUNK_SIZE):
            chunk_data = data[offset : offset + CHUNK_SIZE]
            chunk = FileChunkPayload(seq_num=seq, data=chunk_data)
            writer.write(Packet.build(PacketType.FILE_CHUNK, chunk.pack()))
            await writer.drain()
            seq += 1

        return True

    def get_deploy_result_future(
        self, agent_id: str
    ) -> asyncio.Future[CmdCtrlAckPayload] | None:
        return self._deploy_results.get(agent_id)


class _WriterLike(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...

    def is_closing(self) -> bool: ...

    def get_extra_info(self, name: str) -> object: ...
