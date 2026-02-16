from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agent.core.tcp_client import TCPClient
from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
from shared.protocol import PacketType

TOKEN = "testtoken" + "x" * 55


@pytest.fixture
async def running_server() -> AsyncIterator[tuple[TCPServer, int, SessionManager]]:
    mgr = SessionManager(max_agents=10)
    srv = TCPServer(host="127.0.0.1", port=0, session_mgr=mgr, auth_token=TOKEN)
    port = await srv.start()
    yield srv, port, mgr
    await srv.stop()


@pytest.mark.asyncio
async def test_connect_and_auth(
    running_server: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = running_server
    client = TCPClient(
        agent_id="agent-connect", version="1.0.0", token=TOKEN, port=port
    )

    connected = await client.connect()

    assert connected is True
    assert client.session_id != ""
    assert client.is_connected is True

    await client.disconnect()


@pytest.mark.asyncio
async def test_auth_failure(
    running_server: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = running_server
    client = TCPClient(
        agent_id="agent-auth-fail",
        version="1.0.0",
        token="wrong" + "x" * 59,
        port=port,
    )

    connected = await client.connect()

    assert connected is False
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_heartbeat_exchange(
    running_server: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = running_server
    client = TCPClient(
        agent_id="agent-heartbeat", version="1.0.0", token=TOKEN, port=port
    )

    connected = await client.connect()
    assert connected is True

    await client.send_heartbeat()

    await client.disconnect()


@pytest.mark.asyncio
async def test_disconnect(
    running_server: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = running_server
    client = TCPClient(
        agent_id="agent-disconnect", version="1.0.0", token=TOKEN, port=port
    )

    connected = await client.connect()
    assert connected is True

    await client.disconnect()

    assert client.is_connected is False


@pytest.mark.asyncio
async def test_send_packet(
    running_server: tuple[TCPServer, int, SessionManager],
) -> None:
    _, port, _ = running_server
    client = TCPClient(
        agent_id="agent-send-packet", version="1.0.0", token=TOKEN, port=port
    )

    connected = await client.connect()
    assert connected is True

    await client.send_packet(PacketType.LOG_REAL, b"test-log")

    await client.disconnect()


@pytest.mark.asyncio
async def test_connection_refused() -> None:
    client = TCPClient(
        agent_id="agent-refused",
        version="1.0.0",
        token=TOKEN,
        host="127.0.0.1",
        port=1,
    )

    connected = await client.connect()

    assert connected is False
    assert client.is_connected is False
