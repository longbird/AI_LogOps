from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agent.core.tcp_client import TCPClient
from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
from server.storage.manager import StorageManager

TOKEN = "integrationtest" + "x" * 49


@pytest.fixture
async def server_env(
    tmp_path: Path,
) -> AsyncIterator[tuple[TCPServer, int, StorageManager]]:
    session_mgr = SessionManager(max_agents=10)
    storage_mgr = StorageManager(
        base_dir=str(tmp_path / "storage"), max_retention_days=1
    )
    server = TCPServer(
        host="127.0.0.1",
        port=0,
        session_mgr=session_mgr,
        auth_token=TOKEN,
        storage_mgr=storage_mgr,
    )
    port = await server.start()
    yield server, port, storage_mgr
    await server.stop()


def make_client(agent_id: str, port: int) -> TCPClient:
    return TCPClient(
        agent_id=agent_id,
        version="1.0.0",
        token=TOKEN,
        host="127.0.0.1",
        port=port,
    )


async def wait_for_file(
    path: Path, timeout: float = 1.5, interval: float = 0.05
) -> None:
    end_time = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end_time:
        if path.exists():
            return
        await asyncio.sleep(interval)
    assert path.exists()


@pytest.mark.asyncio
async def test_log_history_transfer(
    server_env: tuple[TCPServer, int, StorageManager],
) -> None:
    _, port, storage_mgr = server_env
    client = make_client("agent-phase2-hist", port)

    connected = await client.connect()
    assert connected is True

    data = b"history line 1\nhistory line 2\n"
    await client.send_log_history("app.log", data)

    expected_path = (
        Path(storage_mgr.base_dir) / "logs" / "agent-phase2-hist" / "app.log"
    )
    await wait_for_file(expected_path)
    assert expected_path.read_bytes() == data

    await client.disconnect()


@pytest.mark.asyncio
async def test_realtime_log_streaming(
    server_env: tuple[TCPServer, int, StorageManager],
) -> None:
    _, port, storage_mgr = server_env
    client = make_client("agent-phase2-real", port)

    connected = await client.connect()
    assert connected is True

    await client.send_log_line("stream.log", "line one")
    await client.send_log_line("stream.log", "line two")
    await client.send_log_line("stream.log", "line three")

    expected_path = (
        Path(storage_mgr.base_dir)
        / "logs"
        / "agent-phase2-real"
        / "realtime"
        / "stream.log"
    )
    await wait_for_file(expected_path)

    end_time = asyncio.get_running_loop().time() + 1.5
    while asyncio.get_running_loop().time() < end_time:
        if (
            expected_path.read_text(encoding="utf-8")
            == "line one\nline two\nline three\n"
        ):
            break
        await asyncio.sleep(0.05)

    assert (
        expected_path.read_text(encoding="utf-8") == "line one\nline two\nline three\n"
    )
    await client.disconnect()


def test_storage_manager_get_logs(tmp_path: Path) -> None:
    storage_mgr = StorageManager(base_dir=str(tmp_path / "storage"))

    _ = storage_mgr.save_log_history("agent-phase2", "a.log", b"a")
    _ = storage_mgr.save_log_history("agent-phase2", "b.log", b"b")
    storage_mgr.append_realtime_log("agent-phase2", "tail.log", "tail")

    logs = storage_mgr.get_agent_logs("agent-phase2")
    expected = {
        str(Path(storage_mgr.base_dir) / "logs" / "agent-phase2" / "a.log"),
        str(Path(storage_mgr.base_dir) / "logs" / "agent-phase2" / "b.log"),
        str(
            Path(storage_mgr.base_dir)
            / "logs"
            / "agent-phase2"
            / "realtime"
            / "tail.log"
        ),
    }
    assert set(logs) == expected


def test_storage_cleanup(tmp_path: Path) -> None:
    storage_mgr = StorageManager(
        base_dir=str(tmp_path / "storage"), max_retention_days=1
    )
    old_log = Path(storage_mgr.save_log_history("agent-phase2", "old.log", b"old-data"))

    old_ts = time.time() - (2 * 24 * 60 * 60)
    os.utime(old_log, (old_ts, old_ts))

    removed = storage_mgr.cleanup_old_logs()
    assert removed == 1
    assert not old_log.exists()


def test_save_and_get_report(tmp_path: Path) -> None:
    storage_mgr = StorageManager(base_dir=str(tmp_path / "storage"))

    saved_path = storage_mgr.save_report(
        "agent-phase2",
        "report.md",
        "# Report\n\nEverything OK",
    )
    reports = storage_mgr.get_agent_reports("agent-phase2")

    assert reports == [saved_path]
    assert Path(saved_path).read_text(encoding="utf-8") == "# Report\n\nEverything OK"
