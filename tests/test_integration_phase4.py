from __future__ import annotations

# pyright: reportMissingImports=false, reportUnusedCallResult=false

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from agent.core.deploy_handler import DeployHandler
from agent.core.process_mgr import ProcessManager
from agent.core.tcp_client import TCPClient
from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
from shared.protocol import (
    CHUNK_SIZE,
    CtrlAckStatus,
    FileAckPayload,
    FileChunkPayload,
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


class StubProcessManager:
    def __init__(
        self,
        process_path: Path,
        *,
        health_ok: bool,
        start_values: list[int],
    ) -> None:
        self.process_path: Path = process_path
        self.health_ok: bool = health_ok
        self.start_values: list[int] = start_values
        self.backup_calls: int = 0
        self.kill_calls: int = 0
        self.start_calls: int = 0
        self.rollback_calls: int = 0
        self.health_check_calls: int = 0
        self.health_check_timeouts: list[int] = []

    def backup_current(self) -> str:
        self.backup_calls += 1
        return "backup-path"

    def kill(self) -> bool:
        self.kill_calls += 1
        return True

    def kill_all(self) -> bool:
        self.kill_calls += 1
        return True

    def start(self) -> int:
        pid = self.start_values[min(self.start_calls, len(self.start_values) - 1)]
        self.start_calls += 1
        return pid

    async def health_check(self, timeout: int = 30) -> bool:
        self.health_check_calls += 1
        self.health_check_timeouts.append(timeout)
        return self.health_ok

    def rollback(self) -> bool:
        self.rollback_calls += 1
        return True


def build_process_mgr(
    tmp_path: Path,
    *,
    health_ok: bool,
    start_side_effect: list[int] | None = None,
) -> StubProcessManager:
    process_path = tmp_path / "agent" / "target_app.bin"
    process_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.write_bytes(b"old-app")
    start_values = start_side_effect if start_side_effect is not None else [1001]
    return StubProcessManager(
        process_path=process_path,
        health_ok=health_ok,
        start_values=start_values,
    )


@pytest.mark.asyncio
async def test_deploy_sends_cmd_and_chunks(
    server_env: tuple[TCPServer, int, SessionManager],
    tmp_path: Path,
) -> None:
    server, port, _ = server_env
    data = b"deploy-bytes-" * 500
    deploy_file = tmp_path / "server_payload.bin"
    deploy_file.write_bytes(data)

    process_mgr = build_process_mgr(tmp_path, health_ok=True)
    client = make_client("agent-phase4-chunks", port)
    handler = DeployHandler(
        tcp_client=client,
        process_mgr=cast(ProcessManager, cast(object, process_mgr)),
        transfer_dir=str(tmp_path / "incoming"),
    )
    client.on_cmd_deploy = handler.handle_cmd_deploy
    client.on_file_chunk = handler.handle_file_chunk

    assert await client.connect() is True
    assert await server.send_deploy(client.agent_id, str(deploy_file)) is True

    future = server.get_deploy_result_future(client.agent_id)
    assert future is not None
    ack = await asyncio.wait_for(future, timeout=2.0)
    assert ack.status == CtrlAckStatus.DEPLOY_VERIFIED

    assembled = tmp_path / "incoming" / deploy_file.name
    assert assembled.exists()
    assert assembled.read_bytes() == data

    await client.disconnect()


@pytest.mark.asyncio
async def test_deploy_file_ack_flow(
    server_env: tuple[TCPServer, int, SessionManager],
    tmp_path: Path,
) -> None:
    server, port, _ = server_env
    data = b"a" * (CHUNK_SIZE * 2 + 123)
    deploy_file = tmp_path / "ack_payload.bin"
    deploy_file.write_bytes(data)

    process_mgr = build_process_mgr(tmp_path, health_ok=True)
    client = make_client("agent-phase4-acks", port)
    handler = DeployHandler(
        tcp_client=client,
        process_mgr=cast(ProcessManager, cast(object, process_mgr)),
        transfer_dir=str(tmp_path / "incoming"),
    )
    client.on_cmd_deploy = handler.handle_cmd_deploy
    client.on_file_chunk = handler.handle_file_chunk

    file_ack_seq: list[int] = []
    original_send_packet = client.send_packet

    async def send_packet_with_record(packet_type: PacketType, payload: bytes) -> None:
        if packet_type == PacketType.FILE_ACK:
            ack = FileAckPayload.unpack(payload)
            file_ack_seq.append(ack.seq_num)
        await original_send_packet(packet_type, payload)

    client.send_packet = send_packet_with_record  # type: ignore[method-assign]

    assert await client.connect() is True
    assert await server.send_deploy(client.agent_id, str(deploy_file)) is True

    future = server.get_deploy_result_future(client.agent_id)
    assert future is not None
    _ = await asyncio.wait_for(future, timeout=2.0)
    # deploy uses 4096-byte chunks (deploy_chunk_size in tcp_server.py)
    deploy_chunk_size = 4096
    expected_chunks = (len(data) + deploy_chunk_size - 1) // deploy_chunk_size
    assert file_ack_seq == list(range(expected_chunks))

    await client.disconnect()


@pytest.mark.asyncio
async def test_deploy_with_process_replace(
    server_env: tuple[TCPServer, int, SessionManager],
    tmp_path: Path,
) -> None:
    server, port, _ = server_env
    deploy_file = tmp_path / "build_v2.bin"
    deploy_file.write_bytes(b"new-build-v2")

    process_mgr = build_process_mgr(tmp_path, health_ok=True)
    client = make_client("agent-phase4-success", port)
    handler = DeployHandler(
        tcp_client=client,
        process_mgr=cast(ProcessManager, cast(object, process_mgr)),
        transfer_dir=str(tmp_path / "incoming"),
    )

    client.on_cmd_deploy = handler.handle_cmd_deploy
    client.on_file_chunk = handler.handle_file_chunk

    assert await client.connect() is True
    assert await server.send_deploy(client.agent_id, str(deploy_file)) is True

    future = server.get_deploy_result_future(client.agent_id)
    assert future is not None
    ack = await asyncio.wait_for(future, timeout=2.0)
    assert ack.status == CtrlAckStatus.DEPLOY_VERIFIED
    assert ack.pid == 1001
    assert process_mgr.backup_calls == 1
    assert process_mgr.kill_calls == 1
    assert process_mgr.start_calls == 1
    assert process_mgr.health_check_calls == 1
    assert process_mgr.health_check_timeouts == [30]
    assert process_mgr.process_path.read_bytes() == b"new-build-v2"

    await client.disconnect()


@pytest.mark.asyncio
async def test_deploy_rollback_on_health_failure(
    server_env: tuple[TCPServer, int, SessionManager],
    tmp_path: Path,
) -> None:
    server, port, _ = server_env
    deploy_file = tmp_path / "build_bad.bin"
    deploy_file.write_bytes(b"bad-build")

    process_mgr = build_process_mgr(
        tmp_path,
        health_ok=False,
        start_side_effect=[2001, 2002],
    )
    client = make_client("agent-phase4-rollback", port)
    handler = DeployHandler(
        tcp_client=client,
        process_mgr=cast(ProcessManager, cast(object, process_mgr)),
        transfer_dir=str(tmp_path / "incoming"),
    )

    client.on_cmd_deploy = handler.handle_cmd_deploy
    client.on_file_chunk = handler.handle_file_chunk

    assert await client.connect() is True
    assert await server.send_deploy(client.agent_id, str(deploy_file)) is True

    future = server.get_deploy_result_future(client.agent_id)
    assert future is not None
    ack = await asyncio.wait_for(future, timeout=2.0)
    assert ack.status == CtrlAckStatus.DEPLOY_ROLLBACK
    assert process_mgr.health_check_calls == 1
    assert process_mgr.health_check_timeouts == [30]
    assert process_mgr.kill_calls == 2
    assert process_mgr.rollback_calls == 1
    assert process_mgr.start_calls == 2

    await client.disconnect()


@pytest.mark.asyncio
async def test_deploy_sha256_mismatch_fails(
    server_env: tuple[TCPServer, int, SessionManager],
    tmp_path: Path,
) -> None:
    server, port, _ = server_env
    deploy_file = tmp_path / "sha_bad.bin"
    deploy_file.write_bytes(b"correct-data")

    process_mgr = build_process_mgr(tmp_path, health_ok=True)
    client = make_client("agent-phase4-sha", port)
    handler = DeployHandler(
        tcp_client=client,
        process_mgr=cast(ProcessManager, cast(object, process_mgr)),
        transfer_dir=str(tmp_path / "incoming"),
    )

    client.on_cmd_deploy = handler.handle_cmd_deploy

    tampered = False

    async def tampered_file_chunk(payload_data: bytes) -> None:
        nonlocal tampered
        if not tampered:
            chunk = FileChunkPayload.unpack(payload_data)
            if chunk.data:
                data = bytearray(chunk.data)
                data[0] ^= 0xFF
                payload_data = FileChunkPayload(
                    seq_num=chunk.seq_num,
                    data=bytes(data),
                ).pack()
            tampered = True
        await handler.handle_file_chunk(payload_data)

    client.on_file_chunk = tampered_file_chunk

    assert await client.connect() is True
    assert await server.send_deploy(client.agent_id, str(deploy_file)) is True

    future = server.get_deploy_result_future(client.agent_id)
    assert future is not None
    ack = await asyncio.wait_for(future, timeout=2.0)
    assert ack.status == CtrlAckStatus.DEPLOY_ROLLBACK
    assert process_mgr.backup_calls == 0
    assert process_mgr.start_calls == 0
    assert process_mgr.health_check_calls == 0

    await client.disconnect()
