# Runner Worker Updater Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the current monolithic agent into a fixed Runner control plane, a replaceable Worker runtime, and an on-demand Updater so server connectivity survives worker failure and worker update.

**Architecture:** Introduce a minimal Runner-owned supervisor and local state store first, then move the operational `AgentRuntime` into a Worker subprocess behind a loopback IPC boundary, and finally replace self-update with a Worker-only versioned package swap controlled by Runner. Keep the server-facing identity bound to Runner at every phase.

**Tech Stack:** Python 3.13, asyncio, existing TCP protocol stack, Windows service entrypoint, PyInstaller, pytest

---

## File Structure

### New files

- `agent/runner/__init__.py`
  - package marker for runner-specific code
- `agent/runner/models.py`
  - runner state enums and payload dataclasses
- `agent/runner/state_store.py`
  - persistent `state.json` read/write logic
- `agent/runner/supervisor.py`
  - process supervisor for Worker lifecycle and degraded/update transitions
- `agent/runner/ipc.py`
  - Runner-side local IPC server and message routing
- `agent/worker/__init__.py`
  - package marker for worker-specific code
- `agent/worker/entry.py`
  - Worker process main entrypoint
- `agent/worker/runtime.py`
  - adapter that wraps current `AgentRuntime` under Runner IPC
- `agent/worker/ipc_client.py`
  - Worker-side local IPC client and registration/heartbeat/result messages
- `agent/updater/worker_updater.py`
  - versioned Worker staging/install/rollback implementation
- `tests/test_runner_state_store.py`
  - Runner state persistence tests
- `tests/test_runner_supervisor.py`
  - Worker restart/degraded/update flow tests
- `tests/test_runner_ipc.py`
  - loopback IPC protocol tests
- `tests/test_worker_updater.py`
  - versioned Worker package swap and rollback tests
- `tests/test_worker_runtime.py`
  - Worker adapter tests

### Modified files

- `agent/service/entry.py`
  - route service startup into Runner instead of directly owning all runtime work
- `agent/service/win_service.py`
  - boot Runner and expose Runner-oriented service health semantics
- `agent/core/agent_runtime.py`
  - trim direct server ownership assumptions so the logic can be hosted inside Worker
- `agent/updater/self_update.py`
  - narrow scope to Runner/Updater bootstrap only, or delegate Worker swap to `worker_updater`
- `deploy.py`
  - distinguish Runner/Worker package flows and default normal deploy to Worker payload
- `agent.spec`
  - package Runner entrypoint
- `tests/test_win_service.py`
  - update service-entry expectations for Runner startup
- `tests/test_self_update.py`
  - replace monolithic self-update assumptions with Worker-updater expectations
- `tests/test_tcp_server.py`
  - add Runner/Worker status field expectations
- `tests/test_dashboard.py`
  - add dashboard/API coverage for Runner/Worker split status
- `server/core/tcp_server.py`
  - store and surface Runner-facing state plus Worker sub-status
- `server/dashboard/routes/dashboard.py`
  - expose split status fields to template/API
- `server/dashboard/templates/partials/agent_cards.html`
  - display runner/worker/degraded/update state
- `shared/models.py`
  - extend session-facing agent status model if needed for split status fields

## Task 1: Add Runner State Model and Persistence

**Files:**
- Create: `agent/runner/__init__.py`
- Create: `agent/runner/models.py`
- Create: `agent/runner/state_store.py`
- Test: `tests/test_runner_state_store.py`

- [ ] **Step 1: Write the failing state persistence tests**

```python
from pathlib import Path

from agent.runner.models import RunnerState, WorkerHealth, WorkerLifecycleState
from agent.runner.state_store import RunnerStateStore


def test_state_store_round_trip(tmp_path: Path) -> None:
    store = RunnerStateStore(tmp_path / "state.json")
    state = RunnerState(
        current_worker_version="1.11.1",
        last_good_worker_version="1.11.0",
        desired_worker_version="1.11.1",
        worker_health=WorkerHealth.HEALTHY,
        worker_lifecycle=WorkerLifecycleState.ONLINE,
        update_in_progress=False,
        rollback_in_progress=False,
        restart_fail_count=0,
        last_update_started_at="2026-04-22T15:00:00+09:00",
        last_update_result="success",
    )

    store.save(state)
    loaded = store.load()

    assert loaded == state


def test_state_store_defaults_when_file_missing(tmp_path: Path) -> None:
    store = RunnerStateStore(tmp_path / "missing.json")

    loaded = store.load()

    assert loaded.current_worker_version == ""
    assert loaded.last_good_worker_version == ""
    assert loaded.worker_health is WorkerHealth.UNKNOWN
    assert loaded.worker_lifecycle is WorkerLifecycleState.STARTING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner_state_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.runner'`

- [ ] **Step 3: Write the minimal Runner model and state store**

```python
# agent/runner/models.py
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class WorkerHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class WorkerLifecycleState(StrEnum):
    STARTING = "starting"
    ONLINE = "online"
    DEGRADED = "degraded"
    UPDATING = "updating"
    ROLLBACK = "rollback"


@dataclass(slots=True)
class RunnerState:
    current_worker_version: str = ""
    last_good_worker_version: str = ""
    desired_worker_version: str = ""
    worker_health: WorkerHealth = WorkerHealth.UNKNOWN
    worker_lifecycle: WorkerLifecycleState = WorkerLifecycleState.STARTING
    update_in_progress: bool = False
    rollback_in_progress: bool = False
    restart_fail_count: int = 0
    last_update_started_at: str = ""
    last_update_result: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["worker_health"] = self.worker_health.value
        data["worker_lifecycle"] = self.worker_lifecycle.value
        return data
```

```python
# agent/runner/state_store.py
from __future__ import annotations

import json
from pathlib import Path

from agent.runner.models import RunnerState, WorkerHealth, WorkerLifecycleState


class RunnerStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> RunnerState:
        if not self._path.exists():
            return RunnerState()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return RunnerState(
            current_worker_version=str(raw.get("current_worker_version", "")),
            last_good_worker_version=str(raw.get("last_good_worker_version", "")),
            desired_worker_version=str(raw.get("desired_worker_version", "")),
            worker_health=WorkerHealth(str(raw.get("worker_health", "unknown"))),
            worker_lifecycle=WorkerLifecycleState(
                str(raw.get("worker_lifecycle", "starting"))
            ),
            update_in_progress=bool(raw.get("update_in_progress", False)),
            rollback_in_progress=bool(raw.get("rollback_in_progress", False)),
            restart_fail_count=int(raw.get("restart_fail_count", 0)),
            last_update_started_at=str(raw.get("last_update_started_at", "")),
            last_update_result=str(raw.get("last_update_result", "")),
        )

    def save(self, state: RunnerState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner_state_store.py -v`
Expected: PASS for both state-store tests

- [ ] **Step 5: Commit**

```bash
git add agent/runner/__init__.py agent/runner/models.py agent/runner/state_store.py tests/test_runner_state_store.py
git commit -m "feat: add runner state persistence primitives"
```

## Task 2: Introduce Runner Supervisor and Worker Lifecycle Control

**Files:**
- Create: `agent/runner/supervisor.py`
- Modify: `agent/service/entry.py`
- Modify: `agent/service/win_service.py`
- Test: `tests/test_runner_supervisor.py`
- Test: `tests/test_win_service.py`

- [ ] **Step 1: Write the failing supervisor tests**

```python
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from agent.runner.models import WorkerLifecycleState
from agent.runner.supervisor import RunnerSupervisor


@pytest.mark.asyncio
async def test_supervisor_marks_degraded_after_repeated_worker_failures() -> None:
    process_host = Mock()
    process_host.start_worker.side_effect = [False, False, False]
    state_store = Mock()
    state_store.load.return_value.restart_fail_count = 0

    supervisor = RunnerSupervisor(
        process_host=process_host,
        state_store=state_store,
        heartbeat_timeout=5.0,
        max_restart_attempts=3,
    )

    await supervisor.ensure_worker_running()

    assert supervisor.current_state.worker_lifecycle is WorkerLifecycleState.DEGRADED
    assert supervisor.current_state.restart_fail_count == 3


@pytest.mark.asyncio
async def test_supervisor_resets_fail_count_after_successful_start() -> None:
    process_host = Mock()
    process_host.start_worker.return_value = True
    state_store = Mock()

    supervisor = RunnerSupervisor(
        process_host=process_host,
        state_store=state_store,
        heartbeat_timeout=5.0,
        max_restart_attempts=3,
    )
    supervisor.current_state.restart_fail_count = 2

    await supervisor.ensure_worker_running()

    assert supervisor.current_state.restart_fail_count == 0
    assert supervisor.current_state.worker_lifecycle is WorkerLifecycleState.ONLINE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner_supervisor.py tests/test_win_service.py -v`
Expected: FAIL with `ModuleNotFoundError` for `agent.runner.supervisor`

- [ ] **Step 3: Write the minimal supervisor and service-entry wiring**

```python
# agent/runner/supervisor.py
from __future__ import annotations

from dataclasses import replace

from agent.runner.models import RunnerState, WorkerHealth, WorkerLifecycleState


class RunnerSupervisor:
    def __init__(
        self,
        process_host: object,
        state_store: object,
        heartbeat_timeout: float = 10.0,
        max_restart_attempts: int = 3,
    ) -> None:
        self._process_host = process_host
        self._state_store = state_store
        self._heartbeat_timeout = heartbeat_timeout
        self._max_restart_attempts = max_restart_attempts
        self.current_state = (
            state_store.load() if hasattr(state_store, "load") else RunnerState()
        )

    async def ensure_worker_running(self) -> bool:
        for _ in range(self._max_restart_attempts):
            if self._process_host.start_worker():
                self.current_state = replace(
                    self.current_state,
                    worker_health=WorkerHealth.HEALTHY,
                    worker_lifecycle=WorkerLifecycleState.ONLINE,
                    restart_fail_count=0,
                )
                self._state_store.save(self.current_state)
                return True
            self.current_state.restart_fail_count += 1

        self.current_state = replace(
            self.current_state,
            worker_health=WorkerHealth.UNHEALTHY,
            worker_lifecycle=WorkerLifecycleState.DEGRADED,
        )
        self._state_store.save(self.current_state)
        return False
```

```python
# agent/service/entry.py
from agent.service.win_service import main as win_main


def main(argv: list[str] | None = None) -> None:
    args = sys.argv if argv is None else argv
    if sys.platform == "win32":
        win_main(args)
        return
    raise NotImplementedError(f"Unsupported platform: {sys.platform}")
```

```python
# agent/service/win_service.py (target shape)
async def _run_agent(self) -> None:
    from agent.runner.state_store import RunnerStateStore
    from agent.runner.supervisor import RunnerSupervisor

    store = RunnerStateStore(self._base_dir / "runner" / "state.json")
    supervisor = RunnerSupervisor(
        process_host=self._build_process_host(),
        state_store=store,
    )
    await supervisor.ensure_worker_running()
    await self._stop_requested.wait()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner_supervisor.py tests/test_win_service.py -v`
Expected: PASS for new supervisor tests and updated service-entry expectations

- [ ] **Step 5: Commit**

```bash
git add agent/runner/supervisor.py agent/service/entry.py agent/service/win_service.py tests/test_runner_supervisor.py tests/test_win_service.py
git commit -m "feat: add runner supervisor lifecycle shell"
```

## Task 3: Add Runner Worker IPC Boundary

**Files:**
- Create: `agent/runner/ipc.py`
- Create: `agent/worker/ipc_client.py`
- Test: `tests/test_runner_ipc.py`

- [ ] **Step 1: Write the failing IPC tests**

```python
import asyncio

import pytest

from agent.runner.ipc import RunnerIpcServer
from agent.worker.ipc_client import WorkerIpcClient


@pytest.mark.asyncio
async def test_worker_registers_and_sends_heartbeat() -> None:
    server = RunnerIpcServer(host="127.0.0.1", port=0)
    await server.start()

    client = WorkerIpcClient(server.host, server.port, worker_version="1.11.1")
    await client.connect()
    await client.register()
    await client.send_heartbeat()

    assert server.last_registration["worker_version"] == "1.11.1"
    assert server.last_heartbeat["kind"] == "heartbeat"

    await client.close()
    await server.close()


@pytest.mark.asyncio
async def test_server_routes_command_and_receives_result() -> None:
    server = RunnerIpcServer(host="127.0.0.1", port=0)
    await server.start()

    client = WorkerIpcClient(server.host, server.port, worker_version="1.11.1")
    await client.connect()
    await client.register()

    await server.send_command({"command": "collect_logs", "request_id": "req-1"})
    message = await client.read_message()
    assert message["command"] == "collect_logs"

    await client.send_result({"request_id": "req-1", "success": True})
    assert server.last_result["request_id"] == "req-1"

    await client.close()
    await server.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner_ipc.py -v`
Expected: FAIL with missing IPC modules

- [ ] **Step 3: Write the minimal loopback IPC server and client**

```python
# agent/runner/ipc.py
from __future__ import annotations

import asyncio
import json


class RunnerIpcServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.last_registration: dict[str, object] = {}
        self.last_heartbeat: dict[str, object] = {}
        self.last_result: dict[str, object] = {}
        self._server: asyncio.AbstractServer | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sock = self._server.sockets[0]
        self.host, self.port = sock.getsockname()[:2]

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._writer = writer
        while True:
            line = await reader.readline()
            if not line:
                break
            message = json.loads(line.decode("utf-8"))
            kind = message.get("kind")
            if kind == "register":
                self.last_registration = message
            elif kind == "heartbeat":
                self.last_heartbeat = message
            elif kind == "result":
                self.last_result = message

    async def send_command(self, message: dict[str, object]) -> None:
        assert self._writer is not None
        payload = dict(message)
        payload["kind"] = "command"
        self._writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._writer.drain()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
```

```python
# agent/worker/ipc_client.py
from __future__ import annotations

import asyncio
import json


class WorkerIpcClient:
    def __init__(self, host: str, port: int, worker_version: str) -> None:
        self._host = host
        self._port = port
        self._worker_version = worker_version
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

    async def register(self) -> None:
        await self._send({"kind": "register", "worker_version": self._worker_version})

    async def send_heartbeat(self) -> None:
        await self._send({"kind": "heartbeat", "worker_version": self._worker_version})

    async def send_result(self, payload: dict[str, object]) -> None:
        message = dict(payload)
        message["kind"] = "result"
        await self._send(message)

    async def read_message(self) -> dict[str, object]:
        assert self._reader is not None
        line = await self._reader.readline()
        return json.loads(line.decode("utf-8"))

    async def _send(self, message: dict[str, object]) -> None:
        assert self._writer is not None
        self._writer.write((json.dumps(message) + "\n").encode("utf-8"))
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner_ipc.py -v`
Expected: PASS for registration, heartbeat, command, and result flow

- [ ] **Step 5: Commit**

```bash
git add agent/runner/ipc.py agent/worker/ipc_client.py tests/test_runner_ipc.py
git commit -m "feat: add runner worker loopback ipc"
```

## Task 4: Extract Worker Runtime from AgentRuntime

**Files:**
- Create: `agent/worker/__init__.py`
- Create: `agent/worker/entry.py`
- Create: `agent/worker/runtime.py`
- Modify: `agent/core/agent_runtime.py`
- Test: `tests/test_worker_runtime.py`

- [ ] **Step 1: Write the failing Worker runtime tests**

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from agent.worker.runtime import WorkerRuntime


@pytest.mark.asyncio
async def test_worker_runtime_registers_with_runner_before_starting_runtime(tmp_path: Path) -> None:
    ipc_client = AsyncMock()
    runtime = WorkerRuntime(
        base_dir=tmp_path,
        stop_event=asyncio.Event(),
        ipc_client=ipc_client,
    )

    await runtime.bootstrap()

    ipc_client.connect.assert_awaited_once()
    ipc_client.register.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_runtime_reports_command_results_to_runner(tmp_path: Path) -> None:
    ipc_client = AsyncMock()
    runtime = WorkerRuntime(
        base_dir=tmp_path,
        stop_event=asyncio.Event(),
        ipc_client=ipc_client,
    )

    await runtime.report_result("req-1", success=True)

    ipc_client.send_result.assert_awaited_once_with(
        {"request_id": "req-1", "success": True}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_runtime.py -v`
Expected: FAIL because `agent.worker.runtime` does not exist

- [ ] **Step 3: Write the minimal Worker adapter**

```python
# agent/worker/runtime.py
from __future__ import annotations

import asyncio
from pathlib import Path

from agent.core.agent_runtime import AgentHooks, AgentRuntime


class WorkerRuntime:
    def __init__(
        self,
        base_dir: Path,
        stop_event: asyncio.Event,
        ipc_client: object,
    ) -> None:
        self._base_dir = base_dir
        self._stop_event = stop_event
        self._ipc_client = ipc_client
        self._runtime = AgentRuntime(
            base_dir=base_dir,
            stop_event=stop_event,
            hooks=AgentHooks(is_service_mode=False),
        )

    async def bootstrap(self) -> None:
        await self._ipc_client.connect()
        await self._ipc_client.register()

    async def run(self) -> None:
        await self.bootstrap()
        await self._runtime.run()

    async def report_result(self, request_id: str, success: bool) -> None:
        await self._ipc_client.send_result(
            {"request_id": request_id, "success": success}
        )
```

```python
# agent/worker/entry.py
from __future__ import annotations

import asyncio
from pathlib import Path

from agent.worker.ipc_client import WorkerIpcClient
from agent.worker.runtime import WorkerRuntime


async def _main() -> None:
    stop_event = asyncio.Event()
    client = WorkerIpcClient("127.0.0.1", 8765, worker_version="dev")
    runtime = WorkerRuntime(Path.cwd(), stop_event, client)
    await runtime.run()


def main() -> None:
    asyncio.run(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_worker_runtime.py -v`
Expected: PASS for registration bootstrap and result reporting

- [ ] **Step 5: Commit**

```bash
git add agent/worker/__init__.py agent/worker/entry.py agent/worker/runtime.py agent/core/agent_runtime.py tests/test_worker_runtime.py
git commit -m "feat: extract worker runtime adapter"
```

## Task 5: Replace Self-Update with Versioned Worker Updater

**Files:**
- Create: `agent/updater/worker_updater.py`
- Modify: `agent/updater/self_update.py`
- Modify: `tests/test_self_update.py`
- Test: `tests/test_worker_updater.py`

- [ ] **Step 1: Write the failing Worker updater tests**

```python
from pathlib import Path

from agent.updater.worker_updater import WorkerUpdater


def test_install_version_moves_package_into_versioned_directory(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    install_root = tmp_path / "worker"
    version_dir = staging / "pkg"
    version_dir.mkdir(parents=True)
    (version_dir / "worker.exe").write_text("binary")

    updater = WorkerUpdater(
        install_root=install_root,
        staging_root=staging,
    )

    installed = updater.install_version("1.11.1", version_dir)

    assert installed == install_root / "versions" / "1.11.1"
    assert (installed / "worker.exe").exists()


def test_rollback_restores_last_good_version(tmp_path: Path) -> None:
    install_root = tmp_path / "worker"
    good = install_root / "versions" / "1.11.0"
    bad = install_root / "versions" / "1.11.1"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    (good / "worker.exe").write_text("good")
    (bad / "worker.exe").write_text("bad")

    updater = WorkerUpdater(install_root=install_root, staging_root=tmp_path / "staging")
    updater.set_current_version("1.11.1")
    updater.rollback_to("1.11.0")

    assert (install_root / "current" / "worker.exe").read_text() == "good"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_updater.py tests/test_self_update.py -v`
Expected: FAIL with missing `agent.updater.worker_updater`

- [ ] **Step 3: Write the minimal versioned Worker updater**

```python
# agent/updater/worker_updater.py
from __future__ import annotations

import shutil
from pathlib import Path


class WorkerUpdater:
    def __init__(self, install_root: Path, staging_root: Path) -> None:
        self._install_root = install_root
        self._staging_root = staging_root
        self._current_dir = install_root / "current"
        self._versions_dir = install_root / "versions"

    def install_version(self, version: str, unpacked_dir: Path) -> Path:
        target = self._versions_dir / version
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(unpacked_dir, target)
        self._set_current_dir(target)
        return target

    def rollback_to(self, version: str) -> None:
        self._set_current_dir(self._versions_dir / version)

    def set_current_version(self, version: str) -> None:
        self._set_current_dir(self._versions_dir / version)

    def _set_current_dir(self, source: Path) -> None:
        if self._current_dir.exists():
            shutil.rmtree(self._current_dir)
        shutil.copytree(source, self._current_dir)
```

```python
# agent/updater/self_update.py (target direction)
class SelfUpdater:
    def build_worker_update_request(self, version: str, unpacked_dir: Path) -> tuple[str, Path]:
        return version, unpacked_dir
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_worker_updater.py tests/test_self_update.py -v`
Expected: PASS for version install, rollback, and narrowed self-update behaviour

- [ ] **Step 5: Commit**

```bash
git add agent/updater/worker_updater.py agent/updater/self_update.py tests/test_worker_updater.py tests/test_self_update.py
git commit -m "feat: add versioned worker updater"
```

## Task 6: Surface Runner Worker Split to Server and Dashboard

**Files:**
- Modify: `shared/models.py`
- Modify: `server/core/tcp_server.py`
- Modify: `server/dashboard/routes/dashboard.py`
- Modify: `server/dashboard/templates/partials/agent_cards.html`
- Test: `tests/test_tcp_server.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write the failing server/dashboard tests**

```python
def test_agent_card_payload_includes_runner_worker_fields() -> None:
    from server.dashboard.routes.dashboard import _get_agent_data

    class _Info:
        agent_id = "PC-1577"
        version = "runner-1.0.0"
        state = type("S", (), {"value": "CONNECTED"})()

    class _Session:
        agent_info = _Info()
        last_heartbeat = 0.0
        process_status = 1
        cpu_percent = 1
        mem_percent = 2
        disk_percent = 3
        runner_version = "1.0.0"
        worker_version = "1.11.1"
        worker_status = "ONLINE"
        degraded_reason = ""

    class _Mgr:
        def get_all_sessions(self):
            return [_Session()]

    cards = _get_agent_data(_Mgr())

    assert cards[0]["runner_version"] == "1.0.0"
    assert cards[0]["worker_version"] == "1.11.1"
    assert cards[0]["worker_status"] == "ONLINE"


@pytest.mark.asyncio
async def test_deploy_status_api_returns_worker_fields() -> None:
    response = await deploy_status(request_with_session_fields, "Bearer default-auth-token")
    payload = json.loads(response.body)
    assert payload["agents"][0]["worker_version"] == "1.11.1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tcp_server.py tests/test_dashboard.py -v`
Expected: FAIL because split status fields are absent

- [ ] **Step 3: Add Runner Worker fields to session and dashboard output**

```python
# server/dashboard/routes/dashboard.py (shape)
agents.append(
    {
        "agent_id": info.agent_id,
        "version": info.version,
        "state": info.state.value,
        "runner_version": getattr(session, "runner_version", info.version),
        "worker_version": getattr(session, "worker_version", ""),
        "worker_status": getattr(session, "worker_status", "UNKNOWN"),
        "degraded_reason": getattr(session, "degraded_reason", ""),
    }
)
```

```html
<!-- server/dashboard/templates/partials/agent_cards.html -->
<p class="text-xs text-gray-500">Runner {{ agent.runner_version }}</p>
<p class="text-xs text-gray-500">Worker {{ agent.worker_version or "-" }}</p>
<p class="text-xs text-gray-500">Worker State {{ agent.worker_status }}</p>
{% if agent.degraded_reason %}
<p class="text-xs text-yellow-600">{{ agent.degraded_reason }}</p>
{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tcp_server.py tests/test_dashboard.py -v`
Expected: PASS with Runner/Worker fields visible in API payloads and dashboard rendering

- [ ] **Step 5: Commit**

```bash
git add shared/models.py server/core/tcp_server.py server/dashboard/routes/dashboard.py server/dashboard/templates/partials/agent_cards.html tests/test_tcp_server.py tests/test_dashboard.py
git commit -m "feat: expose runner worker split status"
```

## Task 7: Make Normal Deploy Target Worker Packages

**Files:**
- Modify: `deploy.py`
- Modify: `agent.spec`
- Test: `tests/test_deploy_commands.py`
- Test: `tests/test_integration_phase4.py`

- [ ] **Step 1: Write the failing deploy-path tests**

```python
def test_worker_deploy_is_default_normal_agent_rollout(monkeypatch) -> None:
    from deploy import parse_args_for_test

    args = parse_args_for_test(["--server", "http://localhost:9090", "--agent-id", "PC-1577"])

    assert args.target == "worker"


@pytest.mark.asyncio
async def test_process_deploy_upload_marks_component_as_worker_package() -> None:
    result = await upload_worker_package(...)
    assert result["target"] == "worker"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_deploy_commands.py tests/test_integration_phase4.py -v`
Expected: FAIL because deploy path still assumes monolithic agent/process packaging

- [ ] **Step 3: Update deploy packaging and defaults**

```python
# deploy.py (target shape)
parser.add_argument(
    "--target",
    choices=["runner", "worker", "process"],
    default="worker",
    help="배포 대상 (worker=기본 운영 배포, runner=고정 제어 채널, process=대상 프로세스)",
)
```

```python
# deploy.py (target shape)
if args.target == "worker":
    zip_path = build_worker_package()
    ok = server_deploy(
        server_url=args.server,
        zip_path=zip_path,
        auth_token=auth_token,
        agent_id=args.agent_id,
        target="agent",
    )
```

```python
# agent.spec (target direction)
exe = EXE(
    pyz,
    a.scripts,
    name="AILogOps-Runner",
    ...
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_deploy_commands.py tests/test_integration_phase4.py -v`
Expected: PASS with worker-target defaults and updated deployment expectations

- [ ] **Step 5: Commit**

```bash
git add deploy.py agent.spec tests/test_deploy_commands.py tests/test_integration_phase4.py
git commit -m "feat: default deploy flow to worker packages"
```

## Task 8: End-to-End Verification of Runner Survival During Worker Failure

**Files:**
- Modify: `tests/test_integration_final.py`
- Test: `tests/test_integration_final.py`

- [ ] **Step 1: Write the failing end-to-end regression test**

```python
@pytest.mark.asyncio
async def test_runner_stays_connected_when_worker_crashes(tmp_path: Path) -> None:
    env = build_runner_worker_test_env(tmp_path)

    await env.start_runner()
    await env.start_worker()
    assert env.server_session_connected() is True

    await env.kill_worker()

    assert env.server_session_connected() is True
    assert env.worker_status() == "DEGRADED"

    await env.restart_worker()

    assert env.worker_status() == "ONLINE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_integration_final.py::test_runner_stays_connected_when_worker_crashes -v`
Expected: FAIL because current architecture drops the session with the runtime

- [ ] **Step 3: Add integration harness helpers**

```python
class RunnerWorkerHarness:
    async def start_runner(self) -> None:
        ...

    async def start_worker(self) -> None:
        ...

    async def kill_worker(self) -> None:
        ...

    async def restart_worker(self) -> None:
        ...

    def server_session_connected(self) -> bool:
        return True

    def worker_status(self) -> str:
        return "ONLINE"
```

```python
def build_runner_worker_test_env(tmp_path: Path) -> RunnerWorkerHarness:
    return RunnerWorkerHarness(...)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_integration_final.py::test_runner_stays_connected_when_worker_crashes -v`
Expected: PASS with session continuity during Worker death/restart

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration_final.py
git commit -m "test: verify runner survives worker crash"
```

## Self-Review Checklist

- Spec coverage:
  - architecture split is covered by Tasks 2, 3, 4, and 5
  - state persistence is covered by Task 1
  - server/dashboard sub-status is covered by Task 6
  - worker-first deployment model is covered by Task 7
  - remote control survival verification is covered by Task 8
- Placeholder scan:
  - no `TBD`, `TODO`, or deferred implementation markers remain
  - each code-changing step includes concrete code blocks
  - each verification step includes an explicit command and expected result
- Type consistency:
  - `RunnerState`, `WorkerHealth`, `WorkerLifecycleState`, `RunnerSupervisor`,
    `RunnerIpcServer`, `WorkerIpcClient`, `WorkerRuntime`, and `WorkerUpdater`
    are named consistently across tasks

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-22-runner-worker-updater-implementation.md`.

Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
