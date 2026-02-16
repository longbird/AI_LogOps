from __future__ import annotations

import asyncio
import contextlib
import logging
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
        self.on_cmd_ctrl: PacketCallback | None = None
        self.on_agent_update: PacketCallback | None = None

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

    async def send_heartbeat(self) -> None:
        """HEARTBEAT 패킷 전송. psutil로 CPU/MEM 수집."""

        cpu = max(0, min(100, int(psutil.cpu_percent(interval=None))))
        mem_percent = cast(float, psutil.virtual_memory().percent)
        mem = max(0, min(100, int(mem_percent)))
        payload = HeartbeatPayload(
            timestamp=int(time.time()), cpu_percent=cpu, mem_percent=mem
        ).pack()
        await self.send_packet(PacketType.HEARTBEAT, payload)

    async def send_packet(self, packet_type: PacketType, payload: bytes) -> None:
        """범용 패킷 전송."""

        writer = self._writer
        if writer is None or writer.is_closing() or not self._connected:
            raise ConnectionError("tcp client is not connected")

        writer.write(Packet.build(packet_type, payload))
        await writer.drain()

    async def _recv_loop(self) -> None:
        """서버로부터 패킷 수신 루프 (asyncio.Task로 실행)."""

        reader = self._reader
        if reader is None:
            return

        try:
            while True:
                header_bytes = await reader.readexactly(HEADER_SIZE)
                packet_type, payload_length = PacketHeader.unpack(header_bytes)
                payload = await reader.readexactly(payload_length)

                if packet_type == PacketType.HEARTBEAT:
                    continue
                if packet_type == PacketType.CMD_DEPLOY:
                    if self.on_cmd_deploy is not None:
                        await self.on_cmd_deploy(payload)
                    continue
                if packet_type == PacketType.FILE_CHUNK:
                    continue
                if packet_type == PacketType.CMD_CTRL:
                    if self.on_cmd_ctrl is not None:
                        await self.on_cmd_ctrl(payload)
                    continue
                if packet_type == PacketType.AGENT_UPDATE:
                    if self.on_agent_update is not None:
                        await self.on_agent_update(payload)
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
            writer = self._writer
            if writer is not None and not writer.is_closing():
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
            self._reader = None
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
