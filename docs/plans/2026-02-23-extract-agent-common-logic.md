# Agent Common Logic Extraction + Multi-Server Preparation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract ~600 lines of duplicated logic from `win_service.py` and `gui/app.py` into a shared `AgentRuntime` orchestrator, then prepare for multi-server connections.

**Architecture:** Composition over inheritance. `AgentRuntime` is the orchestrator that both Service and GUI modes instantiate with mode-specific hooks (callables). Recording handlers move to a dedicated `RecordingController` class. Config access unifies under `ConfigView`. A per-connection `ConnectionContext` dataclass prepares for multi-server.

**Tech Stack:** Python 3.13, asyncio, win32event (service only), tkinter (GUI only)

---

## File Overview

| File | Action | Description |
|------|--------|-------------|
| `agent/core/config_view.py` | CREATE | Typed config accessor wrapping dict |
| `agent/recording/controller.py` | CREATE | Recording command handlers (was closures) |
| `agent/core/agent_runtime.py` | CREATE | Main orchestrator (~400 lines) |
| `agent/service/win_service.py` | MODIFY | Thin wrapper using AgentRuntime |
| `agent/gui/app.py` | MODIFY | Thin wrapper using AgentRuntime |

---

### Task 1: Create ConfigView (`agent/core/config_view.py`)

**Files:**
- Create: `agent/core/config_view.py`

**Why:** Both files have duplicate config helper functions (`_as_mapping`/`_to_str`/`_to_int`/`_to_bool`/`_to_list_str` in win_service.py vs `_m`/`_s`/`_i`/`_b`/`_ls` in gui/app.py). Unify into one class.

**Step 1: Create ConfigView class**

```python
"""Typed configuration accessor wrapping a dict/Mapping."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any


class ConfigView:
    """Wraps a raw config dict with typed accessors."""

    __slots__ = ("_data",)

    def __init__(self, data: Any = None) -> None:
        if data is None:
            self._data: Mapping[str, Any] = {}
        elif isinstance(data, Mapping):
            self._data = data
        else:
            self._data = {}

    def sub(self, key: str) -> ConfigView:
        """Get a sub-section as ConfigView."""
        return ConfigView(self._data.get(key))

    def raw(self) -> Mapping[str, Any]:
        return self._data

    def s(self, key: str, default: str = "") -> str:
        v = self._data.get(key)
        if v is None:
            return default
        return str(v).strip() if not isinstance(v, str) else v

    def i(self, key: str, default: int = 0) -> int:
        v = self._data.get(key)
        if v is None:
            return default
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    def b(self, key: str, default: bool = False) -> bool:
        v = self._data.get(key)
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)

    def ls(self, key: str, default: list[str] | None = None) -> list[str]:
        v = self._data.get(key)
        if v is None:
            return default or []
        if isinstance(v, str):
            return [v] if v.strip() else []
        items = []
        for item in v:
            text = str(item).strip()
            if text:
                items.append(text)
        return items
```

**Step 2: Commit**

```bash
git add agent/core/config_view.py
git commit -m "feat: add ConfigView typed config accessor"
```

---

### Task 2: Create RecordingController (`agent/recording/controller.py`)

**Files:**
- Create: `agent/recording/controller.py`

**Why:** Recording handlers (_handle_cmd_rec, _handle_rec_upload_req, _handle_stt_result, _handle_rec_data_req) are ~315 lines duplicated as closures in both files. Extract into a class with proper state management.

**Key design:**
- Owns `_rec_watcher` and `_rec_watcher_task` as instance attributes (was `nonlocal` closures)
- Takes `tcp_client`, `recording_cfg` (ConfigView), and `logger` in constructor
- Methods: `handle_cmd_rec`, `handle_rec_upload_req`, `handle_stt_result`, `handle_rec_data_req`, `on_connection_lost`, `cleanup`
- RecordingUploader created lazily on first upload request

**Step 1: Create RecordingController class**

Extract the 4 handlers + connection_lost + cleanup from win_service.py lines 349-697 into methods. Replace `nonlocal` with `self._rec_watcher` / `self._rec_watcher_task`. Replace `_to_str()` etc. with `self._cfg.s()` etc.

**Step 2: Commit**

```bash
git add agent/recording/controller.py
git commit -m "feat: add RecordingController for recording command handlers"
```

---

### Task 3: Create AgentRuntime (`agent/core/agent_runtime.py`)

**Files:**
- Create: `agent/core/agent_runtime.py`

**Why:** The main orchestrator replacing the duplicated `_run_agent()` / `_run_agent_async()` methods (~400 lines each). Both Service and GUI create an `AgentRuntime` instance and call `run()`.

**Key design:**

```python
@dataclass
class AgentHooks:
    """Mode-specific callbacks. GUI implements these; Service uses defaults."""
    on_status: Callable[[str], None] = lambda s: None
    on_connection_change: Callable[[bool], None] = lambda c: None
    on_log_sent: Callable[[], None] = lambda: None
    on_duplicate_instance: Callable[[str], None] = lambda m: None
    on_provider_ready: Callable[[str], None] = lambda p: None
    is_service_mode: bool = False

class AgentRuntime:
    def __init__(self, base_dir: Path, stop_event: asyncio.Event, hooks: AgentHooks):
        ...

    async def run(self) -> None:
        """Load config -> create components -> register callbacks -> run loop -> cleanup."""
        ...
```

**The `run()` method consolidates:**
1. Instance lock acquisition
2. Config loading (via ConfigView)
3. Component creation (TCPClient, ProcessManager, DeployHandler, LogWatcher, etc.)
4. LLM Router + Subscription setup
5. LogCmdHandler + callback registration
6. RecordingController creation + callback registration
7. Monitor/Scheduler start
8. Auto-connect + heartbeat/reconnect loop
9. Cleanup in finally block

**Stop mechanism:**
- `stop_event: asyncio.Event` passed in constructor
- Service: separate thread does `WaitForSingleObject` then sets stop_event
- GUI: `_quit_app` sets stop_event
- Heartbeat loop: `while not stop_event.is_set(): await asyncio.sleep(1)`

**Step 1: Create AgentRuntime with AgentHooks**

Migrate the shared logic from win_service.py `_run_agent()` lines 103-739 into `AgentRuntime.run()`. Use ConfigView for config access. Delegate recording to RecordingController.

**Step 2: Commit**

```bash
git add agent/core/agent_runtime.py
git commit -m "feat: add AgentRuntime orchestrator for shared agent logic"
```

---

### Task 4: Adapt win_service.py to use AgentRuntime

**Files:**
- Modify: `agent/service/win_service.py`

**What changes:**
- `_run_agent()` reduces to ~30 lines: create stop_event, create AgentRuntime, call runtime.run()
- Service-specific: bridge `self._stop_handle` (win32event) to `stop_event` via `asyncio.to_thread`
- Remove: all duplicated component creation, recording handlers, heartbeat loop, cleanup
- Remove: `_build_log_sender`, `_build_connect_handler`, `_build_disconnect_handler`, `_heartbeat_loop`, `_process_watch_loop`
- Remove: module-level helpers (`_as_mapping`, `_to_str`, `_to_int`, `_to_bool`, `_to_list_str`)
- Keep: `SvcDoRun`, `SvcStop`, service configuration, Windows-specific code

**Expected reduction:** ~1032 lines -> ~200 lines

**Step 1: Replace _run_agent with AgentRuntime usage**

```python
async def _run_agent(self) -> None:
    stop_event = asyncio.Event()

    # Bridge win32event -> asyncio.Event
    async def _wait_for_service_stop():
        while not self._stop_requested.is_set():
            result = await asyncio.to_thread(
                win32event.WaitForSingleObject,
                self._stop_handle,
                1000,
            )
            if result == 0:
                stop_event.set()
                return

    hooks = AgentHooks(is_service_mode=True)
    runtime = AgentRuntime(
        base_dir=_get_base_dir(),
        stop_event=stop_event,
        hooks=hooks,
    )

    stop_task = asyncio.create_task(_wait_for_service_stop())
    try:
        await runtime.run()
    finally:
        stop_task.cancel()
```

**Step 2: Remove dead code**

Remove all module-level helpers and extracted methods.

**Step 3: Verify no regressions**

Run lsp_diagnostics on modified files.

**Step 4: Commit**

```bash
git add agent/service/win_service.py
git commit -m "refactor: use AgentRuntime in Windows service"
```

---

### Task 5: Adapt gui/app.py to use AgentRuntime

**Files:**
- Modify: `agent/gui/app.py`

**What changes:**
- `_run_agent_async()` reduces to ~40 lines: create hooks with GUI callbacks, create AgentRuntime, call runtime.run()
- GUI hooks: wrap `self._root.after(0, ...)` calls in hook lambdas
- Remove: all duplicated component creation, recording handlers, heartbeat loop, cleanup
- Remove: module-level helpers (`_m`, `_s`, `_i`, `_b`, `_ls`)
- Keep: all tkinter UI code, tray icon, log viewer, thread management

**Expected reduction:** ~1181 lines -> ~400 lines

**Step 1: Create GUI hooks**

```python
hooks = AgentHooks(
    on_status=lambda s: self._root.after(0, lambda: self._status_var.set(s)),
    on_connection_change=lambda c: self._root.after(0, lambda: ...),
    on_log_sent=lambda: self._root.after(0, lambda: self._log_count_var.set(
        str(int(self._log_count_var.get()) + 1)
    )),
    on_duplicate_instance=lambda m: self._root.after(0, lambda: self._status_var.set(m)),
    on_provider_ready=lambda p: self._root.after(0, lambda: self._provider_var.set(p)),
    is_service_mode=False,
)
```

**Step 2: Replace _run_agent_async with AgentRuntime usage**

**Step 3: Remove dead code**

**Step 4: Verify no regressions**

**Step 5: Commit**

```bash
git add agent/gui/app.py
git commit -m "refactor: use AgentRuntime in agent GUI"
```

---

### Task 6: Final verification + cleanup

**Steps:**
1. Run lsp_diagnostics on all changed files
2. Verify agent can start in both modes (service + GUI config)
3. Check that RecordingController handles all 4 commands correctly
4. Verify connection loss cleanup works
5. Remove any remaining dead code
6. Commit final cleanup

---

## Multi-Server Preparation (Future)

The `AgentRuntime` design already supports multi-server via:
- `ConnectionContext` per server: `{client: TCPClient, rec_controller: RecordingController, ...}`
- `self.connections: dict[str, ConnectionContext]`
- Recording ownership: `_rec_owner: str | None` in RecordingController

When implementing multi-server:
1. Parse `config.yaml` `servers:` list (backward compatible with `connection:` single)
2. Create N `ConnectionContext` instances
3. Each context has its own TCPClient + RecordingController
4. Recording ownership: first server to START owns; others get FAILED
5. Heartbeat loop iterates all connections
