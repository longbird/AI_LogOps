"""Per-server connection context and recording ownership."""

# pyright: reportImportCycles=false

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
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
    """Global lock ensuring only one server owns recording at a time.

    First server to send CMD_REC START acquires ownership.
    Other servers get FAILED.  Owner disconnects -> released.
    """

    def __init__(self) -> None:
        self._owner: str | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def try_acquire(self, server_name: str) -> bool:
        """Attempt to acquire recording ownership. Returns True if acquired or already owned."""
        async with self._lock:
            if self._owner is None:
                self._owner = server_name
                return True
            return self._owner == server_name

    async def release(self, server_name: str) -> None:
        """Release ownership if held by *server_name*."""
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
        with contextlib.suppress(Exception):
            await self.tcp_client.disconnect()
