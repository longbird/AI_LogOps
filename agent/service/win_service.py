from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from agent.core.deploy_handler import DeployHandler
from agent.core.log_watcher import LogWatcher
from agent.core.process_mgr import ProcessManager
from agent.core.tcp_client import TCPClient
from agent.telegram.poller import AgentTelegramPoller
from shared.utils import load_yaml_config, setup_logging

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError as exc:  # pragma: no cover - exercised via stubs in tests
    raise RuntimeError("pywin32 is required to run Windows service mode") from exc


class AILogOpsAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "AILogOps-Agent"
    _svc_display_name_ = "AI-LogOps Agent Service"
    _svc_description_ = "AI-LogOps 원격 로그 분석 및 자동 배포 에이전트"
    _svc_start_type_ = win32service.SERVICE_AUTO_START

    def __init__(self, args: list[str]):
        super().__init__(args)
        self._logger = setup_logging(self.__class__.__name__)
        self._stop_handle = win32event.CreateEvent(None, False, False, None)
        self._stop_requested = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._stop_requested.set()
        win32event.SetEvent(self._stop_handle)

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)

    def SvcDoRun(self) -> None:
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_agent())
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()
            asyncio.set_event_loop(None)

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )

    async def _run_agent(self) -> None:
        config = _load_agent_config()
        logger = self._logger

        agent_cfg = _as_mapping(config.get("agent"))
        telegram_cfg = _as_mapping(config.get("telegram"))
        monitoring_cfg = _as_mapping(config.get("monitoring"))
        process_cfg = _as_mapping(config.get("target_process"))
        connection_cfg = _as_mapping(config.get("connection"))

        tcp_client = TCPClient(
            agent_id=str(agent_cfg.get("id", "agent-unknown")),
            version=str(agent_cfg.get("version", "0.0.0")),
            token=str(connection_cfg.get("token", "")),
            host=str(connection_cfg.get("host", "127.0.0.1")),
            port=int(connection_cfg.get("port", 9500)),
            heartbeat_interval=int(connection_cfg.get("heartbeat_interval", 30)),
            reconnect_attempts=int(connection_cfg.get("reconnect_attempts", 5)),
            reconnect_delay=int(connection_cfg.get("reconnect_delay", 10)),
        )
        process_mgr = ProcessManager(
            process_name=str(process_cfg.get("name", "")),
            process_path=str(process_cfg.get("path", "")),
            backup_dir=str(process_cfg.get("backup_dir", "./backups")),
        )
        deploy_handler = DeployHandler(
            tcp_client=tcp_client,
            process_mgr=process_mgr,
            transfer_dir=str(
                monitoring_cfg.get("transfer_dir", "agent/storage/transfers")
            ),
        )
        watcher = LogWatcher(
            watch_dirs=[str(x) for x in monitoring_cfg.get("log_folders", [])],
            extensions=[
                str(x) for x in monitoring_cfg.get("watch_extensions", [".log"])
            ],
            on_new_line=self._build_log_sender(tcp_client),
        )
        poller = AgentTelegramPoller(
            bot_token=str(telegram_cfg.get("bot_token", "")),
            admin_chat_id=int(telegram_cfg.get("admin_chat_id", 0)),
        )

        tcp_client.on_cmd_deploy = deploy_handler.handle_cmd_deploy
        tcp_client.on_file_chunk = deploy_handler.handle_file_chunk

        poller.on_connect = self._build_connect_handler(
            tcp_client=tcp_client,
            watcher=watcher,
            history_max_mb=int(monitoring_cfg.get("history_max_mb", 10)),
        )
        poller.on_disconnect = self._build_disconnect_handler(tcp_client)

        await watcher.start()
        await poller.start()

        try:
            await self._heartbeat_loop(tcp_client)
        finally:
            with contextlib.suppress(Exception):
                await poller.stop()
            with contextlib.suppress(Exception):
                await watcher.stop()
            with contextlib.suppress(Exception):
                await tcp_client.disconnect()
            logger.info("agent service loop ended")

    def _build_log_sender(self, tcp_client: TCPClient):
        async def _send_log(filename: str, line: str) -> None:
            if not tcp_client.is_connected:
                return
            with contextlib.suppress(ConnectionError, OSError):
                await tcp_client.send_log_line(filename, line)

        return _send_log

    def _build_connect_handler(
        self,
        tcp_client: TCPClient,
        watcher: LogWatcher,
        history_max_mb: int,
    ):
        async def _connect(ip: str, port: int) -> None:
            tcp_client.host = ip
            tcp_client.port = port
            connected = await tcp_client.connect()
            if not connected:
                self._logger.warning("connect command failed: %s:%s", ip, port)
                return

            for filepath in watcher.get_watchable_files():
                data = watcher.read_history(filepath, max_mb=history_max_mb)
                filename = Path(filepath).name
                with contextlib.suppress(ConnectionError, OSError):
                    await tcp_client.send_log_history(filename, data)

        return _connect

    def _build_disconnect_handler(self, tcp_client: TCPClient):
        async def _disconnect() -> None:
            await tcp_client.disconnect()

        return _disconnect

    async def _heartbeat_loop(self, tcp_client: TCPClient) -> None:
        interval = max(1, tcp_client.heartbeat_interval)
        next_heartbeat = 0.0
        loop = asyncio.get_running_loop()

        while not self._stop_requested.is_set():
            wait_result = await asyncio.to_thread(
                win32event.WaitForSingleObject,
                self._stop_handle,
                1000,
            )
            if wait_result == 0:
                break

            now = loop.time()
            if now < next_heartbeat:
                continue
            next_heartbeat = now + interval

            if tcp_client.is_connected:
                with contextlib.suppress(ConnectionError, OSError):
                    await tcp_client.send_heartbeat()


def _as_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _load_agent_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    return load_yaml_config(str(config_path))


def configure_failure_actions(service_name: str) -> None:
    command = [
        "sc",
        "failure",
        service_name,
        "reset=",
        "86400",
        "actions=",
        "restart/60000/restart/60000/restart/60000",
    ]
    logger = setup_logging("win_service")
    try:
        _ = subprocess.run(command, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        logger.warning("failed to configure recovery options: %s", exc)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv if argv is None else argv
    win32serviceutil.HandleCommandLine(AILogOpsAgentService)
    if len(args) > 1 and args[1].lower() == "install":
        configure_failure_actions(AILogOpsAgentService._svc_name_)


if __name__ == "__main__":
    main()
