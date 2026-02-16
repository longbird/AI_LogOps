from __future__ import annotations

import asyncio
import logging
from typing import Protocol, cast

from server.core.session_mgr import SessionManager
from shared.protocol import (
    HEADER_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    Packet,
    PacketHeader,
    PacketType,
)
from shared.utils import setup_logging


class TCPServer:
    def __init__(
        self,
        host: str,
        port: int,
        session_mgr: SessionManager,
        auth_token: str,
    ):
        self.host: str = host
        self.port: int = port
        self.session_mgr: SessionManager = session_mgr
        self.auth_token: str = auth_token
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)
        self._server: asyncio.base_events.Server | None = None
        self._is_running: bool = False

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

            if packet_type in {
                PacketType.LOG_HIST,
                PacketType.LOG_REAL,
                PacketType.FILE_ACK,
                PacketType.CMD_CTRL_ACK,
            }:
                self._logger.info(
                    "stub packet received: agent_id=%s type=%s payload_len=%s",
                    agent_id,
                    packet_type.name,
                    payload_length,
                )
                continue

            self._logger.warning(
                "unexpected packet type: agent_id=%s type=%s payload_len=%s",
                agent_id,
                int(packet_type),
                payload_length,
            )


class _WriterLike(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...

    def is_closing(self) -> bool: ...

    def get_extra_info(self, name: str) -> object: ...
