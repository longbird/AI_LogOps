from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false

import asyncio
import importlib
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent.core.deploy_handler import DeployHandler
from agent.core.process_mgr import ProcessManager
from agent.core.tcp_client import TCPClient
from agent.updater.self_update import SelfUpdater
from server.ai.pipeline import AIPipeline, PipelineResult
from server.ai.provider import BaseAIProvider
from server.core.health_monitor import HealthMonitor
from server.core.session_mgr import SessionManager
from server.core.tcp_server import TCPServer
from server.dashboard.app import create_app
from server.storage.manager import StorageManager
from server.telegram.ai_commands import AICommandHandler
from server.telegram.deploy_commands import DeployCommandHandler
from server.telegram.handler import ParsedCommand, TelegramHandler
from server.telegram.rate_limiter import RateLimiter
from shared.models import AgentInfo, AgentSession, AgentState
from shared.protocol import (
    HEADER_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    CHUNK_SIZE,
    CtrlAckStatus,
    FileAckPayload,
    Packet,
    PacketHeader,
    PacketType,
)
from shared.utils import audit_log


TOKEN = "integration-final-" + "x" * 46


class MockAIProvider(BaseAIProvider):
    def __init__(self, responses: list[str]):
        super().__init__(api_key="test", model="mock")
        self.provider_name = "mock"
        self._responses = responses
        self._idx = 0

    async def _call_api(self, prompt: str, system: str = "") -> str:
        del prompt, system
        if self._idx < len(self._responses):
            result = self._responses[self._idx]
            self._idx += 1
            return result
        return "{}"


class _DummyWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    async def drain(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None

    def get_extra_info(self, name: str) -> object:
        del name
        return None

    def write(self, data: bytes) -> None:
        del data


class _StubProcessManager:
    def __init__(self, process_path: Path, health_ok: bool = True) -> None:
        self.process_path = process_path
        self.health_ok = health_ok
        self.backup_calls = 0
        self.kill_calls = 0
        self.start_calls = 0
        self.rollback_calls = 0
        self.health_calls = 0

    def backup_current(self) -> str:
        self.backup_calls += 1
        return "backup"

    def kill(self) -> bool:
        self.kill_calls += 1
        return True

    def start(self) -> int:
        self.start_calls += 1
        return 4242

    async def health_check(self, timeout: int = 30) -> bool:
        del timeout
        self.health_calls += 1
        return self.health_ok

    def rollback(self) -> bool:
        self.rollback_calls += 1
        return True


@pytest.fixture
async def server_env(
    tmp_path: Path,
) -> AsyncIterator[tuple[TCPServer, int, SessionManager, StorageManager]]:
    session_mgr = SessionManager(max_agents=10)
    storage_mgr = StorageManager(str(tmp_path / "storage"))
    server = TCPServer(
        host="127.0.0.1",
        port=0,
        session_mgr=session_mgr,
        auth_token=TOKEN,
        storage_mgr=storage_mgr,
    )
    port = await server.start()
    yield server, port, session_mgr, storage_mgr
    await server.stop()


def _make_client(agent_id: str, port: int) -> TCPClient:
    return TCPClient(
        agent_id=agent_id,
        version="1.0.0",
        token=TOKEN,
        host="127.0.0.1",
        port=port,
    )


async def _wait_for(
    condition: Any,
    timeout: float = 2.0,
    interval: float = 0.05,
) -> None:
    loop = asyncio.get_running_loop()
    end_time = loop.time() + timeout
    while loop.time() < end_time:
        if condition():
            return
        await asyncio.sleep(interval)
    assert condition()


async def _read_packet(reader: asyncio.StreamReader) -> tuple[PacketType, bytes]:
    header = await reader.readexactly(HEADER_SIZE)
    packet_type, payload_len = PacketHeader.unpack(header)
    payload = await reader.readexactly(payload_len)
    return packet_type, payload


def _install_pywin32_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    win32serviceutil = cast(Any, ModuleType("win32serviceutil"))

    class _ServiceFramework:
        def __init__(self, args: object):
            self.args = args

        def ReportServiceStatus(self, status: int) -> None:
            del status

    win32serviceutil.ServiceFramework = _ServiceFramework
    win32serviceutil.HandleCommandLine = lambda cls: None

    win32service = cast(Any, ModuleType("win32service"))
    win32service.SERVICE_STOP_PENDING = 3
    win32service.SERVICE_AUTO_START = 2

    win32event = cast(Any, ModuleType("win32event"))
    win32event.CreateEvent = lambda a, b, c, d: 1
    win32event.SetEvent = lambda handle: None
    win32event.WaitForSingleObject = lambda handle, timeout: 0

    servicemanager = cast(Any, ModuleType("servicemanager"))
    servicemanager.EVENTLOG_INFORMATION_TYPE = 4
    servicemanager.PYS_SERVICE_STARTED = 1
    servicemanager.PYS_SERVICE_STOPPED = 2
    servicemanager.LogMsg = lambda *args, **kwargs: None

    monkeypatch.setitem(sys.modules, "win32serviceutil", win32serviceutil)
    monkeypatch.setitem(sys.modules, "win32service", win32service)
    monkeypatch.setitem(sys.modules, "win32event", win32event)
    monkeypatch.setitem(sys.modules, "servicemanager", servicemanager)


@pytest.mark.asyncio
async def test_tcp_full_chain_auth_log_heartbeat_deploy_disconnect(
    server_env: tuple[TCPServer, int, SessionManager, StorageManager],
    tmp_path: Path,
) -> None:
    server, port, session_mgr, storage_mgr = server_env

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        Packet.build(
            PacketType.AUTH,
            AuthPayload(
                agent_id="agent-final-auth", version="1.0.0", token=TOKEN
            ).pack(),
        )
    )
    await writer.drain()
    packet_type, payload = await _read_packet(reader)
    auth_ack = AuthAckPayload.unpack(payload)
    assert packet_type == PacketType.AUTH_ACK
    assert auth_ack.status == AuthStatus.SUCCESS
    writer.close()
    await writer.wait_closed()

    process_path = tmp_path / "agent" / "current.bin"
    process_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.write_bytes(b"v1")
    process_mgr = _StubProcessManager(process_path=process_path, health_ok=True)

    client = _make_client("agent-final-flow", port)
    deploy_handler = DeployHandler(
        tcp_client=client,
        process_mgr=cast(ProcessManager, cast(object, process_mgr)),
        transfer_dir=str(tmp_path / "incoming"),
    )
    client.on_cmd_deploy = deploy_handler.handle_cmd_deploy
    client.on_file_chunk = deploy_handler.handle_file_chunk

    file_ack_seqs: list[int] = []
    original_send_packet = client.send_packet

    async def _record_file_ack(packet_type: PacketType, packet_payload: bytes) -> None:
        if packet_type == PacketType.FILE_ACK:
            file_ack_seqs.append(FileAckPayload.unpack(packet_payload).seq_num)
        await original_send_packet(packet_type, packet_payload)

    client.send_packet = _record_file_ack  # type: ignore[method-assign]

    assert await client.connect() is True
    assert client.session_id != ""
    await _wait_for(lambda: session_mgr.get_session("agent-final-flow") is not None)

    await client.send_log_history("history.log", b"HISTORY-LINE")
    await client.send_log_line("runtime.log", "REALTIME-LINE")
    await client.send_heartbeat()

    deploy_payload = tmp_path / "payload.bin"
    deploy_payload.write_bytes(b"deploy-content-" * 700)
    assert await server.send_deploy("agent-final-flow", str(deploy_payload)) is True

    deploy_future = server.get_deploy_result_future("agent-final-flow")
    assert deploy_future is not None
    deploy_ack = await asyncio.wait_for(deploy_future, timeout=3.0)
    assert deploy_ack.status == CtrlAckStatus.DEPLOY_VERIFIED

    expected_chunks = (deploy_payload.stat().st_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    assert file_ack_seqs == list(range(expected_chunks))

    history_path = (
        Path(storage_mgr.base_dir) / "logs" / "agent-final-flow" / "history.log"
    )
    realtime_path = (
        Path(storage_mgr.base_dir)
        / "logs"
        / "agent-final-flow"
        / "realtime"
        / "runtime.log"
    )
    assert history_path.exists()
    assert history_path.read_bytes() == b"HISTORY-LINE"
    assert realtime_path.exists()
    assert "REALTIME-LINE" in realtime_path.read_text(encoding="utf-8")

    await client.disconnect()
    await _wait_for(lambda: session_mgr.get_session("agent-final-flow") is None)


@pytest.mark.asyncio
async def test_ai_pipeline_three_stage_with_step35_validation(tmp_path: Path) -> None:
    responses = [
        "# Analysis Report\n\nRoot cause found.",
        "# Improvement Plan\n\n1. Fix parser",
        "# Fixed Code\n\ndef run():\n    return 'ok'\n",
        '{"severity":"LOW","issues":[],"confidence_score":91}',
    ]
    pipeline = AIPipeline(
        provider=MockAIProvider(responses=responses), storage_dir=str(tmp_path)
    )

    result = await pipeline.run_full(
        log_content="ERROR: timeout while connecting",
        source_code="def run():\n    return None\n",
    )

    assert isinstance(result, PipelineResult)
    assert "# Analysis Report" in result.analysis_report
    assert "# Improvement Plan" in result.improvement_plan
    assert "# Fixed Code" in result.fixed_code
    assert result.validation_report is not None
    assert result.validation_report["syntax_check"] == "PASS"
    assert result.validation_report["deploy_allowed"] is True
    assert result.validation_report["confidence_score"] == 91


@pytest.mark.asyncio
async def test_telegram_auto_to_ai_and_deploy_chain_with_security(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_mgr = SessionManager()
    session_mgr.sessions["agent-chain"] = AgentSession(
        agent_info=AgentInfo(
            agent_id="agent-chain", version="1.0.0", state=AgentState.CONNECTED
        ),
        session_id="session-chain",
        writer=_DummyWriter(),
        log_buffer=["ERROR: chain test"],
    )

    pipeline = cast(Any, SimpleNamespace())
    pipeline._storage_manager = SimpleNamespace(base_dir=tmp_path)
    pipeline.run_full = AsyncMock(
        return_value=PipelineResult(
            analysis_report="# Analysis",
            improvement_plan="# Plan",
            fixed_code="print('fixed')",
            validation_report={"confidence_score": 88, "deploy_allowed": True},
        )
    )

    ai_handler = AICommandHandler(
        pipeline=cast(AIPipeline, pipeline), session_mgr=session_mgr
    )

    tcp_server = AsyncMock()
    tcp_server.send_deploy = AsyncMock(return_value=True)
    deploy_file = tmp_path / "reports" / "ai_pipeline" / "Fixed_Source_Code.py"
    deploy_file.parent.mkdir(parents=True, exist_ok=True)
    deploy_file.write_text("print('fixed')\n", encoding="utf-8")
    deploy_handler = DeployCommandHandler(
        tcp_server=tcp_server,
        session_mgr=session_mgr,
        storage_dir=str(tmp_path),
    )

    audit_path = tmp_path / "audit.jsonl"

    def _audit_to_temp(action: str, agent_id: str, user: str, detail: str) -> None:
        audit_log(
            action=action,
            agent_id=agent_id,
            user=user,
            detail=detail,
            log_path=str(audit_path),
        )

    monkeypatch.setattr("server.telegram.handler.audit_log", _audit_to_temp)

    handler = TelegramHandler(admin_chat_ids=[101])
    handler.rate_limiter = RateLimiter(window=10, max_requests=1)

    async def orchestrated_auto(cmd: ParsedCommand) -> str:
        ai_result = await ai_handler.cmd_auto(cmd)
        deploy_cmd = ParsedCommand(
            command="deploy",
            args=[cmd.args[0]],
            chat_id=cmd.chat_id,
            raw_text=f"/deploy {cmd.args[0]}",
        )
        deploy_result = await deploy_handler.cmd_deploy(deploy_cmd)
        return f"{ai_result}\n{deploy_result}"

    handler.register("auto", orchestrated_auto)

    response = await handler.handle("/auto agent-chain print('x')", chat_id=101)
    blocked = await handler.handle("/auto agent-chain print('x')", chat_id=101)

    assert "Auto pipeline complete for agent-chain." in response
    assert "Deploy initiated for agent-chain" in response
    assert blocked == "Too many requests. Please wait."
    pipeline.run_full.assert_awaited_once()
    tcp_server.send_deploy.assert_awaited_once_with("agent-chain", str(deploy_file))

    entries = [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["action"] == "AUTO"
    assert entries[0]["user"] == "telegram:101"


@pytest.mark.asyncio
async def test_dashboard_wiring_login_and_protected_routes(tmp_path: Path) -> None:
    session_mgr = SessionManager()
    session = session_mgr.create_session("agent-dash", "1.2.3", _DummyWriter())
    assert session is not None
    session.log_buffer.append("line-from-dashboard")

    storage_mgr = StorageManager(str(tmp_path / "storage"))
    _ = storage_mgr.save_report("agent-dash", "report.md", "# report")

    app: FastAPI = create_app(session_mgr=session_mgr, storage_mgr=storage_mgr)
    route_paths = {route.path for route in app.routes}
    assert {"/dashboard", "/logs", "/reports", "/deploys"}.issubset(route_paths)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        login = await client.post(
            "/login", data={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/"
        token_cookie = login.cookies.get("access_token")
        assert token_cookie is not None

        cookies = {"access_token": token_cookie}
        dashboard = await client.get("/dashboard", cookies=cookies)
        logs = await client.get("/logs", cookies=cookies)
        reports = await client.get("/reports", cookies=cookies)
        log_partial = await client.get("/api/logs/agent-dash", cookies=cookies)

    assert dashboard.status_code == 200
    assert logs.status_code == 200
    assert reports.status_code == 200
    assert log_partial.status_code == 200
    assert "agent-dash" in dashboard.text
    assert "Log Viewer" in logs.text
    assert "report.md" in reports.text
    assert "line-from-dashboard" in log_partial.text


def test_module_imports_and_key_components_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(project_root))
    _install_pywin32_stubs(monkeypatch)

    imported_modules: dict[str, ModuleType] = {}
    for base in ("shared", "server", "agent"):
        root = project_root / base
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(project_root).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                module_name = ".".join(parts[:-1])
            else:
                module_name = ".".join(parts)
            if not module_name:
                continue
            imported_modules[module_name] = importlib.import_module(module_name)

    assert len(imported_modules) >= 30
    assert hasattr(imported_modules["server.core.tcp_server"], "TCPServer")
    assert hasattr(imported_modules["agent.core.tcp_client"], "TCPClient")
    assert hasattr(imported_modules["server.ai.pipeline"], "AIPipeline")
    assert hasattr(imported_modules["server.core.session_mgr"], "SessionManager")
    assert hasattr(imported_modules["agent.updater.self_update"], "SelfUpdater")
    assert hasattr(
        imported_modules["agent.service.win_service"], "AILogOpsAgentService"
    )


@pytest.mark.asyncio
async def test_health_monitor_cpu_mem_disk_alerts_are_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifications: list[str] = []

    async def _notify(msg: str) -> None:
        notifications.append(msg)

    monitor = HealthMonitor(
        {
            "check_interval": 1,
            "cpu_threshold": 10,
            "memory_threshold": 10,
            "disk_threshold": 10,
            "alert_cooldown": 0,
        },
        telegram_notifier=_notify,
    )

    @dataclass
    class _Mem:
        percent: float

    @dataclass
    class _Disk:
        percent: float

    monkeypatch.setattr(
        "server.core.health_monitor.psutil.cpu_percent", lambda interval=1: 95.0
    )
    monkeypatch.setattr(
        "server.core.health_monitor.psutil.virtual_memory", lambda: _Mem(percent=92.0)
    )
    monkeypatch.setattr(
        "server.core.health_monitor.psutil.disk_usage", lambda path: _Disk(percent=91.0)
    )

    _ = await monitor._check_cpu()
    _ = await monitor._check_cpu()
    cpu_result = await monitor._check_cpu()
    mem_result = await monitor._check_memory()
    disk_result = await monitor._check_disk()

    assert cpu_result["alert"] is True
    assert mem_result["alert"] is True
    assert disk_result["alert"] is True
    assert any("CPU" in msg for msg in notifications)
    assert any("메모리" in msg for msg in notifications)
    assert any("디스크" in msg for msg in notifications)


def test_self_update_and_win_service_wiring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="1.0.0")
    payload = b"new-binary-v1"

    import hashlib

    assert (
        asyncio.run(
            updater.receive_update(payload, hashlib.sha256(payload).hexdigest())
        )
        is True
    )

    bat_path = Path(updater.generate_updater_bat())
    assert bat_path.exists()
    assert "net stop AILogOps-Agent" in bat_path.read_text(encoding="utf-8")

    updater.write_version_file("0.9.0")
    updater.backup_dir.mkdir(parents=True, exist_ok=True)
    (updater.backup_dir / "agent_0.9.0.exe").write_bytes(b"old")
    (updater.backup_dir / "agent_0.9.9.exe").write_bytes(b"latest-good")
    updater.current_exe_path.write_bytes(b"broken")

    assert updater.verify_version_on_boot() is False
    assert updater.current_exe_path.read_bytes() == b"latest-good"
    assert updater.version_file_path.read_text(encoding="utf-8").strip() == "1.0.0"

    _install_pywin32_stubs(monkeypatch)
    sys.modules.pop("agent.service.win_service", None)
    win_service = importlib.import_module("agent.service.win_service")
    svc = win_service.AILogOpsAgentService

    assert svc._svc_name_ == "AILogOps-Agent"
    assert svc._svc_display_name_ == "AI-LogOps Agent Service"
    assert svc._svc_start_type_ == win_service.win32service.SERVICE_AUTO_START
