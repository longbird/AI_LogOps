# Multi-Server Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable the agent to connect to multiple TCP servers simultaneously, with shared components (LogWatcher, ProcessManager, etc.) and per-server transport/handlers.

**Architecture:** Each server gets its own `TCPClient` + per-server handlers (`LogCmdHandler`, `DeployHandler`, `RecordingController`). Shared components remain singleton. Recording ownership and deploy execution are globally exclusive via locks. Config is backward-compatible (`connection:` → single server; `servers:` → multi).

**Tech Stack:** Python 3.13, asyncio, dataclasses

---

## Design Summary

### Per-Server (one instance per connection)
- `TCPClient` — separate TCP socket per server
- `LogCmdHandler` — each server independently controls log streaming
- `DeployHandler` — each server can deploy (execution is globally locked)
- `RecordingController` — per-server, with shared `RecordingOwnership` lock
- Heartbeat/reconnect loop — separate `asyncio.Task` per server

### Shared (singleton)
- `LogWatcher`, `ProcessManager`, `LLMRouter`, `TelegramPoller`
- `SystemMonitor`, `SelfUpdater`, `ProcessDeployer`
- `ProcessMonitorLoop`, `ProcessScheduler`
- `RecordingOwnership` lock, `_deploy_lock` (asyncio.Lock)

### Config Format (backward compatible)
```yaml
# New format: multiple servers
servers:
  - name: "main"
    host: "61.42.53.61"
    port: 9500
    token: "auth-token"
    heartbeat_interval: 30
    reconnect_delay: 60
  - name: "rec"
    host: "49.247.46.86"
    port: 9500
    token: "auth-token"

# Old format: still works (becomes single server named "default")
connection:
  host: "127.0.0.1"
  port: 9500
  token: "default-auth-token"
```

### Recording Ownership Rules
- First server to send `CMD_REC START` acquires ownership
- Other servers' START → `FAILED`
- Owner disconnects → ownership released, watcher stops
- Non-ownership operations (STT save, data queries) work for any server

### Connection Status Hook
- `on_connection_change(bool)` fires `True` when any server connects, `False` when ALL disconnect

---

## Task 1: ServerConfig, ServerConnection, RecordingOwnership

**Files:**
- Create: `agent/core/server_connection.py`

### Implementation

```python
"""Per-server connection context and recording ownership."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.deploy_handler import DeployHandler
    from agent.core.log_cmd_handler import LogCmdHandler
    from agent.core.tcp_client import TCPClient
    from agent.recording.controller import RecordingController


@dataclass
class ServerConfig:
    """Parsed config for one server connection."""
    name: str
    host: str
    port: int
    token: str
    heartbeat_interval: int = 30
    reconnect_attempts: int = 5
    reconnect_delay: int = 60


class RecordingOwnership:
    """Global lock ensuring only one server owns recording at a time."""

    def __init__(self) -> None:
        self._owner: str | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def try_acquire(self, server_name: str) -> bool:
        async with self._lock:
            if self._owner is None:
                self._owner = server_name
                return True
            return self._owner == server_name  # already owns

    async def release(self, server_name: str) -> None:
        async with self._lock:
            if self._owner == server_name:
                self._owner = None

    @property
    def owner(self) -> str | None:
        return self._owner


class ServerConnection:
    """Per-server connection context: transport + handlers."""

    def __init__(
        self,
        config: ServerConfig,
        tcp_client: TCPClient,
        log_cmd_handler: LogCmdHandler,
        deploy_handler: DeployHandler,
        rec_controller: RecordingController,
    ) -> None:
        self.config = config
        self.tcp_client = tcp_client
        self.log_cmd_handler = log_cmd_handler
        self.deploy_handler = deploy_handler
        self.rec_controller = rec_controller

    async def cleanup(self) -> None:
        """Release all per-server resources."""
        await self.rec_controller.cleanup()
        import contextlib
        with contextlib.suppress(Exception):
            await self.tcp_client.disconnect()
```

---

## Task 2: RecordingController — Add Ownership Support

**Files:**
- Modify: `agent/recording/controller.py`

### Changes

1. Add `server_name: str` and `ownership: RecordingOwnership | None` to `__init__`
2. In `_handle_cmd_rec_start`: check ownership before starting watcher
3. In `on_connection_lost`: release ownership
4. In `cleanup`: release ownership

Key changes:
```python
# __init__ additions:
self._server_name: str = server_name
self._ownership: RecordingOwnership | None = ownership

# _handle_cmd_rec_start: add ownership check before existing logic
if self._ownership is not None:
    acquired = await self._ownership.try_acquire(self._server_name)
    if not acquired:
        self._logger.info(
            "recording owned by %s, rejecting START from %s",
            self._ownership.owner, self._server_name,
        )
        ack = CmdRecAckPayload(action=RecAction.START, status=RecAckStatus.FAILED)
        await self._tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())
        return

# on_connection_lost: add ownership release
async def on_connection_lost(self) -> None:
    await self._stop_watcher()
    if self._ownership is not None:
        await self._ownership.release(self._server_name)

# cleanup: add ownership release
async def cleanup(self) -> None:
    await self._stop_watcher()
    if self._ownership is not None:
        await self._ownership.release(self._server_name)
```

---

## Task 3: AgentRuntime — Multi-Server Lifecycle Refactor

**Files:**
- Modify: `agent/core/agent_runtime.py`

This is the largest change. The `run()` method must be restructured.

### Key Changes

1. **Add `_parse_server_configs(cfg)`** — parse `servers:` or `connection:` into `list[ServerConfig]`
2. **Add `_create_server_connection(srv_cfg, shared_deps)`** — create per-server TCPClient + handlers
3. **Add `_server_heartbeat_loop(conn, on_lost)`** — per-server heartbeat as asyncio task
4. **Refactor `run()`** — create connections, start per-server heartbeat tasks, wait for stop_event
5. **Add `connections` property** — expose list of ServerConnections
6. **Add `_deploy_lock`** — shared asyncio.Lock for deploy execution exclusivity
7. **Change `_send_log`** — send to ALL connected servers with realtime active (no head-of-line blocking)
8. **Change connection hooks** — `on_connection_change` becomes aggregate (any_connected)
9. **TelegramPoller** — don't wire on_connect/on_disconnect when multi-server

### `_parse_server_configs` logic
```python
def _parse_server_configs(self, cfg: ConfigView) -> list[ServerConfig]:
    servers_raw = cfg.sub("servers").raw()
    if servers_raw:
        # New format: list of server dicts
        servers_list = cfg.raw().get("servers", [])
        result = []
        for entry in servers_list:
            if isinstance(entry, dict):
                result.append(ServerConfig(
                    name=str(entry.get("name", f"server-{len(result)}")),
                    host=str(entry.get("host", "127.0.0.1")),
                    port=int(entry.get("port", 9500)),
                    token=str(entry.get("token", "")),
                    heartbeat_interval=int(entry.get("heartbeat_interval", 30)),
                    reconnect_attempts=int(entry.get("reconnect_attempts", 5)),
                    reconnect_delay=int(entry.get("reconnect_delay", 60)),
                ))
        return result

    # Fallback: old connection format → single server
    conn_cfg = cfg.sub("connection")
    host = conn_cfg.s("host", "")
    if not host:
        return []
    return [ServerConfig(
        name="default",
        host=host,
        port=conn_cfg.i("port", 9500),
        token=conn_cfg.s("token", ""),
        heartbeat_interval=conn_cfg.i("heartbeat_interval", 30),
        reconnect_attempts=conn_cfg.i("reconnect_attempts", 5),
        reconnect_delay=conn_cfg.i("reconnect_delay", 60),
    )]
```

### `run()` restructure
```
1. Instance lock
2. Load .env + config
3. Parse server configs
4. Create shared components (ProcessManager, LogWatcher, LLM, Telegram, etc.)
5. Create RecordingOwnership (shared) + deploy_lock (shared)
6. For each ServerConfig:
   a. Create TCPClient
   b. Create LogCmdHandler (with shared LogWatcher)
   c. Create DeployHandler (with shared ProcessManager, deploy_lock)
   d. Create RecordingController (with shared RecordingOwnership)
   e. Wire callbacks on TCPClient
   f. Create ServerConnection
7. Expose self.tcp_client = first connection's client (backward compat)
8. Expose self.connections = all connections
9. Wire _send_log to fan-out to all connected servers
10. Start shared components (watcher, poller, monitor, scheduler)
11. Start per-server heartbeat tasks
12. hooks.on_status("실행 중")
13. await self._stop_event.wait()
14. Cancel heartbeat tasks
15. Cleanup per-server connections
16. Cleanup shared components
17. Release instance lock
```

### `_server_heartbeat_loop(conn)` — extracted from current `_heartbeat_loop`
Same logic as current, but operates on `conn.tcp_client` and `conn.config`.
When connection lost: call per-server `_on_connection_lost(conn)` and update aggregate status.

### `_send_log` fan-out
```python
async def _send_log(filename: str, line: str) -> None:
    tasks = []
    for conn in connections:
        if conn.tcp_client.is_connected and conn.log_cmd_handler.is_realtime_active:
            tasks.append(conn.tcp_client.send_log_line(filename, line))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    self._hooks.on_log_sent()
```

### Aggregate connection status
```python
def _update_aggregate_connection(self) -> None:
    any_connected = any(c.tcp_client.is_connected for c in self._connections)
    self._hooks.on_connection_change(any_connected)
```

---

## Task 4: GUI — Force-Reconnect All Servers

**Files:**
- Modify: `agent/gui/app.py`

### Changes

`_force_reconnect` currently uses `self._runtime.tcp_client`. Change to iterate all connections:

```python
async def _do_reconnect() -> None:
    runtime = self._runtime
    if runtime is None:
        return
    for conn in runtime.connections:
        try:
            await conn.tcp_client.disconnect()
            connected = await conn.tcp_client.connect()
            if connected:
                logger.info("reconnected: %s", conn.config.name)
        except Exception:
            logger.exception("reconnect error: %s", conn.config.name)
```

---

## Task 5: config.yaml — Add Servers List Example

**Files:**
- Modify: `agent/config.yaml`

Add commented-out `servers:` section above or below `connection:` to show the new format:

```yaml
# 멀티 서버 연결 (servers 설정 시 connection 섹션 무시)
# servers:
#   - name: "main"
#     host: "61.42.53.61"
#     port: 9500
#     token: "auth-token"
#     heartbeat_interval: 30
#     reconnect_delay: 60
#   - name: "rec"
#     host: "49.247.46.86"
#     port: 9500
#     token: "auth-token"
```

---

## Task 6: Final Verification + Commit

1. `lsp_diagnostics` on all changed files (zero errors)
2. Verify backward compatibility: existing `connection:` config still works
3. Commit with message: `feat: add multi-server connection support`
