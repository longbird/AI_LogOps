from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
from collections.abc import AsyncIterator

import pytest

from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
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


async def _read_packet(reader: asyncio.StreamReader) -> tuple[PacketType, bytes]:
    header_bytes = await reader.readexactly(HEADER_SIZE)
    packet_type, payload_length = PacketHeader.unpack(header_bytes)
    payload = await reader.readexactly(payload_length)
    return packet_type, payload


async def _auth_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    agent_id: str,
    version: str,
    token: str,
) -> AuthAckPayload:
    auth_packet = Packet.build(
        PacketType.AUTH,
        AuthPayload(agent_id=agent_id, version=version, token=token).pack(),
    )
    writer.write(auth_packet)
    await writer.drain()

    packet_type, payload = await _read_packet(reader)
    assert packet_type == PacketType.AUTH_ACK
    return AuthAckPayload.unpack(payload)


@pytest.fixture
async def server_and_port() -> AsyncIterator[tuple[TCPServer, int, SessionManager]]:
    mgr = SessionManager(max_agents=10)
    srv = TCPServer(
        host="127.0.0.1",
        port=0,
        session_mgr=mgr,
        auth_token="testtoken" + "x" * 55,
    )
    port = await srv.start()
    yield srv, port, mgr
    await srv.stop()


@pytest.mark.asyncio
async def test_server_starts_on_port(
    server_and_port: tuple[TCPServer, int, SessionManager],
) -> None:
    srv, port, _ = server_and_port
    assert port > 0
    assert srv.is_running is True


@pytest.mark.asyncio
async def test_valid_auth_returns_success(
    server_and_port: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = server_and_port
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    ack = await _auth_client(
        reader,
        writer,
        agent_id="agent-valid",
        version="1.0.0",
        token="testtoken" + "x" * 55,
    )

    assert ack.status == AuthStatus.SUCCESS
    assert ack.session_id

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_invalid_token_returns_failed(
    server_and_port: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = server_and_port
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    ack = await _auth_client(
        reader,
        writer,
        agent_id="agent-bad-token",
        version="1.0.0",
        token="wrong" + "x" * 59,
    )

    assert ack.status == AuthStatus.FAILED

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_session_created_after_auth(
    server_and_port: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_and_port
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    _ = await _auth_client(
        reader,
        writer,
        agent_id="agent-session-create",
        version="1.0.0",
        token="testtoken" + "x" * 55,
    )

    session = mgr.get_session("agent-session-create")
    assert session is not None
    assert session.agent_info.agent_id == "agent-session-create"

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_heartbeat_echoed_back(
    server_and_port: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = server_and_port
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    _ = await _auth_client(
        reader,
        writer,
        agent_id="agent-heartbeat",
        version="1.0.0",
        token="testtoken" + "x" * 55,
    )

    heartbeat = HeartbeatPayload(
        timestamp=1_700_000_100, cpu_percent=20, mem_percent=40
    )
    writer.write(Packet.build(PacketType.HEARTBEAT, heartbeat.pack()))
    await writer.drain()

    packet_type, payload = await _read_packet(reader)
    assert packet_type == PacketType.HEARTBEAT
    assert HeartbeatPayload.unpack(payload) == heartbeat

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_removes_session(
    server_and_port: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_and_port
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    _ = await _auth_client(
        reader,
        writer,
        agent_id="agent-disconnect",
        version="1.0.0",
        token="testtoken" + "x" * 55,
    )

    disconnect = DisconnectPayload(reason=DisconnectReason.NORMAL)
    writer.write(Packet.build(PacketType.DISCONNECT, disconnect.pack()))
    await writer.drain()

    for _ in range(20):
        if mgr.get_session("agent-disconnect") is None:
            break
        await asyncio.sleep(0.01)

    assert mgr.get_session("agent-disconnect") is None

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_max_agents_enforced() -> None:
    mgr = SessionManager(max_agents=2)
    srv = TCPServer(
        host="127.0.0.1",
        port=0,
        session_mgr=mgr,
        auth_token="testtoken" + "x" * 55,
    )
    port = await srv.start()
    clients: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

    try:
        for i in range(3):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            clients.append((reader, writer))

            ack = await _auth_client(
                reader,
                writer,
                agent_id=f"agent-max-{i}",
                version="1.0.0",
                token="testtoken" + "x" * 55,
            )
            if i < 2:
                assert ack.status == AuthStatus.SUCCESS
            else:
                assert ack.status == AuthStatus.FAILED
                assert mgr.get_session("agent-max-2") is None
    finally:
        for _, writer in clients:
            writer.close()
            await writer.wait_closed()
        await srv.stop()


@pytest.mark.asyncio
async def test_connection_lost_removes_session(
    server_and_port: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_and_port
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    _ = await _auth_client(
        reader,
        writer,
        agent_id="agent-lost",
        version="1.0.0",
        token="testtoken" + "x" * 55,
    )

    writer.close()
    await writer.wait_closed()

    for _ in range(20):
        if mgr.get_session("agent-lost") is None:
            break
        await asyncio.sleep(0.01)

    assert mgr.get_session("agent-lost") is None
