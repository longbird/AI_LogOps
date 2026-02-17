from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from agent.core.tcp_client import TCPClient
from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
from shared.protocol import CmdLogPayload, LogAction, Packet, PacketType

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


class TestTCPClientCmdLog:
    @pytest.mark.asyncio
    async def test_on_cmd_log_callback_receives_payload(
        self,
        running_server: tuple[TCPServer, int, SessionManager],
    ) -> None:
        """CMD_LOG packet from server triggers on_cmd_log callback on client."""
        srv, port, mgr = running_server
        client = TCPClient(
            agent_id="PC-LOG-01",
            version="1.0.0",
            token=TOKEN,
            host="127.0.0.1",
            port=port,
        )
        received: list[bytes] = []

        async def log_handler(payload: bytes) -> None:
            received.append(payload)

        client.on_cmd_log = log_handler
        await client.connect()

        # Server sends CMD_LOG to the agent
        session = mgr.get_session("PC-LOG-01")
        assert session is not None
        assert session.writer is not None
        cmd = CmdLogPayload(action=LogAction.HIST_REQUEST, date="20260217")
        writer = session.writer
        writer.write(Packet.build(PacketType.CMD_LOG, cmd.pack()))
        await writer.drain()

        await asyncio.sleep(0.3)
        assert len(received) == 1
        parsed = CmdLogPayload.unpack(received[0])
        assert parsed.action == LogAction.HIST_REQUEST
        assert parsed.date == "20260217"
        await client.disconnect()
