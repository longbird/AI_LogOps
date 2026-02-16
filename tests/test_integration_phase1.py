from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from agent.core.tcp_client import TCPClient
from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
from server.telegram.handler import ParsedCommand, TelegramHandler
from shared.protocol import (
    HEADER_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    Packet,
    PacketHeader,
    PacketType,
)

TOKEN = "integrationtest" + "x" * 49


@pytest.fixture
async def server_env() -> AsyncIterator[tuple[TCPServer, int, SessionManager]]:
    mgr = SessionManager(max_agents=10)
    srv = TCPServer(host="127.0.0.1", port=0, session_mgr=mgr, auth_token=TOKEN)
    port = await srv.start()
    yield srv, port, mgr
    await srv.stop()


def make_client(agent_id: str, port: int) -> TCPClient:
    return TCPClient(
        agent_id=agent_id,
        version="1.0.0",
        token=TOKEN,
        host="127.0.0.1",
        port=port,
    )


async def wait_for_session_count(
    mgr: SessionManager,
    expected: int,
    timeout: float = 1.0,
    interval: float = 0.05,
) -> None:
    end_time = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end_time:
        if len(mgr.get_all_sessions()) == expected:
            return
        await asyncio.sleep(interval)
    assert len(mgr.get_all_sessions()) == expected


async def read_packet(reader: asyncio.StreamReader) -> tuple[PacketType, bytes]:
    header_bytes = await reader.readexactly(HEADER_SIZE)
    packet_type, payload_length = PacketHeader.unpack(header_bytes)
    payload = await reader.readexactly(payload_length)
    return packet_type, payload


async def auth_stream_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    agent_id: str,
) -> AuthAckPayload:
    packet = Packet.build(
        PacketType.AUTH,
        AuthPayload(agent_id=agent_id, version="1.0.0", token=TOKEN).pack(),
    )
    writer.write(packet)
    await writer.drain()

    packet_type, payload = await read_packet(reader)
    assert packet_type == PacketType.AUTH_ACK
    ack = AuthAckPayload.unpack(payload)
    assert ack.status == AuthStatus.SUCCESS
    return ack


@pytest.mark.asyncio
async def test_full_connect_heartbeat_disconnect_flow(
    server_env: tuple[TCPServer, int, SessionManager],
) -> None:
    srv, port, mgr = server_env
    client = make_client("agent-full-flow", port)

    connected = await client.connect()
    assert connected is True
    assert client.session_id != ""

    session = mgr.get_session("agent-full-flow")
    assert session is not None
    session_id = session.session_id

    before_heartbeat = session.last_heartbeat
    await asyncio.sleep(0.1)
    await client.send_heartbeat()
    await asyncio.sleep(0.1)

    updated = mgr.get_session("agent-full-flow")
    assert updated is not None
    assert updated.session_id == session_id
    assert updated.last_heartbeat > before_heartbeat

    await client.disconnect()
    await wait_for_session_count(mgr, expected=0, timeout=1.5)
    assert mgr.get_session("agent-full-flow") is None

    await srv.stop()
    assert srv.is_running is False


@pytest.mark.asyncio
async def test_multiple_agents_connect(
    server_env: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_env
    clients = [make_client(f"agent-multi-{i}", port) for i in range(3)]

    connected = await asyncio.gather(*(client.connect() for client in clients))
    assert connected == [True, True, True]

    await wait_for_session_count(mgr, expected=3, timeout=1.5)
    sessions = mgr.get_all_sessions()
    assert len(sessions) == 3

    session_ids = {session.session_id for session in sessions}
    assert len(session_ids) == 3
    assert {client.session_id for client in clients} == session_ids

    _ = await asyncio.gather(*(client.disconnect() for client in clients))
    await wait_for_session_count(mgr, expected=0, timeout=1.5)
    assert len(mgr.get_all_sessions()) == 0


@pytest.mark.asyncio
async def test_connect_disconnect_reconnect(
    server_env: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_env
    first_client = make_client("agent-reconnect", port)

    first_connected = await first_client.connect()
    assert first_connected is True
    first_session_id = first_client.session_id
    assert first_session_id != ""

    await first_client.disconnect()
    await wait_for_session_count(mgr, expected=0, timeout=1.5)

    second_client = make_client("agent-reconnect", port)
    second_connected = await second_client.connect()
    assert second_connected is True
    second_session_id = second_client.session_id
    assert second_session_id != ""

    await wait_for_session_count(mgr, expected=1, timeout=1.5)
    assert first_session_id != second_session_id
    assert len(mgr.get_all_sessions()) == 1

    current = mgr.get_session("agent-reconnect")
    assert current is not None
    assert current.session_id == second_session_id

    await second_client.disconnect()
    await wait_for_session_count(mgr, expected=0, timeout=1.5)


@pytest.mark.asyncio
async def test_telegram_handler_with_session_manager(
    server_env: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_env
    handler = TelegramHandler(admin_chat_ids=[101])

    async def status_handler(_: ParsedCommand) -> str:
        return f"{len(mgr.get_all_sessions())} agents connected"

    handler.register("status", status_handler)

    initial = await handler.handle("/status", chat_id=101)
    assert initial == "0 agents connected"

    client = make_client("agent-telegram-status", port)
    connected = await client.connect()
    assert connected is True

    await wait_for_session_count(mgr, expected=1, timeout=1.5)
    connected_status = await handler.handle("/status", chat_id=101)
    assert connected_status == "1 agents connected"

    await client.disconnect()
    await wait_for_session_count(mgr, expected=0, timeout=1.5)

    final = await handler.handle("/status", chat_id=101)
    assert final == "0 agents connected"


@pytest.mark.asyncio
async def test_rapid_heartbeat_updates(
    server_env: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_env
    client = make_client("agent-rapid-heartbeat", port)

    connected = await client.connect()
    assert connected is True

    initial_session = mgr.get_session("agent-rapid-heartbeat")
    assert initial_session is not None
    initial_heartbeat = initial_session.last_heartbeat

    await asyncio.sleep(0.1)
    for _ in range(5):
        await client.send_heartbeat()
        await asyncio.sleep(0.05)

    await asyncio.sleep(0.2)
    updated_session = mgr.get_session("agent-rapid-heartbeat")
    assert updated_session is not None
    assert updated_session.last_heartbeat > initial_heartbeat
    assert time.time() - updated_session.last_heartbeat < 2.0

    await client.disconnect()
    await wait_for_session_count(mgr, expected=0, timeout=1.5)


@pytest.mark.asyncio
async def test_server_handles_abrupt_client_death(
    server_env: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, mgr = server_env
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    _ = await auth_stream_client(reader, writer, agent_id="agent-abrupt")
    await wait_for_session_count(mgr, expected=1, timeout=1.5)

    writer.close()
    await writer.wait_closed()

    await asyncio.sleep(0.5)
    await wait_for_session_count(mgr, expected=0, timeout=1.5)
    assert mgr.get_session("agent-abrupt") is None
